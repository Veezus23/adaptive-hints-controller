"""Separate Eedi development pilot of the shared rule-based action controller."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

from . import ROOT
from .eedi_action_controller import (ASSISTANCE, CONTEXT, EXPLANATIONS, PERFORMANCE,
                                     select_action)
from .eedi_study import (PILOT, REVIEW_TEMPLATE, SPLIT, case_view, verify_source)

ANNOTATIONS = ROOT / "reviews" / "eedi_ai_action_evidence_v2.json"
WINDOWS = ROOT / "reports" / "eedi_action_windows_v2.json"
RESULTS = ROOT / "reports" / "eedi_action_pilot_v2.json"
TRACE_REPORT = ROOT / "reports" / "EEDI_ACTION_PILOT_V2_CASE_TRACES.md"

V1_HASHES = {
    ROOT / "reports" / "eedi_context_pilot_v1.json": "b73e1a639a9c634b343d2a369585256a92d60581ddd723de27806d44068e949d",
    ROOT / "reports" / "EEDI_CONTEXT_PILOT_V1.md": "767d3f8aaa7a43c3a9c452ed1116b7f7525df6957ba8c9ae81a685c1dc4017b4",
    ROOT / "reviews" / "eedi_ai_context_pilot_v1.json": "c5fcfef575416d4ed5e417b51368d0390fa79b0ef63d9454b341b86d91aef106",
    ROOT / "reviews" / "eedi_decision_input_packet_v2.md": "5b7334f3c4a9168aaf4f4b4b8addde1c20afdc9a68f2656ab1d484803779990b",
}
SOCIAL_OPENERS = re.compile(r"\b(?:how can i help|how are you|can i help|did you mean to|cancelled your help request)\b", re.I)
EXPLICIT_MATH = re.compile(r"\d|[=+*/×÷%]|\\frac|\\%")
FIELDS = {"context": CONTEXT, "performance": PERFORMANCE,
          "competing_explanations": EXPLANATIONS, "prior_assistance": ASSISTANCE}


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def verify_v1_unchanged() -> dict:
    found = {str(path.relative_to(ROOT)): _digest(path) for path in V1_HASHES}
    if any(found[str(path.relative_to(ROOT))] != expected for path, expected in V1_HASHES.items()):
        raise ValueError("A frozen v1 context-audit artifact changed; stop before running v2.")
    return found


def local_window_v2(decision: dict) -> dict:
    """Select latest non-social tutor prompt and its contiguous adjacent context."""
    messages = decision["messages"]
    if not messages or messages[-1]["role"] != "student":
        raise ValueError("Decision prefix must end in a student turn.")
    tutor_indices = [i for i, row in enumerate(messages[:-1]) if row["role"] == "tutor"]
    if not tutor_indices:
        raise ValueError("No prior tutor turn for local window.")
    candidates = [i for i in tutor_indices if "?" in messages[i]["text"]
                  and not SOCIAL_OPENERS.search(messages[i]["text"])]
    fallback = not candidates
    prompt_index = candidates[-1] if candidates else tutor_indices[-1]
    prompt = messages[prompt_index]
    start_index = prompt_index if fallback or EXPLICIT_MATH.search(prompt["text"]) else max(0, prompt_index - 2)
    local = messages[start_index:]
    if local[-1] != messages[-1] or any(row["sequence"] > decision["cutoff_sequence"] for row in local):
        raise ValueError("Local window crossed the decision cutoff.")
    return {
        "question": decision["question"], "messages": local,
        "cutoff_sequence": decision["cutoff_sequence"],
        "selected_tutor_prompt_sequence": prompt["sequence"],
        "window_start_sequence": local[0]["sequence"],
        "fallback_to_nearest_tutor": fallback,
        "included_sequences": [row["sequence"] for row in local],
        "rule": "Latest non-social question-marked tutor prompt; fallback nearest tutor. If prompt has no explicit math symbol, prepend up to two adjacent turns; keep all turns through cutoff.",
    }


def paired_inputs(intervention_id: str) -> dict:
    decision = case_view(intervention_id)
    local = local_window_v2(decision)
    full = {"question": decision["question"], "messages": decision["messages"],
            "cutoff_sequence": decision["cutoff_sequence"],
            "included_sequences": [row["sequence"] for row in decision["messages"]]}
    if local["question"] != full["question"] or local["messages"][-1] != full["messages"][-1]:
        raise ValueError("Paired inputs differ in question or latest response.")
    return {"local_window_v2": local, "full_prefix": full}


def window_manifest(*, write: bool = True) -> dict:
    """Export revised-window metadata without changing the original v1 inputs."""
    verify_source()
    v1_hashes = verify_v1_unchanged()
    registry = json.loads(PILOT.read_text(encoding="utf-8"))
    rows = []
    for case in registry["cases"]:
        views = paired_inputs(case["intervention_id"])
        local, full = views["local_window_v2"], views["full_prefix"]
        rows.append({"intervention_id": case["intervention_id"],
                     "question_id": case["question_id"], "cutoff_sequence": case["prefix_end_sequence"],
                     "local_window_v2": {key: local[key] for key in
                                         ("selected_tutor_prompt_sequence", "window_start_sequence",
                                          "fallback_to_nearest_tutor", "included_sequences", "rule")},
                     "full_prefix_sequences": full["included_sequences"]})
    result = {"version": "eedi-action-windows-v2", "registry_sha256": _digest(PILOT),
              "v1_artifact_sha256": v1_hashes, "cases": rows,
              "boundary": "Sequence metadata only; both inputs stop at the same real student turn. V1 inputs remain unchanged."}
    if write:
        WINDOWS.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _validate_field(name: str, field: dict, visible: dict[int, dict], latest_student_sequence: int) -> None:
    if not isinstance(field, dict) or field.get("value") not in FIELDS[name]:
        raise ValueError(f"Invalid {name} value.")
    citations = field.get("citations")
    if not isinstance(citations, list) or len(set(citations)) != len(citations):
        raise ValueError(f"Invalid {name} citations.")
    if any(not isinstance(sequence, int) or sequence not in visible for sequence in citations):
        raise ValueError(f"{name} cites a message outside its own condition.")
    if not isinstance(field.get("note"), str) or not field["note"].strip():
        raise ValueError(f"{name} needs an evidence note.")
    if field["value"] != "unknown" and field["value"] != "none_visible" and not citations:
        raise ValueError(f"Non-unknown {name} needs a visible source-message citation.")
    if name == "performance" and field["value"] != "unknown" and latest_student_sequence not in citations:
        raise ValueError("Observed performance must cite the latest student response.")
    if name == "prior_assistance" and any(visible[sequence]["role"] != "tutor" for sequence in citations):
        raise ValueError("Prior assistance must cite tutor messages in its own condition.")
    if name == "prior_assistance" and field["value"] == "none_visible" and citations:
        raise ValueError("None-visible assistance cannot cite a tutor action.")
    if name == "competing_explanations":
        hypotheses = field.get("hypotheses")
        if not isinstance(hypotheses, list) or any(not isinstance(item, str) or not item for item in hypotheses):
            raise ValueError("Competing hypotheses must be nonempty strings when present.")
        expected = {"multiple": lambda n: n >= 2, "single": lambda n: n == 1, "unknown": lambda n: n == 0}
        if not expected[field["value"]](len(hypotheses)):
            raise ValueError("Competing-explanation state does not match its hypotheses.")


def _validate_evidence(evidence: dict, condition: dict) -> None:
    if set(evidence) != set(FIELDS):
        raise ValueError("Evidence must contain exactly the four controller fields.")
    visible = {row["sequence"]: row for row in condition["messages"]}
    latest = condition["messages"][-1]["sequence"]
    for name, field in evidence.items():
        _validate_field(name, field, visible, latest)


def render_case_traces(result: dict) -> str:
    """Readable paired evidence and rule trace without raw transcript text."""
    lines = ["# Eedi v2 action-controller development traces", "",
             "AI-assisted evidence pending independent human review. Cases are frozen development inputs only. Citations are conversation sequence numbers visible in the named input condition. No action was delivered to a learner; no action quality or learning outcome is estimated.", ""]
    for row in result["cases"]:
        lines += [f"## Intervention {row['intervention_id']} · screened {row['screened_topic']} · cutoff {row['cutoff_sequence']}", ""]
        for name, label in (("local_window_v2", "Revised local window"), ("full_prefix", "Full prefix")):
            item = row[name]
            decision = item["decision"]
            lines += [f"### {label}", "",
                      f"Input message sequences: {', '.join(map(str, row['input_sequences'][name]))}.", "",
                      f"**Selected:** `{decision['action']}` via `{decision['selected_rule']}`. {decision['selection_reason']}", ""]
            for field_name in ("context", "performance", "competing_explanations", "prior_assistance"):
                field = item["evidence"][field_name]
                citation = ", ".join(map(str, field["citations"])) or "none"
                hypothesis = (" Hypotheses: " + "; ".join(field["hypotheses"]) + ".") if field_name == "competing_explanations" and field["hypotheses"] else ""
                lines.append(f"- `{field_name}` = `{field['value']}`; cited sequences {citation}. {field['note']}{hypothesis}")
            lines += ["", "Rule trace: " + "; ".join(f"{rule['rule']} {rule['disposition']}" for rule in decision["rule_trace"]) + ".", ""]
        lines += [f"Paired action kind differs: **{'yes' if row['action_differs'] else 'no'}**.", ""]
    return "\n".join(lines)


def run_action_pilot(*, write: bool = True) -> dict:
    """Run the same controller independently on both cited evidence conditions."""
    verify_source()
    v1_hashes = verify_v1_unchanged()
    registry = json.loads(PILOT.read_text(encoding="utf-8"))
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    annotations = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    if annotations.get("status") != "AI-assisted development evidence; pending independent human review":
        raise ValueError("AI annotation provenance missing or altered.")
    cases = registry["cases"]
    annotation_rows = annotations.get("cases", [])
    if len(cases) != 24 or len(annotation_rows) != 24 or len({row.get("id") for row in annotation_rows}) != 24:
        raise ValueError("Expected one distinct paired annotation per frozen development case.")
    if any(case["intervention_id"] not in split["partitions"]["development"] for case in cases):
        raise ValueError("A frozen pilot case is outside development.")
    rows = []
    for case, annotation in zip(cases, annotation_rows):
        if case["intervention_id"] != annotation.get("id"):
            raise ValueError("Evidence annotations differ from the frozen registry order.")
        views = paired_inputs(case["intervention_id"])
        if set(annotation) != {"id", "local_window_v2", "full_prefix"}:
            raise ValueError("Annotations may contain evidence only, not actions or references.")
        decisions = {}
        for name in ("local_window_v2", "full_prefix"):
            evidence = annotation[name]
            _validate_evidence(evidence, views[name])
            decisions[name] = {"evidence": evidence, "decision": select_action(evidence)}
        rows.append({"intervention_id": case["intervention_id"], "question_id": case["question_id"],
                     "screened_topic": case["screened_topic"], "cutoff_sequence": case["prefix_end_sequence"],
                     "input_sequences": {name: views[name]["included_sequences"] for name in views},
                     "local_window_metadata": {key: views["local_window_v2"][key] for key in
                                               ("selected_tutor_prompt_sequence", "window_start_sequence",
                                                "fallback_to_nearest_tutor")},
                     **decisions,
                     "action_differs": decisions["local_window_v2"]["decision"]["action"] !=
                                       decisions["full_prefix"]["decision"]["action"]})
    review_template = json.loads(REVIEW_TEMPLATE.read_text(encoding="utf-8"))
    v1_cases = {row["intervention_id"]: row for row in
                json.loads((ROOT / "reports" / "eedi_context_pilot_v1.json").read_text(encoding="utf-8"))["cases"]}
    summary = {"cases": len(rows),
               "revised_local_fallbacks": sum(row["local_window_metadata"]["fallback_to_nearest_tutor"] for row in rows),
               "revised_local_longer_than_v1": sum(len(row["input_sequences"]["local_window_v2"]) > 2 for row in rows),
               "selected_prompt_shifted_from_v1": sum(row["local_window_metadata"]["selected_tutor_prompt_sequence"] != v1_cases[row["intervention_id"]]["nearest_tutor_sequence"] for row in rows),
               "local_and_full_inputs_identical": sum(row["input_sequences"]["local_window_v2"] == row["input_sequences"]["full_prefix"] for row in rows),
               "paired_action_differences": sum(row["action_differs"] for row in rows),
               "paired_evidence_value_differences": {name: sum(row["local_window_v2"]["evidence"][name]["value"] != row["full_prefix"]["evidence"][name]["value"] for row in rows) for name in FIELDS},
               "local_actions": dict(Counter(row["local_window_v2"]["decision"]["action"] for row in rows)),
               "full_actions": dict(Counter(row["full_prefix"]["decision"]["action"] for row in rows)),
               "full_differs_from_always_clarify": sum(row["full_prefix"]["decision"]["action"] != "clarification" for row in rows),
               "full_differs_from_always_probe": sum(row["full_prefix"]["decision"]["action"] != "diagnostic_probe" for row in rows)}
    result = {"version": "eedi-action-pilot-v2", "source_revision": verify_source()["revision"],
              "registry_sha256": _digest(PILOT), "annotation_sha256": _digest(ANNOTATIONS),
              "v1_artifact_sha256": v1_hashes, "window_protocol": "docs/EEDI_ACTION_PILOT_V2_PROTOCOL.md",
              "annotation_status": annotations["status"],
              "human_review_status": "blank; not modified" if all(not row["independent_reviews"] and row["adjudication"] is None for row in review_template["cases"]) else "contains entries; not used",
              "summary": summary, "cases": rows,
              "boundary": "Evidence is AI-assisted and condition-specific. Controller receives structured evidence only. No future student/tutor turn, reference response, preferred action, validation/test dialogue, or synthetic learner continuation enters inference. No action quality, generalization, or learning benefit is measured."}
    if write:
        RESULTS.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        TRACE_REPORT.write_text(render_case_traces(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("windows", "run"))
    args = parser.parse_args()
    result = window_manifest() if args.command == "windows" else run_action_pilot()
    print(json.dumps({"output": str(WINDOWS if args.command == "windows" else RESULTS),
                      "summary": result.get("summary", {"cases": len(result["cases"])})}, indent=2))


if __name__ == "__main__":
    main()
