"""Initial decisions on authentic development excerpts; never replay follow-ups.

Saved historical classifier probabilities are displayed as descriptive context
only. Neither controller receives those probabilities or reference annotations.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json

from . import HISTORY, ROOT
from .contracts import ObservationEvent
from .data import DevelopmentSource, digest as file_digest, read_json, verify_history, write_json
from .engine import EvidenceLedgerController
from .regrouping import RegroupingAdapter


VERSION = "regrouping-initial-development-inspection-v1.1"


def initial_decision(record_id, policy_mode="adaptive", source=None):
    """One recorded initial event, at most one proposal, and zero deliveries."""
    source = source or DevelopmentSource()
    observation = source.observation(record_id)
    adapter = RegroupingAdapter()
    task = adapter.assess_context(observation)
    last = observation.observed_context[-1]
    session_id = f"offline-{policy_mode}-{record_id}"
    span = {"record_id": record_id, "context_index": len(observation.observed_context) - 1,
            "message_id": last.message_id, "role": last.role, "start": 0,
            "end": len(last.text), "text": last.text,
            "text_sha256": sha256(last.text.encode()).hexdigest()}
    event = ObservationEvent(
        event_id=f"bridge:{record_id}:{last.message_id}", session_id=session_id,
        task_id=task.task_id, task_version=task.version, action_id=None,
        source_kind="recorded_excerpt", raw_text=last.text, source_spans=[span],
        source_hash=task.source_hash,
        received_time=last.time or "not_recorded_in_source", assistance="unknown",
        parser_provenance={"source": "history_v02/baseline_v0/data/bridge_real_student_sample/full_inputs.jsonl",
                           "record_id": record_id, "boundary": "Exact observed student turn; reference labels and later tutor responses excluded."})
    engine = EvidenceLedgerController(adapter, task, initial_event=event,
        session_id=session_id, session_kind="debugging", policy_mode=policy_mode)
    trace = engine.propose().to_dict()
    state = engine.state
    assert len(state["events"]) == 1
    assert state["events"][0]["event"]["source_kind"] == "recorded_excerpt"
    assert not state["deliveries"]
    assert not state["annotations"]
    assert len(state["actions"]) <= 1
    return {"record_id": record_id, "partition": "development", "policy_mode": policy_mode,
            "observation": asdict(observation), "task": task.to_dict(),
            "trace": trace, "state": state,
            "interpretation": "Offline initial decision only. The source event is authentic; no proposed action was delivered and no subsequent response or learner outcome was observed."}


def _action(trace):
    selected = trace.get("selected")
    if selected:
        return selected["candidate"]["candidate_id"]
    outcome = trace.get("outcome") or {}
    return "close:" + outcome.get("status", trace["status"])


def _kind(trace):
    selected = trace.get("selected")
    return selected["candidate"]["kind"] if selected else "closed_or_review"


def _prompt(trace):
    selected = trace.get("selected")
    return selected["text"] if selected else None


def source_configuration_hashes():
    paths = sorted((ROOT / "adaptive_hints_v03").glob("*.py"))
    paths += sorted((ROOT / "config").glob("*.json")) + sorted((ROOT / "content").glob("*.json"))
    paths += [ROOT / "artifacts/history_v02_manifest.json",
              HISTORY / "baseline_v0/artifacts/split.json",
              HISTORY / "baseline_v0/data/bridge_real_student_sample/full_inputs.jsonl",
              HISTORY / "reviews/audit_selection.json", HISTORY / "reviews/inspection_registry.json",
              HISTORY / "artifacts/development_predictions.jsonl", HISTORY / "reports/loop_comparison.json"]
    return {path.relative_to(ROOT).as_posix(): file_digest(path) for path in paths}


def ensure_content_review_queue():
    """Create an honest pending queue; never overwrite a later human review."""
    path = ROOT / "reviews/content_review_queue_v031.json"
    bank_path = ROOT / "content/regrouping_bank_v1.json"
    bank = read_json(bank_path)
    if path.exists():
        existing = read_json(path)
        if existing["content_sha256"] != file_digest(bank_path):
            raise ValueError("Content changed after this review queue was created; preserve the queue and create a versioned successor.")
        return existing
    adapter = RegroupingAdapter()
    entries = []
    for kind, collection in (("probe", bank["probes"]), ("instruction", bank["instructions"])):
        for content_id, content in collection.items():
            entries.append({"content_id": content_id, "kind": kind, "template": content["text"],
                            "target": content.get("target"), "disclosure": content.get("disclosure", []),
                            "human_review_status": "pending_review", "reviewer": None,
                            "checklist": {item: "pending_review" for item in bank["review_checklist"]},
                            "notes": [], "provenance": "Authored tutor content, not learner data or replacement annotations."})
    for number in bank["verification_bank"]["numbers"]:
        entries.append({"content_id": f"V_FRESH:{number}", "kind": "verification", "number": number,
                        "freshness": "session-only", "human_review_status": "pending_review", "reviewer": None,
                        "checklist": {item: "pending_review" for item in bank["review_checklist"]},
                        "notes": [], "provenance": "Authored full-exchange task; no observed learner response or outcome."})
    for content_id in ("C_CONTEXT", "C_RESTATE"):
        entries.append({"content_id": content_id, "kind": "context", "source": "adaptive_hints_v03/regrouping.py",
                        "human_review_status": "pending_review", "reviewer": None,
                        "checklist": {item: "pending_review" for item in bank["review_checklist"]},
                        "notes": [], "provenance": "Authored minimal-context/unit-explicit template; no claimed expert review."})
    queue = {"version": "regrouping-content-review-queue-v1", "content_version": bank["version"],
             "content_sha256": file_digest(bank_path), "mathematical_validation": adapter.content_validation,
             "human_review_status": "pending_review", "entries": entries,
             "boundary": "Executed mathematics checks do not complete wording, pedagogy, target, disclosure or age-assumption review."}
    write_json(path, queue)
    return queue


def _aggregate(records):
    return {"n": len(records),
            "context_status_counts": dict(sorted(Counter(row["task"]["context_status"] for row in records).items())),
            "adaptive_initial_action_counts": dict(sorted(Counter(row["adaptive_action"] for row in records).items())),
            "fixed_initial_action_counts": dict(sorted(Counter(row["fixed_action"] for row in records).items())),
            "saved_v02_initial_action_counts": dict(sorted(Counter(row["saved_v02_action"] for row in records).items())),
            "adaptive_status_counts": dict(sorted(Counter(row["trace"]["status"] for row in records).items())),
            "changed_action_type_vs_saved_v02": sum(row["changed_action_type_vs_saved_v02"] for row in records),
            "changed_prompt_text_vs_saved_v02": sum(row["changed_prompt_text_vs_saved_v02"] for row in records),
            "changed_candidate_vs_same_gate_fixed": sum(row["changed_candidate_vs_fixed"] for row in records),
            "previously_inspected_records": len(records),
            "new_real_followup_observations": 0, "actual_deliveries": 0,
            "observed_learning_outcomes": 0}


def _render_traces(records):
    lines = ["# Regrouping v0.3.1 — authentic reviewed-case initial traces", "",
             "These are the existing 17 purposively selected development excerpts. Each trace contains one authentic initial observation and an undelivered proposal or an explicit review/stop result. There are no invented continuations, learner sessions, or measured outcomes. Reference labels never enter the controller.", ""]
    for row in records:
        lines += [f"## {row['record_id']}", "", f"**Context:** `{row['task']['context_status']}` — {row['task']['reason']}", "",
                  f"**Comparison:** saved v0.2 `{row['saved_v02_action']}` → adaptive `{row['adaptive_action']}`; same-gate fixed `{row['fixed_action']}`.", "",
                  "**Exact recorded messages:**", ""]
        for message in row["observation"]["observed_context"]:
            lines += [f"- {message['role']} · source message `{message['message_id']}`: {json.dumps(message['text'], ensure_ascii=False)}"]
        lines += ["", f"**Decision rule:** `{row['trace']['rule_id']}` — {row['trace']['reason']}", "",
                  "**Issued proposal (not delivered):**", "", _prompt(row["trace"]) or "No instructional prompt issued; see the explicit controller outcome.", "",
                  f"**Missing fields:** {', '.join(row['task']['missing_fields']) or 'None recorded by the current gate.'}", "",
                  "**Hypothesis evidence:**", ""]
        for summary in row["state"]["hypothesis_summaries"]:
            lines.append(f"- `{summary['hypothesis_id']}`: `{summary['status']}`; event IDs: {', '.join(summary.get('evidence_ids', [])) or 'none'}.")
        lines += ["", "**Rejected candidates:**", ""]
        rejected = row["trace"]["rejected_candidates"]
        lines.extend(["```json", json.dumps(rejected, ensure_ascii=False, indent=2), "```"] if rejected else ["No candidate rejection recorded before this context/termination rule."])
        lines += ["", "**Budget and status:**", "", "```json", json.dumps({"status": row["trace"]["status"],
                  "before": row["trace"]["budget_before"], "after": row["trace"]["budget_after"],
                  "outcome": row["trace"]["outcome"]}, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def run_inspection():
    preservation_before = verify_history()
    source = DevelopmentSource()
    records = []
    old_kind = {"clarify": "context", "diagnostic_probe": "probe", "explain_step": "probe",
                "self_check": "instruction", "strategy_cue": "instruction", "concept_reminder": "instruction",
                "independent_check": "verification", "review": "closed_or_review"}
    for rid in source.ids:
        adaptive = initial_decision(rid, "adaptive", source)
        fixed = initial_decision(rid, "fixed", source)
        assert adaptive["task"] == fixed["task"]
        assert adaptive["trace"]["budget_before"]["caps"] == fixed["trace"]["budget_before"]["caps"]
        saved = source.saved_old_decision(rid)
        saved_selected = saved["trace"].get("selected") or {}
        baseline = source.descriptive_baseline(rid)
        adaptive_action, fixed_action = _action(adaptive["trace"]), _action(fixed["trace"])
        records.append({**adaptive, "audit_selected": rid in source.selected_ids,
            "descriptive_saved_logistic_baseline": baseline,
            "baseline_use": "Display only; not supplied to the evidence engine or regrouping hypotheses.",
            "adaptive_action": adaptive_action, "fixed_action": fixed_action,
            "fixed_trace": fixed["trace"], "saved_v02_action": saved["adaptive_action"],
            "saved_v02_prompt": saved_selected.get("text"),
            "saved_v02_context": saved["case"]["context"],
            "changed_action_type_vs_saved_v02": _kind(adaptive["trace"]) != old_kind.get(saved["adaptive_action"], saved["adaptive_action"]),
            "changed_prompt_text_vs_saved_v02": _prompt(adaptive["trace"]) != saved_selected.get("text"),
            "changed_candidate_vs_fixed": adaptive_action != fixed_action,
            "actual_delivery": False, "new_real_followup_observations": 0})
    selected = [next(row for row in records if row["record_id"] == rid) for rid in source.selected_ids]
    queue = ensure_content_review_queue()
    result = {"version": VERSION, "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "exploratory_initial_development_inspection",
        "method": "One authentic recorded initial event per excerpt; adaptive and fixed first proposals use the same new context gate, mathematical content and six-prompt policy. No subsequent learner response is replayed or generated.",
        "historical_comparator": "Saved v0.2 self-report controller initial decisions; no historical model/controller rerun.",
        "fixed_policy": "Same shared engine with policy_mode=fixed: identical stop/context/pending/operator/budget/success gates; on a supported unresolved task, fixed H_EQ plus a full attempt, then the shared verification path after an actual qualifying response. Only initial decisions are compared here.",
        "scope": "92 previously used development excerpts; 17-case registry is a purposive subset and anchor is a debugging case, not independent validation.",
        "aggregate": {"development_92": _aggregate(records), "purposive_17": _aggregate(selected)},
        "human_content_review": {"queue_path": "reviews/content_review_queue_v031.json", "total_entries": len(queue["entries"]),
            "status_counts": dict(Counter(row["human_review_status"] for row in queue["entries"])),
            "mathematical_validation": queue["mathematical_validation"]},
        "source_configuration_hashes": source_configuration_hashes(),
        "history_preservation_before": preservation_before, "history_preservation_after": verify_history(),
        "new_classifier_fits": 0, "new_classifier_predictions": 0, "historical_test_inference": 0,
        "new_real_followup_observations": 0, "actual_deliveries": 0, "records": records}
    write_json(ROOT / "reports/development_inspection_v031.json", result)
    write_json(ROOT / "reports/reviewed_case_traces_v031.json", {"version": VERSION, "selection": "Original 17-case purposive audit registry", "records": selected})
    (ROOT / "reports/reviewed_case_traces_v031.md").write_text(_render_traces(selected) + "\n", encoding="utf-8")
    return result
