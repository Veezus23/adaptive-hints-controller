"""Exploratory Eedi context ablation on frozen development decision inputs.

This measures available history and records AI-assisted, unadjudicated action
proposals. It does not use later dialogue as an inference input or estimate
learning gains from undelivered hints.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from . import ROOT
from .eedi_study import PILOT, REVIEW_TEMPLATE, SPLIT, comparison_views, verify_source

ANNOTATIONS = ROOT / "reviews" / "eedi_ai_context_pilot_v1.json"
RESULTS = ROOT / "reports" / "eedi_context_pilot_v1.json"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _validate_annotation(case: dict, annotation: dict, views: dict, allowed_actions: set[str]) -> dict:
    if case["intervention_id"] != annotation.get("id"):
        raise ValueError("Annotation order or intervention ID differs from the frozen registry.")
    if annotation.get("local_action") not in allowed_actions or annotation.get("full_action") not in allowed_actions:
        raise ValueError(f"Unrecognized action kind for {annotation.get('id')}.")
    if not isinstance(annotation.get("additional_decision_evidence"), bool):
        raise ValueError(f"Evidence flag missing for {annotation.get('id')}.")
    if not annotation.get("note"):
        raise ValueError(f"Annotation rationale missing for {annotation.get('id')}.")
    local = views["local_context"]
    full = views["full_prefix"]
    cutoff = case["prefix_end_sequence"]
    if local["question"] != full["question"] or local["messages"][-1] != full["messages"][-1]:
        raise ValueError("Paired conditions do not share the question and latest student response.")
    if local["cutoff_sequence"] != full["cutoff_sequence"] != cutoff:
        raise ValueError("Paired conditions differ from the frozen cutoff.")
    if any(message["sequence"] > cutoff for message in full["messages"] + local["messages"]):
        raise ValueError("Future turn entered a decision condition.")
    local_sequences = {message["sequence"] for message in local["messages"]}
    omitted = [message for message in full["messages"] if message["sequence"] not in local_sequences]
    omitted_sequences = {message["sequence"] for message in omitted}
    citations = annotation.get("omitted_sequences")
    if not isinstance(citations, list) or len(citations) != len(set(citations)):
        raise ValueError(f"Invalid citation list for {annotation.get('id')}.")
    if not set(citations).issubset(omitted_sequences):
        raise ValueError(f"Annotation for {annotation.get('id')} cites a local, absent, or future turn.")
    if annotation["additional_decision_evidence"] != bool(citations):
        raise ValueError(f"Evidence flag and citations disagree for {annotation.get('id')}.")
    nearest = local["messages"][0]
    if nearest["role"] != "tutor":
        raise ValueError("Local condition lacks a preceding tutor utterance.")
    return {
        "intervention_id": case["intervention_id"],
        "question_id": case["question_id"],
        "screened_topic": case["screened_topic"],
        "cutoff_sequence": cutoff,
        "nearest_tutor_sequence": nearest["sequence"],
        "nearest_tutor_has_question_mark": "?" in nearest["text"],
        "full_prefix_turn_count": len(full["messages"]),
        "omitted_turn_count": len(omitted),
        "omitted_student_turn_count": sum(message["role"] == "student" for message in omitted),
        "additional_decision_evidence": annotation["additional_decision_evidence"],
        "cited_omitted_sequences": citations,
        "local_action": annotation["local_action"],
        "full_action": annotation["full_action"],
        "action_kind_changes": annotation["local_action"] != annotation["full_action"],
        "ai_assisted_note": annotation["note"],
    }


def run_context_pilot(*, write: bool = True) -> dict:
    """Check every reviewed input and summarize a paired, descriptive pilot."""
    manifest = verify_source()
    registry = json.loads(PILOT.read_text(encoding="utf-8"))
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    annotations = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    cases = registry["cases"]
    judged = annotations["cases"]
    if len(cases) != 24 or len(judged) != len(cases) or len({row["id"] for row in judged}) != len(cases):
        raise ValueError("Expected exactly one AI-assisted annotation for each of 24 frozen cases.")
    if set(row["intervention_id"] for row in cases) - set(split["partitions"]["development"]):
        raise ValueError("A pilot case is not in the development split.")
    if annotations.get("status") != "AI-assisted exploratory development judgments; pending independent human review":
        raise ValueError("Annotation provenance is missing or changed.")
    allowed = set(annotations["action_kinds"])
    rows = [_validate_annotation(case, annotation, comparison_views(case["intervention_id"]), allowed)
            for case, annotation in zip(cases, judged)]
    human_template = json.loads(REVIEW_TEMPLATE.read_text(encoding="utf-8"))
    if any(row["independent_reviews"] or row["adjudication"] is not None for row in human_template["cases"]):
        human_review_status = "contains_entries; not used"
    else:
        human_review_status = "blank; not modified"
    summary = {
        "cases": len(rows),
        "topic_screen_counts": dict(Counter(row["screened_topic"] for row in rows)),
        "cases_with_omitted_turns": sum(row["omitted_turn_count"] > 0 for row in rows),
        "cases_with_omitted_student_turns": sum(row["omitted_student_turn_count"] > 0 for row in rows),
        "total_omitted_turns": sum(row["omitted_turn_count"] for row in rows),
        "nearest_tutor_without_question_mark": sum(not row["nearest_tutor_has_question_mark"] for row in rows),
        "ai_judged_additional_decision_evidence": sum(row["additional_decision_evidence"] for row in rows),
        "ai_proposed_action_kind_changes": sum(row["action_kind_changes"] for row in rows),
        "ai_proposed_full_action_counts": dict(Counter(row["full_action"] for row in rows)),
        "full_proposal_differs_from_always_clarify": sum(row["full_action"] != "clarification" for row in rows),
        "full_proposal_differs_from_always_diagnostic_probe": sum(row["full_action"] != "diagnostic_probe" for row in rows),
    }
    result = {
        "version": "eedi-context-pilot-v1",
        "design": "Exploratory paired context ablation; question + nearest prior tutor utterance + latest student reply versus question + full prefix. Both at the same frozen development cutoff.",
        "source_revision": manifest["revision"],
        "source_manifest_sha256": _digest(ROOT / "data" / "eedi_qatd" / "source_manifest.json"),
        "registry_sha256": _digest(PILOT),
        "annotations_sha256": _digest(ANNOTATIONS),
        "provenance": annotations["status"],
        "rubric": annotations["rubric"],
        "human_review_template": human_review_status,
        "summary": summary,
        "cases": rows,
        "interpretation_boundary": "Structural counts are exact for these 24 selected development inputs. Evidence and action judgments are AI-assisted, unblinded, and unadjudicated. Action differences are neither correctness nor learning-gain estimates. No future turns, official test cases, invented student continuations, or historical tutor-response labels were used to choose actions.",
    }
    if write:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    result = run_context_pilot()
    print(json.dumps({"results": str(RESULTS), "summary": result["summary"]}, indent=2))


if __name__ == "__main__":
    main()
