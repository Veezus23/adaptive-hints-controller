"""Auditable, real-dialogue intake for a separate Eedi development study.

No reference label, future turn, or model-generated learner response is created.
The frozen Bridge experiment and its evaluation artifacts are never modified.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.request import urlopen

from . import ROOT

REPO = "Eedi/Question-Anchored-Tutoring-Dialogues-2k"
REVISION = "6d4eb56961aa098a901a1043aa98013dba7cf1ed"
FILES = (
    "README.md",
    "anchored-dialogues/train.csv",
    "anchored-dialogues/test.csv",
    "dq-question-metadata.csv",
    "dialogue-subjects.csv",
)
DATA = ROOT / "data" / "eedi_qatd"
MANIFEST = DATA / "source_manifest.json"
AUDIT = ROOT / "reports" / "eedi_source_audit.json"
TOPIC_AUDIT = ROOT / "reports" / "eedi_topic_inventory.json"
SPLIT = DATA / "study_split.json"
PILOT = ROOT / "reviews" / "eedi_development_pilot_v2.json"
REVIEW_TEMPLATE = ROOT / "reviews" / "eedi_manual_review_template_v2.json"
REVIEW_PACKET = ROOT / "reviews" / "eedi_decision_input_packet_v2.md"
MATH_EVIDENCE = re.compile(r"\d|[=+×*/]|\b(?:denominat\w*|numerat\w*|divid\w*|multipli\w*|subtract\w*|add(?:ing|ed)?|brackets?|percent\w*|simplif\w*|factoris\w*|inverse)\b", re.I)
TOPIC_PATTERNS = {
    "fraction_add_subtract": re.compile(r"\\frac.*?[+-].*?\\frac|(?:add|subtract).*?fractions?", re.I | re.S),
    "solve_linear_equation": re.compile(r"solve (?:the |this )?equation|first step to solve.*?equation|equation.*?first step", re.I | re.S),
    "percentage_of_amount": re.compile(r"(?:\\%|percent|percentage).*?\bof\b|\bof\b.*?(?:\\%|percent|percentage)", re.I | re.S),
}


def _digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def fetch() -> dict:
    """Download the publisher's pinned files, preserving any prior local copy."""
    DATA.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists():
        saved = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if saved.get("revision") != REVISION or set(saved.get("sha256", {})) != set(FILES):
            raise ValueError("Existing Eedi manifest differs; inspect it before proceeding.")
        for relative, expected in saved["sha256"].items():
            if not (DATA / relative).is_file() or _digest(DATA / relative) != expected:
                raise ValueError(f"Eedi source changed or missing: {relative}")
        return saved
    if any((DATA / relative).exists() for relative in FILES):
        raise ValueError("Unmanifested Eedi files exist; inspect them before downloading.")
    hashes = {}
    for relative in FILES:
        target = DATA / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".partial")
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{relative}"
        try:
            with urlopen(url, timeout=90) as response, temporary.open("wb") as output:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(block)
            if temporary.stat().st_size == 0:
                raise ValueError(f"Empty Eedi download: {relative}")
            temporary.replace(target)
            hashes[relative] = _digest(target)
        finally:
            temporary.unlink(missing_ok=True)
    manifest = {"source": f"https://huggingface.co/datasets/{REPO}",
                "revision": REVISION, "sha256": hashes,
                "purpose": "Real tutoring dialogue study; separate from frozen Bridge evaluation.",
                "license_note": "Publisher card says non-commercial research; card metadata and prose name different CC variants. Resolve before redistribution."}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_source() -> dict:
    if not MANIFEST.is_file():
        raise FileNotFoundError("Eedi source missing; run `python -m adaptive_hints_v03.eedi_study fetch`.")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("revision") != REVISION or set(manifest.get("sha256", {})) != set(FILES):
        raise ValueError("Unexpected Eedi source manifest.")
    for relative, expected in manifest["sha256"].items():
        if _digest(DATA / relative) != expected:
            raise ValueError(f"Eedi source hash mismatch: {relative}")
    return manifest


def audit() -> dict:
    """Describe source structure without reading future turns into inference."""
    manifest = verify_source()
    train = _read_csv(DATA / "anchored-dialogues/train.csv")
    test = _read_csv(DATA / "anchored-dialogues/test.csv")
    questions = _read_csv(DATA / "dq-question-metadata.csv")
    subjects = _read_csv(DATA / "dialogue-subjects.csv")
    required_dialogue = {"InterventionId", "QuestionId_DQ", "MessageSequence", "IsTutor", "MessageString"}
    required_question = {"InterventionId", "QuestionId_DQ", "Text", "Sequence", "Label"}
    for name, rows, required in (("train", train, required_dialogue), ("test", test, required_dialogue),
                                  ("questions", questions, required_question)):
        if not rows or not required.issubset(rows[0]):
            raise ValueError(f"Eedi {name} schema incomplete: {sorted(rows[0]) if rows else []}")
    train_ids = {row["InterventionId"] for row in train}
    test_ids = {row["InterventionId"] for row in test}
    if train_ids & test_ids:
        raise ValueError("Publisher train/test intervention overlap.")
    def partition(rows):
        groups = defaultdict(list)
        for row in rows:
            if row["IsTutor"] not in {"0", "1"}:
                raise ValueError("Unexpected Eedi role value.")
            groups[row["InterventionId"]].append(row)
        duplicate_sequences = []
        question_conflicts = []
        for intervention, messages in groups.items():
            sequences = [int(row["MessageSequence"]) for row in messages]
            if len(sequences) != len(set(sequences)):
                duplicate_sequences.append(intervention)
            if len({row["QuestionId_DQ"] for row in messages}) != 1:
                question_conflicts.append(intervention)
        return {"messages": len(rows), "interventions": len(groups),
                "question_ids": len({row["QuestionId_DQ"] for row in rows}),
                "duplicate_sequence_interventions": duplicate_sequences,
                "question_conflict_interventions": question_conflicts,
                "role_counts": dict(Counter("tutor" if row["IsTutor"] == "1" else "student" for row in rows))}
    question_pairs = {(row["InterventionId"], row["QuestionId_DQ"]) for row in questions}
    dialogue_pairs = {(row["InterventionId"], row["QuestionId_DQ"]) for row in train + test}
    train_question_ids = {row["QuestionId_DQ"] for row in train}
    test_question_ids = {row["QuestionId_DQ"] for row in test}
    report = {"source": manifest, "columns": {"dialogue": list(train[0]), "question": list(questions[0]),
                                                "subject": list(subjects[0]) if subjects else []},
              "publisher_partitions": {"train": partition(train), "test": partition(test)},
              "question_metadata": {"rows": len(questions), "matched_intervention_question_pairs": len(dialogue_pairs & question_pairs),
                                    "missing_intervention_question_pairs": len(dialogue_pairs - question_pairs)},
              "subjects": {"rows": len(subjects), "interventions": len({row.get("InterventionId") for row in subjects})},
              "cross_partition": {"shared_intervention_ids": len(train_ids & test_ids),
                                  "shared_question_ids": len(train_question_ids & test_question_ids)},
              "student_identity": "The released CSV has TutorId, not a stable learner ID. Student-disjoint evaluation cannot be claimed.",
              "label_boundary": "TalkMovePrediction is model-applied tutor labeling, not validated learner diagnosis. No future tutor/student message may enter an earlier decision.",
              "outcome_boundary": "Recorded continuation follows the historical tutor action only; it is not a counterfactual outcome for this prototype."}
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def _grouped_dialogues(path: Path) -> dict[str, list[dict[str, str]]]:
    grouped = defaultdict(list)
    for row in _read_csv(path):
        grouped[row["InterventionId"]].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row["MessageSequence"]))
    return dict(grouped)


def _rank(value: str, salt: str) -> str:
    return sha256(f"eedi-pilot-v1:{salt}:{value}".encode("ascii")).hexdigest()


def _decision_point(messages: list[dict[str, str]]) -> int | None:
    """Find an early content-bearing student turn with a historical next turn."""
    for index, message in enumerate(messages[:-1]):
        if index > 23:
            break
        if (message["IsTutor"] == "0" and MATH_EVIDENCE.search(message["MessageString"])
                and any(prior["IsTutor"] == "1" for prior in messages[:index])
                and messages[index + 1]["IsTutor"] == "1"):
            return index
    return None


def topic_inventory() -> dict:
    """Count nonexclusive publisher tags and conservative anchored-question matches.

    These are screening counts, not validated mathematical-topic labels. The
    candidate counts include no historical continuation or learner outcome.
    """
    verify_source()
    train = _grouped_dialogues(DATA / "anchored-dialogues/train.csv")
    train_ids = set(train)
    with (DATA / "anchored-dialogues/test.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        test_questions = {row["QuestionId_DQ"] for row in csv.DictReader(stream)}
    subjects = _read_csv(DATA / "dialogue-subjects.csv")
    question_rows = _read_csv(DATA / "dq-question-metadata.csv")
    by_intervention = defaultdict(list)
    for row in question_rows:
        if row["InterventionId"] in train_ids:
            by_intervention[row["InterventionId"]].append(row)
    tag_ids = defaultdict(set)
    for row in subjects:
        if row["InterventionId"] in train_ids and row["SubjectType"] == "Subtopic":
            tag_ids[row["SubjectName"]].add(row["InterventionId"])
    tag_counts = []
    for name, ids in tag_ids.items():
        tag_counts.append({"subtopic_tag": name, "training_conversations": len(ids),
                           "distinct_anchored_questions": len({train[i][0]["QuestionId_DQ"] for i in ids})})
    tag_counts.sort(key=lambda row: (-row["training_conversations"], row["subtopic_tag"]))
    candidates = {}
    for name, pattern in TOPIC_PATTERNS.items():
        raw, text_only, usable = [], [], []
        for intervention, messages in train.items():
            metadata = by_intervention[intervention]
            question_text = "\n".join(row["Text"] for row in metadata if row["Label"] == "Question Text")
            if not pattern.search(question_text):
                continue
            raw.append(intervention)
            image_dependent = any(row["Label"].endswith(" Image") for row in metadata)
            if not image_dependent:
                text_only.append(intervention)
                if (messages[0]["QuestionId_DQ"] not in test_questions
                        and _decision_point(messages) is not None):
                    usable.append(intervention)
        candidates[name] = {"question_text_match_training_conversations": len(raw),
                            "text_only_training_conversations": len(text_only),
                            "text_only_train_question_disjoint_with_test_and_early_decision_point": len(usable),
                            "usable_distinct_questions": len({train[i][0]["QuestionId_DQ"] for i in usable}),
                            "usable_intervention_ids": sorted(usable, key=int)}
    result = {"source_revision": REVISION, "scope": "publisher training partition only",
              "tag_warning": "Publisher subtopic tags are nonexclusive and can be broad or unrelated to the anchored question. Counts must not be summed.",
              "anchored_screen_warning": "Question-text regex and image exclusion are mechanical screening only; every candidate needs human context review.",
              "early_decision_point": "Student message with a mathematical number, operator, or action word in first 24 turns, after a tutor turn and immediately before a historical tutor turn. This is a screening rule, not a validated learner-state label; later text is not used for selection.",
              "subtopic_tags": tag_counts, "anchored_question_candidates": candidates}
    TOPIC_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    TOPIC_AUDIT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def prepare_pilot(limit: int = 24) -> dict:
    """Freeze a question-disjoint development/validation plan and real pilot IDs.

Publisher test dialogue text is never used. Its question IDs are used only to
    quarantine overlapping questions from development and validation.
    """
    verify_source()
    if PILOT.exists():
        raise ValueError("Pilot registry already exists; preserve it rather than silently resample.")
    existing_split = json.loads(SPLIT.read_text(encoding="utf-8")) if SPLIT.exists() else None
    train = _grouped_dialogues(DATA / "anchored-dialogues/train.csv")
    with (DATA / "anchored-dialogues/test.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        test_ids, test_questions = set(), set()
        for row in csv.DictReader(stream):
            test_ids.add(row["InterventionId"])
            test_questions.add(row["QuestionId_DQ"])
    train_questions = {rows[0]["QuestionId_DQ"] for rows in train.values()}
    available_questions = sorted(train_questions - test_questions, key=lambda q: _rank(q, "question"))
    if len(available_questions) < 2:
        raise ValueError("Too few publisher-train-only questions for a grouped split.")
    validation_count = max(1, round(0.2 * len(available_questions)))
    validation_questions = set(available_questions[:validation_count])
    development_questions = set(available_questions[validation_count:])
    partitions = {"development": [], "validation": [], "quarantined_shared_question": []}
    for intervention, rows in sorted(train.items(), key=lambda item: int(item[0])):
        question = rows[0]["QuestionId_DQ"]
        partition = ("quarantined_shared_question" if question in test_questions
                     else "validation" if question in validation_questions else "development")
        partitions[partition].append(intervention)
    screened = topic_inventory()["anchored_question_candidates"]
    topic_order = ("solve_linear_equation", "fraction_add_subtract", "percentage_of_amount")
    targets = {name: limit // len(topic_order) + (index < limit % len(topic_order))
               for index, name in enumerate(topic_order)}
    chosen = []
    selected_ids = set()
    for name in topic_order:
        candidates = [intervention for intervention in screened[name]["usable_intervention_ids"]
                      if intervention in partitions["development"] and intervention not in selected_ids]
        for intervention in sorted(candidates, key=lambda value: _rank(value, "review"))[:targets[name]]:
            index = _decision_point(train[intervention])
            assert index is not None
            chosen.append((intervention, train[intervention][index], name))
            selected_ids.add(intervention)
    split = {"version": "eedi-question-disjoint-pilot-v1", "publisher_revision": REVISION,
             "source_sha256": json.loads(MANIFEST.read_text(encoding="utf-8"))["sha256"],
             "method": "Official test remains sealed. Training questions also seen in official test are quarantined. Remaining training QuestionId_DQ groups are deterministically assigned 80% development / 20% validation.",
             "student_disjoint": False, "reason": "No stable student ID is released.",
             "partitions": partitions,
             "question_ids": {"development": sorted(development_questions),
                              "validation": sorted(validation_questions),
                              "official_test": sorted(test_questions)},
             "official_test_intervention_count": len(test_ids),
             "test_use": "Question IDs only for overlap quarantine; no test dialogue, label, or outcome used for development."}
    registry = {"version": "eedi-development-pilot-v2", "split_version": split["version"],
                "selection": "Topic-question-screened, text-only, first eligible early student turn followed by a recorded historical tutor turn; stable-hash-selected development interventions. Selection does not inspect future message text. Mathematical fit requires human review.",
                "cases": [{"intervention_id": intervention,
                           "question_id": train[intervention][0]["QuestionId_DQ"],
                           "prefix_end_sequence": int(message["MessageSequence"]),
                           "student_turn_sha256": sha256(message["MessageString"].encode("utf-8")).hexdigest(),
                           "screened_topic": topic,
                           "annotation_status": "pending_human_review"}
                          for intervention, message, topic in chosen],
                "boundary": "Registry contains IDs and source hashes only. Inference view stops at the recorded student turn. Historical next tutor turn and continuation require a separate reference view."}
    if existing_split is not None:
        if existing_split != split:
            raise ValueError("Previously frozen Eedi study split differs; preserve and investigate it.")
    else:
        SPLIT.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    PILOT.parent.mkdir(parents=True, exist_ok=True)
    PILOT.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return {"partition_counts": {key: len(value) for key, value in partitions.items()},
            "question_group_counts": {"development": len(development_questions),
                                      "validation": len(validation_questions),
                                      "official_test": len(test_questions),
                                      "quarantined_overlap": len(train_questions & test_questions)},
            "eligible_development_decision_points_by_topic": {
                name: len([intervention for intervention in screened[name]["usable_intervention_ids"]
                           if intervention in partitions["development"]]) for name in topic_order},
            "pilot_cases_by_screened_topic": dict(Counter(topic for _, _, topic in chosen)),
            "pilot_cases": len(chosen),
            "split": str(SPLIT), "registry": str(PILOT)}


def case_view(intervention_id: str, *, reference: bool = False) -> dict:
    """Return either a decision input or a separate historical-reference view."""
    verify_source()
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    registry = json.loads(PILOT.read_text(encoding="utf-8"))
    if intervention_id not in split["partitions"]["development"]:
        raise ValueError("Only development interventions may be viewed in the pilot.")
    case = next((case for case in registry["cases"] if case["intervention_id"] == intervention_id), None)
    if case is None:
        raise ValueError("Intervention is not in the frozen pilot registry.")
    messages = _grouped_dialogues(DATA / "anchored-dialogues/train.csv")[intervention_id]
    cutoff = case["prefix_end_sequence"]
    if not reference:
        question = [row for row in _read_csv(DATA / "dq-question-metadata.csv")
                    if row["InterventionId"] == intervention_id and row["QuestionId_DQ"] == case["question_id"]]
        subjects = [row for row in _read_csv(DATA / "dialogue-subjects.csv")
                    if row["InterventionId"] == intervention_id]
        prefix = [{"sequence": int(row["MessageSequence"]),
                   "role": "tutor" if row["IsTutor"] == "1" else "student",
                   "text": row["MessageString"]}
                  for row in messages if int(row["MessageSequence"]) <= cutoff]
        if sha256(prefix[-1]["text"].encode("utf-8")).hexdigest() != case["student_turn_sha256"]:
            raise ValueError("Pilot decision-turn source hash mismatch.")
        return {"view": "inference_input", "intervention_id": intervention_id,
                "question_id": case["question_id"], "question": [{"sequence": int(row["Sequence"]),
                    "label": row["Label"], "text": row["Text"]} for row in sorted(question, key=lambda row: int(row["Sequence"]))],
                "subjects": [{"name": row["SubjectName"], "type": row["SubjectType"]} for row in subjects],
                "messages": prefix, "cutoff_sequence": cutoff,
                "boundary": "No later tutor response, student continuation, or TalkMovePrediction in this view."}
    future = [row for row in messages if int(row["MessageSequence"]) > cutoff]
    return {"view": "historical_reference_only", "intervention_id": intervention_id,
            "cutoff_sequence": cutoff,
            "messages": [{"sequence": int(row["MessageSequence"]),
                          "role": "tutor" if row["IsTutor"] == "1" else "student",
                          "text": row["MessageString"]} for row in future],
            "boundary": "Observed continuation follows the original tutor action, never an undelivered prototype action."}


def comparison_views(intervention_id: str) -> dict:
    """Two pre-decision inputs differing only in amount of recorded history."""
    decision = case_view(intervention_id)
    messages = decision["messages"]
    latest = messages[-1]
    if latest["role"] != "student":
        raise ValueError("Pilot cutoff must end with a student response.")
    prior_tutors = [message for message in messages[:-1] if message["role"] == "tutor"]
    if not prior_tutors:
        raise ValueError("Comparison requires a prior recorded tutor message.")
    nearest_tutor = prior_tutors[-1]
    common = {"question": decision["question"], "cutoff_sequence": decision["cutoff_sequence"]}
    return {"intervention_id": intervention_id, "question_id": decision["question_id"],
            "local_context": {**common, "messages": [nearest_tutor, latest],
                              "definition": "Question + nearest preceding recorded tutor utterance + latest student response. Whether that utterance is a substantive prompt requires review."},
            "full_prefix": {**common, "messages": messages,
                            "definition": "Same question + every recorded conversation turn through the latest student response."},
            "boundary": "Both views stop at the same frozen cutoff; neither contains future turns, reference labels, or model-applied talk-move labels."}


def _fenced(text: str) -> list[str]:
    """Render exact source text as a safe Markdown code block."""
    runs = [len(match.group()) for match in re.finditer(r"`+", text)]
    fence = "`" * max(3, 1 + max(runs, default=0))
    return [fence + "text", text, fence]


def export_review_packet() -> dict:
    """Write all frozen development inputs with blank human-review fields."""
    verify_source()
    registry = json.loads(PILOT.read_text(encoding="utf-8"))
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    if REVIEW_PACKET.exists():
        raise ValueError("Review packet already exists; preserve any reviewer notes before replacing it.")
    lines = ["# Eedi development decision-input review packet", "",
             "Source: pinned Eedi Question-Anchored Tutoring Dialogues 2k. These 24 cases come only from the frozen development registry. This packet contains the real anchored question and recorded conversation through each cutoff; it contains no future turn or historical tutor response after the cutoff.", "",
             "These are two proposed input conditions, not measured experiment results. The local condition uses the nearest preceding tutor utterance; a reviewer should check whether it is a substantive prompt. The full condition adds earlier history. Both conditions use the same question and latest student response. Topic screening is provisional. Review fields are intentionally blank; AI-assisted notes are stored separately from independent human reviews.", "",
             "The transcripts may retain sensitive details despite publisher anonymization. Keep this packet local and do not redistribute it without resolving the source license and privacy requirements.", ""]
    seen = set()
    for case in registry["cases"]:
        intervention = case["intervention_id"]
        if intervention in seen or intervention not in split["partitions"]["development"]:
            raise ValueError("Duplicate or non-development case in pilot registry.")
        seen.add(intervention)
        views = comparison_views(intervention)
        cutoff = case["prefix_end_sequence"]
        if views["local_context"]["cutoff_sequence"] != cutoff or views["full_prefix"]["cutoff_sequence"] != cutoff:
            raise ValueError("Comparison cutoff differs from frozen registry.")
        lines += [f"## Intervention {intervention} · question {case['question_id']} · cutoff {cutoff}", "",
                  f"Screened topic (pending review): `{case['screened_topic']}`", "",
                  "### Anchored question, shared by both input conditions", ""]
        for item in views["local_context"]["question"]:
            lines += [f"**{item['label']} (item sequence {item['sequence']})**", "", *_fenced(item["text"]), ""]
        for title, key in (("A. Question + nearest preceding tutor utterance + latest student response", "local_context"),
                           ("B. Question + full conversation prefix", "full_prefix")):
            lines += [f"### {title}", "", "The anchored question above is included in this condition.", ""]
            for message in views[key]["messages"]:
                if message["sequence"] > cutoff:
                    raise ValueError("Future message reached the review packet.")
                lines += [f"**{message['role'].title()} · conversation sequence {message['sequence']}**", "",
                          *_fenced(message["text"]), ""]
        lines += ["### Blank review fields", "",
                  "- Usable for this topic and decision point: ______", "- Context sufficient for a verified local step: ______",
                  "- Observed evidence (cite sequences): ______", "- Observed prior assistance: ______",
                  "- Competing explanations / uncertainty: ______", "- Preferred next action and exact wording: ______",
                  "- Why this action; why alternatives were rejected: ______",
                  "- Could the answer to a diagnostic probe change the next action? ______", "- Reviewer and review date: ______", ""]
    REVIEW_PACKET.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_PACKET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(REVIEW_PACKET), "cases": len(seen),
            "comparison": ["question + nearest preceding tutor utterance + latest student response",
                           "question + full conversation prefix"],
            "future_turns_exported": 0, "review_fields_populated": 0}


def make_review_template() -> dict:
    """Create blank independent-review fields without inventing judgments."""
    verify_source()
    registry = json.loads(PILOT.read_text(encoding="utf-8"))
    if REVIEW_TEMPLATE.exists():
        raise ValueError("Review template already exists; preserve human entries and disagreements.")
    template = {"version": "eedi-manual-review-template-v2",
                "registry_version": registry["version"],
                "instructions": "Review `input` only before recording independent reviews. Keep each review separate. Open `reference` only after initial action judgments are locked. Do not use a later real response as the outcome of an undelivered action.",
                "cases": [{"intervention_id": case["intervention_id"],
                           "question_id": case["question_id"],
                           "screened_topic": case["screened_topic"],
                           "prefix_end_sequence": case["prefix_end_sequence"],
                           "independent_reviews": [],
                           "adjudication": None,
                           "review_status": "pending"}
                          for case in registry["cases"]],
                "review_fields": {"reviewer_id": None, "topic_fit": None,
                                  "context_sufficient_for_local_step": None,
                                  "observed_mathematical_evidence": [],
                                  "observed_prior_assistance": [],
                                  "competing_explanations": [],
                                  "preferred_next_action_kind": None,
                                  "action_rationale": None,
                                  "rejected_alternatives": [],
                                  "would_answer_to_probe_change_action": None,
                                  "notes": None}}
    REVIEW_TEMPLATE.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    return {"path": str(REVIEW_TEMPLATE), "cases": len(template["cases"]),
            "independent_reviews_recorded": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "audit", "topics", "prepare", "review-template", "packet", "compare", "input", "reference"))
    parser.add_argument("--intervention", help="Frozen development pilot intervention ID for input/reference")
    parser.add_argument("--limit", type=int, default=24, help="Number of pilot cases to freeze on first prepare")
    args = parser.parse_args()
    if args.command in {"input", "reference", "compare"}:
        if not args.intervention:
            parser.error("--intervention is required for input/reference/compare")
        result = comparison_views(args.intervention) if args.command == "compare" else case_view(args.intervention, reference=args.command == "reference")
    elif args.command == "fetch": result = fetch()
    elif args.command == "audit": result = audit()
    elif args.command == "topics": result = topic_inventory()
    elif args.command == "review-template": result = make_review_template()
    elif args.command == "packet": result = export_review_packet()
    else: result = prepare_pilot(args.limit)
    # Windows PowerShell may use cp1252; ASCII JSON keeps the CLI usable there.
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
