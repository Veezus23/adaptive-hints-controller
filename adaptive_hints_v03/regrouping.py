"""Content-specific base-ten adapter; the shared engine contains no mathematics."""
from __future__ import annotations
from dataclasses import asdict, is_dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import re

from . import ROOT
from .contracts import ActionCandidate, TaskContract, ValidationResult, EvidenceLink, TaskFinding
from .validators import (VERSION, COUNT, read_number, exchange, total_value,
                         parse_response, validate_fields, is_departure)

CONTENT = json.loads((ROOT / "content/regrouping_bank_v1.json").read_text(encoding="utf-8"))
RULES = json.loads((ROOT / "content/rules_v1.json").read_text(encoding="utf-8"))
FORBIDDEN = {"human_annotations", "raw_human_annotations", "original_tutor_responses", "observed_original_tutor_responses", "error_label", "error_label_raw", "c_revision", "references"}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _source(source):
    if is_dataclass(source):
        source = asdict(source)
    if isinstance(source, str):
        source = {"record_id": "supplied-context-" + sha256(source.encode()).hexdigest()[:12], "task_text": source}
    if not isinstance(source, dict) or FORBIDDEN.intersection(source):
        raise ValueError("Context requires observation-only source, never references or human labels")
    if "task_text" in source:
        messages = [{"role": "context_supplier", "text": source["task_text"], "message_id": None}]
    else:
        messages = source.get("observed_context", [])
    normalized = []
    for message in messages:
        if is_dataclass(message): message = asdict(message)
        if not isinstance(message, dict) or FORBIDDEN.intersection(message):
            raise ValueError("Invalid context message")
        text = message.get("text")
        if not isinstance(text, str): raise ValueError("Verbatim context text is required")
        normalized.append({"role": message.get("role", message.get("user")), "text": text,
                           "message_id": message.get("message_id", message.get("id"))})
    if not normalized: raise ValueError("At least one actual source message is required")
    return source, normalized


def _span(message, index):
    text = message["text"]
    return {"context_index": index, "message_id": message["message_id"], "role": message["role"],
            "start": 0, "end": len(text), "text": text, "text_sha256": sha256(text.encode()).hexdigest()}


def mathematical_content_check():
    checked = 0
    for n in range(100, 1000):
        after = exchange(n)
        assert total_value(after) == n
        assert after["hundreds"] == n // 100 - 1 and after["tens"] == n // 10 % 10 + 10
        checked += 1
    assert 100 == 10 * 10
    return {"status": "mathematically_checked_authored_content", "validator": VERSION,
            "supported_number_checks": checked, "provenance": "Executed deterministic base-ten invariant checks; not completed human or pedagogical review.",
            "content_sha256": sha256((ROOT / "content/regrouping_bank_v1.json").read_bytes()).hexdigest()}


class RegroupingAdapter:
    hypothesis_ids = RULES["hypotheses"]
    prerequisite_order = RULES["prerequisite_order"]
    fixed_instruction_id = "H_EQ"
    operator_categories = {
        "structured_full_exchange": "Parse explicit hundreds/tens/ones counts and the stated reasoning.",
        "structured_unit_count_value": "Parse separately stated count of tens and value in ones.",
        "structured_equivalence": "Parse an explicit count of tens equal to one hundred.",
        "structured_tens_count": "Parse an explicit combined count of tens.",
        "alternate_wording_interpretation": "Preserve an explicitly described alternate reading; no mathematical diagnosis.",
        "unparseable": "The visible response remains uninterpretable under this contract.",
        "acknowledgement": "An acknowledgement does not establish understanding or failure.",
    }

    def __init__(self):
        self.content_validation = mathematical_content_check()

    def assess_context(self, source):
        original, messages = _source(source)
        eligible = [(i, m) for i, m in enumerate(messages) if m["role"] in {"tutor", "teacher", "context_supplier"}]
        if not eligible: eligible = list(enumerate(messages[:-1] or messages))
        # Prefer a substantive recorded question over a later neutral offer of help.
        relevant = [(i, m) for i, m in eligible if re.search(r"\b(?:borrow|exchange|regroup|hundreds?|formula|times|subtract|figure\s+\d|diagram|chart|round\w*|tens|place value|factor\w*|binomial\w*|constant term|division|sum|difference|product|length|area|angle|sides|symmetry|shape|clock|pictograph|image|quadrilateral|centimeters|baseballs|cookies)\b|\d\s*[-+*/=×]\s*\d", m["text"], re.I)]
        i, message = (relevant or eligible)[-1]
        text, low = message["text"], message["text"].lower()
        number_matches = re.findall(r"(?<![\d.,])([1-9]\d{2})(?![\d.,])", text)
        numbers = list(dict.fromkeys(map(int, number_matches)))
        # Explicit before counts can establish N without reconstructing a task.
        before = re.search(r"(?:start with|starts with)\s+(" + COUNT + r")\s+hundreds?\s*[, ]+(" + COUNT + r")\s+tens?\s*(?:,|and)?\s*(" + COUNT + r")\s+ones?", low)
        if before:
            h, t, u = map(read_number, before.groups())
            if 1 <= h <= 9 and 0 <= t <= 9 and 0 <= u <= 9: numbers = [100 * h + 10 * t + u]
        if not numbers:
            words_number = re.search(r"\b(?:in|represent|number)\s+((?:one|two|three|four|five|six|seven|eight|nine) hundred(?: and)?(?: (?:" + "|".join(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]) + r")){0,2})\b", low)
            if words_number: numbers = [read_number(words_number[1])]
        number = numbers[0] if len(numbers) == 1 else None
        direction = bool(re.search(r"(?:exchange|exchanging|trade|trading|regroup|regrouping)\s+(?:one|1)\s+hundred\s+(?:for|as|into)\s+(?:(?:ten|10)\s+)?tens", low))
        borrowed = bool(re.search(r"borrow(?:ing)?\s+from\s+the\s+hundreds(?:\s+column)?", low))
        unit_probe = bool(re.search(r"tens digit.*(?:represent|value)", low))
        eq_probe = bool(re.search(r"how many tens.*(?:one|1) hundred", low))
        visual = bool(re.search(r"\b(?:figure\s+(?:\d|above|below)|diagram|image|picture|chart|pictograph)\b|\b(?:board|screen)\s+(?:above|below)\b|(?:time.*on the clock|this shape|(?:top|bottom|pink) rectangle|are the sides equal)", low))
        unsupported = bool(re.search(r"\b(?:decimal|negative|subtraction|subtract|round|multipli|formula|volume|angles?)\w*\b|\d\s*(?:times|[×*/+])\s*\d|\d+\s*-\s*\d+|(?<!\w)-\d|\d+\.\d+|(?:exchange|regroup|trade)\s+(?:one|1)\s+ten\s+(?:for|as|into)\s+ones|(?:sum of|difference between)\s+\d.*\d|\d.*cookies.*\d.*cookies|how many centimeters.*meter|quadrilateral having", low))
        kind, missing, status = "FULL", [], "missing_context"
        reason = "The local number, exchange direction, and requested units must be explicit."
        if visual:
            missing = ["referenced_figure"]
            reason = "The current question depends on an unavailable figure or chart."
        elif unsupported:
            status, reason = "unsupported_task", "The visible task belongs to a different mathematical operation or representation; this adapter only checks one hundred-to-tens exchange."
        elif eq_probe:
            number, kind, status, reason = number or 100, "P_EQ", "supported_local_task", "The hundred/tens equivalence prerequisite is explicit."
        elif unit_probe and number is not None:
            kind, status, reason = "P_UNIT", "supported_local_task", "The explicit place-value count/value prerequisite is supported."
        elif (direction or borrowed) and number is not None:
            if borrowed or re.search(r"\bdigit\b|what will the \d in", low):
                status, reason = "ambiguous_wording", "Digit versus count wording is ambiguous: fourteen tens is an exchanged count, not a single decimal digit. The recorded reply alone is not an error diagnosis."
            elif re.search(r"hundreds.*tens.*ones|place.value (?:table|representation)|how many (?:hundreds|tens)", low):
                status, reason = "supported_local_task", "Number, one-hundred-to-tens direction and requested unit representation are explicit."
            else:
                status, reason = "ambiguous_wording", "Exchange direction is known, but the requested answer format needs explicit unit counts."
        elif direction or borrowed or re.search(r"\b(?:exchange|regroup|borrow)\b", low) or unit_probe:
            if number is None: missing.append("single_three_digit_number")
            if not (direction or borrowed): missing.append("exchange_direction")
            if not re.search(r"hundreds.*tens.*ones|place.value", low): missing.append("requested_representation")
        elif re.search(r"place value.*\d|\d.*place value", low):
            status, reason = "unsupported_task", "An explicit place-value task is visible, but it is outside the approved count/value prerequisite grammar; review the exact task rather than infer a regrouping exchange."
        else:
            missing = ["exact_local_task"]
            reason = "The excerpt omits the local task or its referenced quantities. There is no evidence that the intended task is regrouping."
        parameters = {"number": number, "question_kind": kind, "exchange": "one_hundred_to_tens" if direction or borrowed or status == "supported_local_task" else None,
                      "exposure_key": f"base10-exchange:{number}" if number is not None else "unknown",
                      "stop_requested": is_departure(messages[-1]["text"]),
                      "original_ambiguous": status == "ambiguous_wording"}
        return TaskContract("source-" + digest({"text": text, "parameters": parameters})[:20], "task-contract-1", "base10_regrouping",
                            status, text, parameters, [_span(message, i)], digest(original), missing, reason)

    def _task(self, task, kind, prompt, number=None, role="teaching"):
        n = task.parameters.get("number") if number is None else number
        parameters = {**task.parameters, "number": n, "question_kind": kind, "bank_role": role,
                      "exchange": "one_hundred_to_tens", "exposure_key": f"base10-exchange:{n}"}
        return TaskContract(f"regrouping-v1:{role}:{n}:{kind}", "task-contract-1", "base10_regrouping", "supported_local_task", prompt,
                            parameters, task.source_spans, task.source_hash, [], "Explicit authored local question; mathematical content checked and human review pending.", "session-only")

    def select_verification(self, task, exposures):
        shown = {task.parameters.get("number")}
        for exposure in exposures:
            shown.add(exposure.get("parameters", {}).get("number"))
            key = exposure.get("exposure_key", "")
            if key.startswith("base10-exchange:"):
                try: shown.add(int(key.split(":")[-1]))
                except ValueError: pass
        for n in CONTENT["verification_bank"]["numbers"]:
            if n not in shown:
                text = f"Without help, represent {n} as hundreds, tens and ones after exchanging one hundred for tens. Explain both changes and why the total value stays the same."
                return self._task(task, "VERIFY", text, n, "verification")
        return None

    def candidate_actions(self, task, state):
        n = task.parameters.get("number")
        candidates = []
        provenance = {"source": "content/regrouping_bank_v1.json", "authored_content": True,
                      "mathematical_validation": self.content_validation, "human_review": "pending_review"}
        if task.context_status in {"missing_context", "ambiguous_wording"}:
            if task.context_status == "ambiguous_wording" and n is not None:
                h, t, u = n // 100, n // 10 % 10, n % 10
                text = f"Start with {h} hundreds, {t} tens and {u} ones. Exchange one hundred for tens. How many hundreds, tens and ones do you have now? Explain what changed."
                context_task = self._task(task, "FULL", text)
                cid = "C_RESTATE"
            else:
                missing = task.missing_fields
                field = missing[0] if missing else "requested_representation"
                wording = {"referenced_figure": "Please provide the actual referenced figure or describe its visible labels and quantities.",
                           "exact_local_task": "What exact local question or quantities are you working with? Please include any referenced labels or values.",
                           "single_three_digit_number": "What exact three-digit whole number is the exchange about?",
                           "exchange_direction": "What exact exchange is requested between the place-value units?",
                           "requested_representation": "Should the response state counts of hundreds, tens and ones, or a value? Please give the exact request."}
                text = wording[field]
                context_task = replace(task, prompt=text)
                cid = "C_CONTEXT"
            candidates.append(ActionCandidate(cid, "context", text, context_task, {"context_fields": task.missing_fields, "exposure_key": task.parameters.get("exposure_key")},
                              ["wording_mismatch"], {}, [], CONTENT["version"], provenance, priority=0,
                              answered_content_key="context:" + task.context_status, response_format="Explicit task context or unit-labeled representation", reasoning_request=True))
        if n is None or not 100 <= n <= 999 or task.context_status == "unsupported_task":
            return candidates
        states = {s["hypothesis_id"]: s["status"] for s in state.get("hypothesis_summaries", [])}
        history = state.get("effective_history", [])
        numeric_success = any(e.get("validation", {}).get("correctness") == "correct" and e.get("validation", {}).get("reasoning") in {"absent", "unresolved"} and e.get("validation", {}).get("full_representation") for e in history)
        unit_uncertain = any(e.get("validation", {}).get("category") in {"value_count_ambiguity", "unit_incomplete", "unit_mismatch"} for e in history)
        for pid, probe in CONTENT["probes"].items():
            probe_n = n
            if pid == "P_UNIT" and n // 10 % 10 == 0:
                shown = {item.get("parameters", {}).get("number") for item in state.get("exposures", [])} | {n}
                probe_n = next(value for value in range(110, 1000) if value // 10 % 10 and value not in shown and value not in CONTENT["verification_bank"]["numbers"])
            text = probe["text"].format(N=probe_n, t=probe_n // 10 % 10)
            priority = probe["priority"]
            if pid == "P_UNIT" and not unit_uncertain: priority = 60
            if pid == "P_EQ" and not unit_uncertain: priority = 5
            if pid == "P_REASON" and numeric_success: priority = 1
            key = f"{pid}:{probe_n}"
            answered = any(e.get("task", {}).get("parameters", {}).get("question_kind") == pid and e.get("validation", {}).get("correctness") in {"correct", "incorrect"} and (pid != "P_UNIT" or e["task"]["parameters"]["number"] // 10 % 10 != 0) for e in history)
            probe_task = self._task(task, pid, text, probe_n)
            if probe_n != n:
                probe_task = replace(probe_task, parameters={**probe_task.parameters, "episode_number": n, "fresh_unit_item": "Zero count gives limited nonzero-unit evidence; authored session-unseen nonzero item."})
            candidates.append(ActionCandidate(pid, "probe", text, probe_task, {"exposure_key": f"base10-exchange:{probe_n}"},
                              [probe["target"]], probe["branches"], probe["disclosure"], CONTENT["version"], provenance,
                              available=not answered, reason="This question has already received interpretable evidence." if answered else "Authored prerequisite distinction; no numerical information gain claimed.",
                              priority=priority, answered_content_key=key, response_format="Explicit unit counts plus reasoning/equalities", reasoning_request=pid == "P_REASON"))
        for hid, hint in CONTENT["instructions"].items():
            text = hint["text"] + f" Now give the full hundreds, tens and ones representation for {n} after this one exchange, and explain why the value is unchanged."
            candidates.append(ActionCandidate(hid, "instruction", text, self._task(task, "FULL", text),
                              {"instruction_validated": True, "neutral_check": hid == "H_CHECK", "exposure_key": f"base10-exchange:{n}",
                               "task_finding_codes": hint.get("task_finding_codes", [])}, [hint["target"]], {}, hint["disclosure"],
                              CONTENT["version"], provenance, priority=100, answered_content_key=f"{hid}:{n}",
                              response_format="Full three-unit representation and consistent explanation", reasoning_request=True))
        verification = self.select_verification(task, state.get("exposures", []))
        if verification:
            candidates.append(ActionCandidate("V_FRESH", "verification", verification.prompt, verification,
                              {"exposure_key": verification.parameters["exposure_key"], "verification_exposure_key": verification.parameters["exposure_key"]},
                              ["application_gap", "conservation_gap"], {}, [], CONTENT["version"], provenance,
                              priority=0, answered_content_key=verification.task_id, response_format="All three counts and explanation; outside assistance recorded", reasoning_request=True))
        return candidates

    def validate(self, event, task, assistance_history):
        if event.task_id != task.task_id or event.task_version != task.version:
            raise ValueError("Response must bind to the exact issued task and version")
        if is_departure(event.raw_text):
            return ValidationResult("unparseable", "absent", {"departure": True}, VERSION, "1", {"method": "explicit_departure"}, departure=True, category="departure")
        if task.context_status == "ambiguous_wording":
            return ValidationResult("ambiguous", "unresolved", {"raw_text": event.raw_text}, VERSION, "1", {"method": "context_gate"},
                                    ambiguity_reason="Original digit/count wording does not establish what the reply means.", category="original_wording_ambiguous")
        if task.context_status == "missing_context":
            # A minimal response to the requested missing field augments known
            # context; it must not discard the already established exchange.
            supplied = self.assess_context({"record_id": event.event_id, "task_text": event.raw_text})
            if task.missing_fields == ["single_three_digit_number"]:
                try:
                    n = read_number(event.raw_text.strip())
                    exchange(n)
                except ValueError:
                    pass
                else:
                    supplied = replace(task, task_id=task.task_id + ":context:" + event.event_id,
                        context_status="supported_local_task", missing_fields=[],
                        parameters={**task.parameters, "number": n, "exposure_key": f"base10-exchange:{n}"},
                        source_spans=task.source_spans + [{"event_id": event.event_id,"text": event.raw_text,"role": "context_supplier"}],
                        source_hash=digest({"previous":task.source_hash,"supplied":event.source_hash}),
                        reason="Actual response supplies the only missing number; known direction and requested units are retained.")
            elif "referenced_figure" not in task.missing_fields:
                # Exact concatenation of actual source text only, never an
                # invented reconstruction of a missing problem or diagram.
                known_text = "\n".join(span["text"] for span in task.source_spans)
                merged = self.assess_context({"record_id": event.event_id,"task_text": known_text + "\n" + event.raw_text})
                supplied = replace(merged, source_spans=task.source_spans + [{"event_id":event.event_id,"text":event.raw_text,"role":"context_supplier"}],
                    source_hash=digest({"previous":task.source_hash,"supplied":event.source_hash}))
            return ValidationResult("unparseable", "unresolved", {"supplied_context": supplied.to_dict()}, VERSION, "1", {"method": "actual_context_parse"}, category="context_supplied")
        n = task.parameters.get("number")
        if task.context_status != "supported_local_task" or n is None:
            return ValidationResult("unparseable", "unresolved", {}, VERSION, "1", {"method": "unsupported_task_gate"}, category="unsupported_task")
        if event.source_kind == "software_fixture" and event.structured_fields:
            parsed = event.structured_fields
            method = "isolated_typed_software_fixture"
        else:
            parsed = parse_response(event.raw_text, task.parameters["question_kind"], n)
            method = "deterministic_supported_text_grammar"
        assessed = validate_fields(parsed, n, task.parameters["question_kind"])
        return ValidationResult(**assessed, validator_id=VERSION, validator_version="1",
                                provenance={"method": method, "raw_text_preserved": True, "hidden_diagnosis_validated": False})

    def context_after(self, task, event, validation):
        if validation.category == "context_supplied":
            return TaskContract.from_dict(validation.parsed_fields["supplied_context"])
        if task.context_status == "supported_local_task":
            n = task.parameters.get("episode_number", task.parameters["number"])
            return replace(task, parameters={**task.parameters, "number": n, "question_kind": "FULL", "exposure_key": f"base10-exchange:{n}"})
        return task

    def operator_validate(self, event, task, category, parsed_fields, rationale):
        if category not in self.operator_categories or not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("A documented parsing category and operator rationale are required")
        if category in {"alternate_wording_interpretation", "unparseable", "acknowledgement"}:
            return ValidationResult("ambiguous" if category == "alternate_wording_interpretation" else "unparseable", "unresolved" if category != "acknowledgement" else "absent",
                                    {"operator_category": category}, VERSION, "1", {"method": "operator_categorization", "rationale": rationale, "not_a_validated_diagnosis": True}, category=category)
        expected_kind = {"structured_full_exchange": {"FULL", "P_FULL", "P_REASON", "VERIFY"},
                         "structured_unit_count_value": {"P_UNIT"}, "structured_equivalence": {"P_EQ"}, "structured_tens_count": {"P_COMBINE"}}
        if task.parameters.get("question_kind") not in expected_kind[category]:
            raise ValueError("Operator parse category does not match the issued question")
        checked = validate_fields(parsed_fields, task.parameters["number"], task.parameters["question_kind"])
        return ValidationResult(**checked, validator_id=VERSION, validator_version="1",
                                provenance={"method": "mathematical_check_of_operator_confirmed_parse", "rationale": rationale,
                                            "operator_category": category, "parse_is_operator_judgment": True, "not_a_validated_diagnosis": True})

    def map_task_findings(self, event, validation, task, history):
        """Classify only the checked response, never the learner's hidden cause."""
        if task.context_status != "supported_local_task" or validation.operator_required or validation.departure:
            return []
        categories = {
            "unit_mismatch": ("unit_count_value_incompatible", "validated_error", "The explicit count and value do not both match the requested place-value units."),
            "unit_correct": ("unit_count_value_correct", "observed_success", "The explicit count and value match this unit task."),
            "unit_incomplete": ("unit_parts_incomplete", "incomplete", "The response omits a requested unit count or value; omitted material is not incorrect mathematics."),
            "equivalence_incorrect": ("equivalence_response_incorrect", "validated_error", "The stated number of tens does not equal one hundred on this explicit task."),
            "equivalence_correct": ("equivalence_response_correct", "observed_success", "The stated tens count matches the equivalence question."),
            "combine_incorrect": ("combined_tens_incorrect", "validated_error", "The stated combined tens count does not match the explicit addends."),
            "combine_correct": ("combined_tens_correct", "observed_success", "The stated tens count matches the explicit addends."),
            "conservation_incorrect": ("full_value_changed", "validated_error", "All three unit counts are present, but their weighted total differs from the starting number."),
            "exchange_incorrect": ("requested_exchange_not_performed", "validated_error", "All three unit counts preserve value, but do not show exactly one requested hundred-to-tens exchange."),
            "full_correct": ("exact_exchange_correct", "observed_success", "The full representation performs the requested exchange; reasoning is tracked separately."),
            "partial_representation": ("representation_incomplete", "incomplete", "The full task is missing one or more requested unit counts; no mathematical error is inferred."),
            "value_count_ambiguity": ("value_count_ambiguous", "ambiguous", "A bare number could mean a count of tens or a value in ones."),
            "original_wording_ambiguous": ("original_wording_ambiguous", "ambiguous", "The original digit/count wording has more than one valid interpretation."),
            "answer_correct_reasoning_unverified": ("math_correct_reasoning_unverified", "reasoning_issue", "The numerical response is correct, but its explanation is missing or unresolved."),
            "answer_correct_reasoning_inconsistent": ("math_correct_reasoning_inconsistent", "reasoning_issue", "The numerical response is correct, but its stated reasoning conflicts with the task."),
        }
        item = categories.get(validation.category)
        if item is None:
            return []
        code, classification, rationale = item
        if validation.category == "partial_representation" and not validation.parsed_fields.get("counts"):
            code, rationale = ("numeric_reply_lacks_units",
                "A bare number supplies none of the requested unit-labeled representation; its mathematical role is unknown.")
        # The validator, task, source and effective assistance remain explicit.
        return [TaskFinding(code, classification, event.event_id, task.task_id, task.version,
                            validation.category, validation.validator_id, validation.validator_version,
                            validation.provenance, event.assistance, rationale,
                            event.source_hash, event.source_kind, event.action_id, event.received_time)]

    def map_evidence(self, event, validation, task, history):
        links = []
        strength = "reasoning_supported" if validation.reasoning in {"consistent", "inconsistent"} else "response_only"
        def link(hypothesis, rule, relation, rationale):
            links.append(EvidenceLink(hypothesis, event.event_id, rule, RULES["version"], relation, strength, event.assistance, rationale))
        kind, correctness = task.parameters.get("question_kind"), validation.correctness
        category = validation.category
        if category in {"original_wording_ambiguous", "value_count_ambiguity", "alternate_wording_interpretation"}:
            link("wording_mismatch", "R_AMBIGUOUS", "supports", "The wording or unit interpretation is unresolved; this does not demonstrate a mathematical misconception.")
        elif correctness in {"unparseable", "ambiguous", "partial"}:
            link("unknown_cause", "R_UNKNOWN", "inconclusive", "The available response cannot distinguish the competing explanations.")
        elif kind == "P_UNIT":
            if task.parameters["number"] // 10 % 10 == 0 and correctness == "correct":
                link("unit_identity_gap", "R_ZERO_UNIT", "inconclusive", "Zero count/value alone gives limited evidence about nonzero units; use an authored fresh nonzero item if needed.")
            else:
                link("unit_identity_gap", "R_UNIT", "challenges" if correctness == "correct" else "supports", "Explicit count/value evidence on the requested units; reasoning and assistance are recorded separately.")
        elif kind == "P_EQ":
            link("hundred_tens_gap", "R_EQ", "challenges" if correctness == "correct" else "supports", "Observed equivalence response is evidence about this prerequisite, not proof of a hidden cause.")
        elif kind == "P_COMBINE":
            demonstrated = any(e.get("validation", {}).get("correctness") == "correct" and e.get("validation", {}).get("reasoning") == "consistent" and e.get("task", {}).get("parameters", {}).get("question_kind") == "P_EQ" and e.get("effective_assistance", e.get("event", {}).get("assistance")) == "unassisted" for e in history)
            link("combine_tens_gap" if demonstrated or correctness == "correct" else "unknown_cause", "R_COMBINE", "challenges" if correctness == "correct" else "supports" if demonstrated else "inconclusive", "Combining difficulty is attributed only after independently demonstrated equivalence; otherwise cause stays unresolved.")
        elif validation.full_representation:
            if correctness == "incorrect":
                if category == "conservation_incorrect":
                    link("conservation_gap", "R_CONSERVE", "supports", "The explicit complete representation changes total value; a partial reply would not establish this.")
                independently_correct = {e.get("task", {}).get("parameters", {}).get("question_kind") for e in history if e.get("validation", {}).get("correctness") == "correct" and e.get("validation", {}).get("reasoning") == "consistent" and e.get("effective_assistance", e.get("event", {}).get("assistance")) == "unassisted"}
                if {"P_UNIT", "P_EQ", "P_COMBINE"} <= independently_correct:
                    link("application_gap", "R_APPLY", "supports", "Prerequisites were independently demonstrated before this incorrect full coordination attempt.")
                if task.parameters.get("original_ambiguous"):
                    link("wording_mismatch", "R_WORDING", "challenges", "An explicit full unit-labeled representation still fails after restatement; this challenges wording as the sole explanation without eliminating it.")
                repeated = any(e.get("validation", {}).get("full_representation") and e["validation"].get("correctness") == "incorrect"
                    and e["validation"].get("category") == category and e["validation"].get("reasoning") in {"consistent", "inconsistent"}
                    and e.get("task", {}).get("parameters", {}).get("number") != task.parameters["number"] for e in history)
                if repeated and validation.reasoning in {"consistent", "inconsistent"}:
                    link("possible_slip", "R_SLIP", "challenges", "The same reasoned full-exchange error occurs on distinct numeric tasks; an isolated slip is less consistent with this evidence, without certainty about cause.")
            elif correctness == "correct" and validation.reasoning == "consistent":
                link("conservation_gap", "R_CONSERVE", "challenges", "Complete correct exchange with an equal-value explanation; assistance partition limits what this demonstrates.")
                link("application_gap", "R_APPLY", "challenges", "Observed complete coordinated performance, without a mastery claim.")
                if task.parameters.get("original_ambiguous"):
                    link("wording_mismatch", "R_WORDING", "supports", "Success after explicit-unit wording is compatible with a wording mismatch, not proof of its cause.")
                previous_action = event.parser_provenance.get("candidate_id")
                if previous_action == "H_CHECK" and event.assistance == "unassisted":
                    link("possible_slip", "R_SLIP", "supports", "A reasoned unassisted correction after a neutral check is compatible with an isolated slip.")
        if correctness == "correct" and validation.reasoning == "inconsistent":
            # A correct answer with contradictory reasoning cannot challenge a
            # prerequisite as if its underlying explanation had been established.
            links = [replace(item, relation="inconclusive", rationale="Correct output conflicts with its stated reasoning; understanding is not verified.") if item.relation == "challenges" else item for item in links]
        if not links:
            link("unknown_cause", "R_UNKNOWN", "inconclusive", "No decisive rule applies; unknown cause remains available.")
        return links
