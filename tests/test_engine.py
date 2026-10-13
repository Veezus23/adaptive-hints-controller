"""Typed software state/validator fixtures only; no learner conversations.

The tokens below are protocol fixtures. They are never imported by empirical
inspection, learner sessions, model fitting, or outcome reports.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from adaptive_hints_v03.contracts import (ActionCandidate, EvidenceLink, ObservationEvent,
                                        TaskContract, ValidationResult)
from adaptive_hints_v03.engine import EvidenceLedgerController


class TypedFixtureAdapter:
    hypothesis_ids = ["prerequisite", "coordination", "uncertainty"]
    prerequisite_order = ["prerequisite", "coordination", "uncertainty"]
    fixed_instruction_id = "I_A"

    def __init__(self):
        self.base = TaskContract("ITEM_A", "1", "typed_software_contract", "supported_local_task", "TASK_TOKEN_A",
                                 {"exposure_key": "A"}, source_hash="FIXTURE_SOURCE")
        self.single_branch = False
        self.no_hints = False
        self.verification_exposed = False
        self.neutral_instruction = False

    def candidate_actions(self, task, state):
        public = {"authored_content": True, "software_fixture": True}
        hints = [ActionCandidate("I_A", "instruction", "INSTRUCTION_TOKEN_A", self.base,
                                 {"exposure_key": "A", "neutral_check": self.neutral_instruction}, ["prerequisite"], {}, [] if self.neutral_instruction else ["prerequisite"], provenance=public,
                                 available=not self.no_hints),
                 ActionCandidate("I_B", "instruction", "INSTRUCTION_TOKEN_B", self.base,
                                 {"exposure_key": "A"}, ["coordination"], {}, ["coordination"], provenance=public,
                                 available=not self.no_hints)]
        branches = {"CATEGORY_A": {"next_action": "I_A"}, "CATEGORY_B": {"next_action": "I_A" if self.single_branch else "I_B"}}
        probes = [ActionCandidate(name, "probe", "PROBE_TOKEN_" + name, self.base, {"exposure_key": "A"},
                                  ["prerequisite"], branches, ["prerequisite"] if name == "Q_DISCLOSE" else [],
                                  provenance=public, priority=i, answered_content_key=name,
                                  reasoning_request=name == "Q_REASON")
                  for i, name in enumerate(("Q_A", "Q_DISCLOSE", "Q_REASON"), 1)]
        independent = replace(self.base, task_id="ITEM_B", parameters={"exposure_key": "A" if self.verification_exposed else "B"}, prompt="TASK_TOKEN_B")
        verification = ActionCandidate("V_FRESH", "verification", "VERIFICATION_TOKEN", independent,
                                       {"exposure_key": independent.parameters["exposure_key"]}, provenance=public)
        result = probes + hints + [verification]
        if task.context_status in {"missing_context", "ambiguous_wording"}:
            result.append(ActionCandidate("C_CONTEXT", "context", "CONTEXT_TOKEN", self.base,
                                          {"exposure_key": "A"}, provenance=public))
        return result

    def validate(self, event, task, assistance_history):
        fields = deepcopy(event.structured_fields)
        return ValidationResult(fields.get("correctness", "unparseable"), fields.get("reasoning", "absent"), fields,
                                "typed-fixture-validator", "1", {"software_fixture_only": True},
                                fields.get("full", False), fields.get("operator", False), fields.get("departure", False),
                                category=fields.get("category"))

    def map_evidence(self, event, validation, task, history):
        f = validation.parsed_fields
        return [EvidenceLink(f.get("target", "uncertainty"), event.event_id, "typed-fixture-rule", "1",
                             f.get("relation", "inconclusive"), f.get("strength", "response_only"),
                             event.assistance, "Typed software-state evidence only.")]

    def context_after(self, task, event, validation):
        return self.base

    def operator_validate(self, event, task, category, parsed_fields, rationale):
        if category != "documented_fixture_category":
            raise ValueError("Unknown category")
        return self.validate(replace(event, structured_fields=parsed_fields), task, [])


def event(engine, fields=None, *, event_id=None, assistance="unassisted", initial=False, raw_text="TYPED_STATE_TOKEN"):
    action = engine.state["pending_action"]
    task = engine.state["root_task"] if initial else action["candidate"]["task"]
    return ObservationEvent(event_id or "EVENT_" + str(len(engine.state["events"]) + 1), engine.state["session_id"],
                            task["task_id"], task["version"], None if initial else action["action_id"],
                            "software_fixture", raw_text, source_hash="TYPED_FIXTURE_HASH", received_time="FIXTURE_TIME",
                            assistance=assistance, structured_fields=fields or {})


def make_engine(adapter=None, task=None, initial_fields=None, initial_assistance="unassisted", policy_mode="adaptive"):
    adapter = adapter or TypedFixtureAdapter()
    root = task or adapter.base
    initial = None
    if initial_fields is not None:
        initial = ObservationEvent("INITIAL_EVENT", "fixture-session", root.task_id, root.version, None,
                                   "software_fixture", "INITIAL_TYPED_TOKEN", source_hash="FIXTURE", received_time="T0",
                                   assistance=initial_assistance, structured_fields=initial_fields)
    return EvidenceLedgerController(adapter, root, initial, session_id="fixture-session", policy_mode=policy_mode)


def delivered_response(engine, fields=None, **kwargs):
    pending = engine.state["pending_action"]
    engine.record_delivery(pending["action_id"], "TYPED_DELIVERY_FIXTURE", "T_DELIVERY")
    observation = event(engine, fields, **kwargs)
    return observation, engine.observe(observation)


GAP = {"correctness": "incorrect", "reasoning": "inconsistent", "target": "prerequisite", "relation": "supports", "strength": "reasoning_supported"}
FULL_GOOD = {"correctness": "correct", "reasoning": "consistent", "full": True, "relation": "challenges", "target": "coordination", "strength": "reasoning_supported"}
FULL_BAD = {"correctness": "incorrect", "reasoning": "inconsistent", "full": True, "relation": "supports", "target": "coordination", "strength": "reasoning_supported"}


def test_delivery_is_separate_and_identity_replay_is_exactly_once():
    engine = make_engine()
    first = engine.propose()
    assert first.selected["delivery_status"] == "not_delivered"
    assert engine.propose().selected["action_id"] == first.selected["action_id"]
    before = engine.budget()
    response = event(engine)
    with pytest.raises(ValueError, match="actual delivery separately"):
        engine.observe(response)
    assert engine.state["events"] == []
    engine.record_delivery(first.selected["action_id"], "TYPED_DELIVERY", "T1")
    engine.record_delivery(first.selected["action_id"], "TYPED_DELIVERY", "T1")
    assert len(engine.state["deliveries"]) == 1
    assert engine.budget() == before
    engine.observe(response)
    saved = engine.state
    engine.observe(response)
    assert engine.state == saved
    with pytest.raises(ValueError, match="identity cannot be changed"):
        engine.observe(replace(response, assistance="assisted"))


def test_equal_text_on_a_distinct_delivered_action_remains_real_event():
    engine = make_engine()
    engine.propose()
    delivered_response(engine, raw_text="SAME_TYPED_TOKEN", event_id="EVENT_A")
    delivered_response(engine, raw_text="SAME_TYPED_TOKEN", event_id="EVENT_B")
    assert len(engine.state["events"]) == 2
    assert engine.state["events"][0]["event"]["raw_text"] == engine.state["events"][1]["event"]["raw_text"]


def test_context_gate_blocks_initial_gap_and_departure_overrides_gates():
    adapter = TypedFixtureAdapter()
    ambiguous = replace(adapter.base, context_status="ambiguous_wording")
    engine = make_engine(adapter, ambiguous, GAP)
    assert all(summary["status"] == "unassessed" for summary in engine.state["hypothesis_summaries"])
    assert engine.propose().selected["candidate"]["candidate_id"] == "C_CONTEXT"
    departed = make_engine(adapter, ambiguous, {"departure": True})
    assert departed.propose().outcome["status"] == "stopped_by_learner"
    assert departed.budget()["used"]["total"] == 0


def test_context_and_early_termination_traces_explain_every_blocked_action_kind():
    for context in ("missing_context", "ambiguous_wording"):
        adapter = TypedFixtureAdapter()
        task = replace(adapter.base, context_status=context, missing_fields=["typed_required_field"])
        engine = make_engine(adapter, task)
        trace = engine.propose()
        assert trace.selected["kind"] == "context"
        assert all(item["kind"] == "context" for item in trace.eligible_candidates)
        assert {item["kind"] for item in trace.rejected_candidates} == {"probe", "instruction", "verification"}
        assert all(context in item["reason"] for item in trace.rejected_candidates)
        assert engine.propose().rejected_candidates == trace.rejected_candidates
        assert engine.budget()["used"]["total"] == 1

    class NoDomainCandidates(TypedFixtureAdapter):
        def candidate_actions(self, task, state):
            return []

    for stopped in (False, True):
        adapter = NoDomainCandidates()
        task = replace(adapter.base, context_status="supported_local_task" if stopped else "unsupported_task")
        engine = make_engine(adapter, task, {"departure": True} if stopped else None)
        trace = engine.propose()
        assert trace.status == "closed"
        assert trace.eligible_candidates == []
        assert {item["kind"] for item in trace.rejected_candidates} == {"context", "probe", "instruction", "verification"}
        assert all(item["scope"] == "action_kind_summary" and item["candidate_id"].endswith(":*") for item in trace.rejected_candidates)
        assert all(("stop/departure" if stopped else "unsupported content") in item["reason"].lower() for item in trace.rejected_candidates)
        saved = engine.state
        engine.propose()
        assert engine.state["events"] == saved["events"]
        assert engine.budget()["used"]["total"] == 0


def test_reasoned_gap_goes_directly_to_instruction_uncertainty_to_probe():
    direct = make_engine(initial_fields=GAP)
    assert direct.propose().selected["candidate"]["candidate_id"] == "I_A"
    uncertain = make_engine(initial_fields={**GAP, "reasoning": "absent", "strength": "response_only"})
    assert uncertain.propose().selected["kind"] == "probe"


def test_probe_discrimination_checks_distinct_available_next_actions():
    adapter = TypedFixtureAdapter()
    adapter.single_branch = True
    trace = make_engine(adapter).propose()
    assert trace.status == "review_required"
    assert trace.selected is None
    assert len([item for item in trace.rejected_candidates if item["kind"] == "probe"]) == 3
    assert all("two distinct" in item["reason"] for item in trace.rejected_candidates if item["kind"] == "probe")
    # Every normally issued probe has two actions satisfying current gates.
    normal = make_engine()
    trace = normal.propose()
    assert len({branch["next_action"] for branch in trace.selected["candidate"]["response_branches"].values()}) == 2


def test_probe_branches_project_information_slot_before_claiming_next_action_feasible():
    engine = make_engine()
    engine.propose()
    delivered_response(engine, {})
    assert engine.budget()["used"]["information"] == 2
    adapter = engine.adapter
    later_probe = ActionCandidate("Q_OTHER", "probe", "LATER_TYPED_TOKEN", adapter.base,
                                  response_branches={"A": {"next_action": "I_A"}, "B": {"next_action": "I_B"}})
    current = ActionCandidate("Q_PROJECT", "probe", "PROJECTED_TYPED_TOKEN", adapter.base,
                              response_branches={"A": {"next_action": "I_A"}, "B": {"next_action": "Q_OTHER"}})
    candidates = adapter.candidate_actions(adapter.base, engine.state) + [later_probe, current]
    assert engine._candidate_gate(later_probe) is None
    branches = engine._projected_branches(current, candidates)
    assert branches["A"]["feasible_after_probe"]
    assert not branches["B"]["feasible_after_probe"]
    assert "allowance is exhausted" in branches["B"]["feasibility_reason"]
    assert "two distinct" in engine._candidate_gate(current, check_discrimination=True, all_candidates=candidates)


def test_observed_weak_probe_evidence_routes_declared_provisional_hint_without_certainty():
    engine = make_engine()
    engine.propose()
    weak_gap = {"correctness": "incorrect", "reasoning": "absent", "target": "prerequisite", "relation": "supports",
                "strength": "response_only", "category": "CATEGORY_A"}
    _, trace = delivered_response(engine, weak_gap)
    assert trace.selected["candidate"]["candidate_id"] == "I_A"
    assert trace.rule_id == "instruction.provisional_probe_response"
    assert "does not confirm" in trace.reason


def test_context_plus_probes_reserves_instruction_strategy_and_verification_and_resumes(tmp_path):
    adapter = TypedFixtureAdapter()
    root = replace(adapter.base, context_status="ambiguous_wording")
    engine = make_engine(adapter, root)
    engine.propose()  # context
    delivered_response(engine, {})  # Q_A
    delivered_response(engine, {})  # Q_DISCLOSE
    _, trace = delivered_response(engine, GAP)  # I_A, no additional probe
    assert trace.selected["kind"] == "instruction"
    assert engine.budget()["used"]["information"] == 3
    assert engine.budget()["remaining"]["verification"] == 1
    path = tmp_path / "fixture-state.json"
    engine.save(path)
    engine = EvidenceLedgerController(adapter, root, session_id="fixture-session", state=json.loads(path.read_text()))
    _, trace = delivered_response(engine, FULL_BAD)  # one justified I_B
    assert trace.rule_id == "instruction.evidence_justified_change"
    _, trace = delivered_response(engine, FULL_GOOD)  # verification sixth prompt
    assert trace.selected["kind"] == "verification"
    assert engine.budget()["used"] == {"total": 6, "information": 3, "instruction": 2, "verification": 1, "strategy_change": 1}
    _, trace = delivered_response(engine, FULL_GOOD)
    assert trace.outcome["status"] == "local_unassisted_success"
    assert engine.budget()["used"]["total"] == 6


def test_operator_pending_consumes_no_prompt_and_supersession_recomputes_not_duplicates():
    engine = make_engine()
    engine.propose()
    response, trace = delivered_response(engine, {"operator": True})
    assert trace.status == "operator_review_pending"
    spent = engine.budget()
    engine.propose()
    assert engine.budget() == spent
    trace = engine.annotate(response.event_id, "documented_fixture_category", GAP, "TYPED_OPERATOR_RATIONALE", "OPERATOR_FIXTURE", "ANNOTATION_A", "T2")
    assert trace.selected["candidate"]["candidate_id"] == "I_A"
    before = engine.budget()
    changed = {**GAP, "correctness": "correct", "reasoning": "consistent", "relation": "challenges"}
    engine.annotate(response.event_id, "documented_fixture_category", changed, "TYPED_CORRECTION_RATIONALE", "OPERATOR_FIXTURE", "ANNOTATION_B", "T3", supersedes="ANNOTATION_A")
    assert engine.budget() == before
    assert len(engine.state["events"]) == 1
    assert len(engine.state["annotations"]) == 2
    assert engine.state["annotations"][1]["supersedes"] == "ANNOTATION_A"
    summary = next(item for item in engine.state["hypothesis_summaries"] if item["hypothesis_id"] == "prerequisite")
    assert summary["status"] == "challenged"
    assert len(engine.state["evidence_link_history"]) >= 3
    with pytest.raises(ValueError, match="latest annotation"):
        engine.annotate(response.event_id, "documented_fixture_category", GAP, "R", "OP", "ANNOTATION_C", "T4", supersedes="ANNOTATION_A")


def test_assistance_partitions_preserve_pre_help_support_and_later_success():
    engine = make_engine(initial_fields=GAP)
    engine.propose()
    assisted_challenge = {**FULL_GOOD, "target": "prerequisite"}
    delivered_response(engine, assisted_challenge, assistance="unassisted")
    summary = next(item for item in engine.state["hypothesis_summaries"] if item["hypothesis_id"] == "prerequisite")
    assert summary["status"] == "mixed"
    assert summary["partitions"]["before_help:unassisted"]["status"] == "supported"
    assert summary["partitions"]["after_help:assisted"]["status"] == "challenged"
    assert engine.state["effective_history"][-1]["effective_assistance"] == "assisted"
    assert engine.state["pending_action"]["kind"] == "verification"


def test_no_disclosure_neutral_check_can_preserve_explicit_unassisted_evidence():
    adapter = TypedFixtureAdapter()
    adapter.neutral_instruction = True
    engine = make_engine(adapter, initial_fields=GAP)
    engine.propose()
    _, trace = delivered_response(engine, FULL_GOOD, assistance="unassisted")
    assert engine.state["effective_history"][-1]["effective_assistance"] == "unassisted"
    assert trace.outcome["status"] == "local_unassisted_success"
    assert engine.state["exposures"][-1]["neutral_check"] is True


def test_independent_complete_reasoned_initial_response_needs_no_hint():
    engine = make_engine(initial_fields=FULL_GOOD)
    trace = engine.propose()
    assert trace.outcome["status"] == "local_unassisted_success"
    assert engine.budget()["used"]["total"] == 0
    unknown = make_engine(initial_fields=FULL_GOOD, initial_assistance="unknown")
    assert unknown.propose().selected["kind"] == "verification"


def test_numeric_success_without_consistent_reasoning_requests_reasoning_once():
    engine = make_engine(initial_fields={**FULL_GOOD, "reasoning": "absent"})
    trace = engine.propose()
    assert trace.selected["candidate"]["candidate_id"] == "Q_REASON"
    assert engine.state["outcome"] is None


@pytest.mark.parametrize("fields,assistance,outcome", [
    (FULL_GOOD, "unassisted", "local_unassisted_success"),
    (FULL_GOOD, "assisted", "assisted_success_only"),
    (FULL_GOOD, "unknown", "answer_correct_reasoning_unverified"),
    ({**FULL_GOOD, "reasoning": "absent"}, "unassisted", "answer_correct_reasoning_unverified"),
    ({**FULL_GOOD, "reasoning": "inconsistent"}, "unassisted", "verification_incorrect"),
    (FULL_BAD, "unassisted", "verification_incorrect"),
    ({"correctness": "partial", "reasoning": "unresolved"}, "unassisted", "verification_unresolved"),
])
def test_verification_always_ends_with_actual_outcome(fields, assistance, outcome):
    engine = make_engine(initial_fields=GAP)
    engine.propose()
    delivered_response(engine, FULL_GOOD)
    assert engine.state["pending_action"]["kind"] == "verification"
    spent = engine.budget()
    _, trace = delivered_response(engine, fields, assistance=assistance)
    assert trace.outcome["status"] == outcome
    assert engine.propose().status == "closed"
    assert engine.budget() == spent


def test_exposed_verification_item_is_rejected_even_with_different_id():
    adapter = TypedFixtureAdapter()
    adapter.verification_exposed = True
    engine = make_engine(adapter, initial_fields=GAP)
    engine.propose()
    _, trace = delivered_response(engine, FULL_GOOD)
    assert trace.outcome["status"] == "assisted_success_only"
    assert engine.budget()["used"]["verification"] == 0


def test_unclear_input_is_not_failure_and_two_validated_failures_stop():
    engine = make_engine(initial_fields=GAP)
    engine.propose()
    delivered_response(engine, {"correctness": "partial", "reasoning": "unresolved"})
    assert engine._verified_failures() == []
    failed = make_engine(initial_fields=GAP)
    failed.propose()
    delivered_response(failed, FULL_BAD)
    assert failed.budget()["used"]["strategy_change"] == 1
    _, trace = delivered_response(failed, FULL_BAD)
    assert trace.outcome["status"] == "repeated_failure"
    assert failed.budget()["used"]["instruction"] == 2


def test_repeated_failure_does_not_rotate_to_an_unrelated_hint():
    engine = make_engine(initial_fields=GAP)
    engine.propose()
    _, trace = delivered_response(engine, {**FULL_BAD, "target": "uncertainty", "relation": "inconclusive"})
    assert engine.budget()["used"]["strategy_change"] == 0
    assert trace.selected is None or trace.selected["kind"] != "instruction"


def test_operator_correction_of_closed_verification_recomputes_outcome_without_new_prompt():
    engine = make_engine(initial_fields=GAP)
    engine.propose()
    delivered_response(engine, FULL_GOOD)
    observed, trace = delivered_response(engine, FULL_BAD)
    assert trace.outcome["status"] == "verification_incorrect"
    budget = engine.budget()
    revised = engine.annotate(observed.event_id, "documented_fixture_category", FULL_GOOD, "TYPED_CORRECTION", "OP", "ANN_VERIFY", "T4")
    assert revised.status == "closed"
    assert revised.outcome["status"] == "local_unassisted_success"
    assert engine.state["outcome_history"][0]["status"] == "verification_incorrect"
    assert engine.budget() == budget


def test_unparseable_verification_closes_immediately_and_operator_review_cannot_add_prompts():
    engine = make_engine(initial_fields=GAP)
    engine.propose()
    delivered_response(engine, FULL_GOOD)
    observed, trace = delivered_response(engine, {"operator": True, "correctness": "unparseable"})
    assert trace.status == "closed"
    assert trace.outcome["status"] == "verification_unresolved"
    assert engine.state["pending_action"] is None
    assert engine.state["effective_history"][-1]["validation"]["operator_required"] is True
    spent = engine.budget()
    corrected = engine.annotate(observed.event_id, "documented_fixture_category", FULL_GOOD, "TYPED_PARSE", "OP", "ANN_UNPARSED_VERIFY", "T5")
    assert corrected.status == "closed"
    assert corrected.outcome["status"] == "local_unassisted_success"
    assert engine.budget() == spent


def test_fixed_comparator_shares_context_and_budget_gates():
    engine = make_engine(policy_mode="fixed")
    assert engine.propose().selected["candidate"]["candidate_id"] == "I_A"
    adapter = TypedFixtureAdapter()
    ambiguous = replace(adapter.base, context_status="ambiguous_wording")
    assert make_engine(adapter, ambiguous, policy_mode="fixed").propose().selected["kind"] == "context"
    unsupported = replace(adapter.base, context_status="unsupported_task")
    trace = make_engine(adapter, unsupported, policy_mode="fixed").propose()
    assert trace.outcome["status"] == "unsupported_task"
    assert trace.budget_after["used"]["total"] == 0


def test_no_answer_key_enters_public_action_contract_and_fixtures_stay_out_of_authentic_sessions():
    adapter = TypedFixtureAdapter()
    unsafe = replace(adapter.base, parameters={"answer_key": "SECRET"})
    with pytest.raises(ValueError, match="Answer keys"):
        make_engine(adapter, unsafe)
    initial = ObservationEvent("E", "actual", adapter.base.task_id, "1", None, "software_fixture", "TOKEN", source_hash="H", received_time="T")
    with pytest.raises(ValueError, match="cannot enter authentic"):
        EvidenceLedgerController(adapter, adapter.base, initial, session_id="actual", session_kind="authentic_learner")


def test_resuming_cannot_delete_issued_prompts_or_change_root_identity():
    engine = make_engine()
    engine.propose()
    state = engine.state
    state["actions"] = []
    with pytest.raises(ValueError, match="Pending action"):
        EvidenceLedgerController(TypedFixtureAdapter(), TypedFixtureAdapter().base, session_id="fixture-session", state=state)
