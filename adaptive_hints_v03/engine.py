"""Generic deterministic evidence-ledger policy; contains no subject formulas.

The adapter supplies task meaning, parsing, evidence links, content and authored
prerequisite ordering. This engine owns delivery, event identity, temporal
assistance, budgets, supersession, qualitative aggregation and closure.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import (ActionCandidate, DecisionTrace, DomainAdapter, EvidenceLink, TaskFinding,
                        HypothesisSummary, ObservationEvent, SessionOutcome,
                        TaskContract, ValidationResult)


POLICY_PATH = Path(__file__).resolve().parents[1] / "config/policy.json"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _assert_no_answer_keys(value):
    if isinstance(value, dict):
        forbidden = {"answer_key", "answer_keys", "expected_answer", "correct_answer", "solution", "solutions"}
        if forbidden & set(value):
            raise ValueError("Answer keys must stay outside public task/action contracts.")
        for child in value.values():
            _assert_no_answer_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_answer_keys(child)


class EvidenceLedgerController:
    def __init__(self, adapter: DomainAdapter, task: TaskContract, initial_event: ObservationEvent | None = None,
                 *, session_id: str, session_kind: str = "debugging", state=None, policy_mode: str = "adaptive"):
        if type(task) is not TaskContract:
            raise TypeError("The shared engine requires a typed TaskContract.")
        if not session_id or session_kind not in {"debugging", "authentic_learner"} or policy_mode not in {"adaptive", "fixed"}:
            raise ValueError("A stable session ID, explicit session kind, and declared policy mode are required.")
        _assert_no_answer_keys(task.to_dict())
        self.adapter = adapter
        self.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        self._check_policy()
        if state is None:
            self._state = {
                "version": self.policy["version"], "policy_hash": digest(self.policy),
                "session_id": session_id, "session_kind": session_kind, "policy_mode": policy_mode,
                "root_task": task.to_dict(), "task": task.to_dict(), "status": "active", "outcome": None,
                "outcome_history": [], "events": [], "event_hashes": {}, "annotations": [],
                "actions": [], "deliveries": [], "pending_action": None, "pending_operator_review": None,
                "effective_history": [], "hypothesis_summaries": [], "evidence_link_history": [], "task_findings": [],
                "used_content_keys": [], "answered_candidate_ids": [], "exposures": [],
                "last_trace": None, "stop_requested": False, "manual_stop_requested": False, "strategy_changes": 0,
                "candidate_inventory": {"eligible": [], "rejected": []},
            }
            if initial_event is not None:
                self._record_initial(initial_event, task)
            self._recompute()
        else:
            self._state = deepcopy(dict(state))
            if (self._state.get("session_id") != session_id or self._state.get("session_kind") != session_kind
                    or self._state.get("policy_mode") != policy_mode or self._state.get("root_task") != task.to_dict()
                    or self._state.get("version") != self.policy["version"] or self._state.get("policy_hash") != digest(self.policy)):
                raise ValueError("Restore must preserve session, root task, policy, and debugging/authentic identity.")
            self._check_restored_state()
            self._recompute()
            if initial_event is not None:
                prior = self._state["event_hashes"].get(initial_event.event_id)
                if prior != digest(initial_event.to_dict()):
                    raise ValueError("An initial event cannot be replaced or added while resuming.")

    @property
    def state(self):
        result = deepcopy(self._state)
        result["budget"] = self.budget()
        return result

    def save(self, path):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)

    def _check_policy(self):
        expected = {"total_prompt_cap": 6, "information_prompt_cap": 3, "instruction_prompt_cap": 2,
                    "verification_prompt_cap": 1, "strategy_change_cap": 1, "post_instruction_failure_cap": 2}
        if any(self.policy.get(key) != number for key, number in expected.items()):
            raise ValueError("This version declares six/three/two/one prompt caps and one strategy change.")

    def budget(self):
        actions = self._state["actions"]
        used = {"total": len(actions), "information": sum(a["candidate"]["kind"] in {"context", "probe"} for a in actions),
                "instruction": sum(a["candidate"]["kind"] == "instruction" for a in actions),
                "verification": sum(a["candidate"]["kind"] == "verification" for a in actions),
                "strategy_change": sum(a.get("strategy_change", False) for a in actions)}
        caps = {"total": self.policy["total_prompt_cap"], "information": self.policy["information_prompt_cap"],
                "instruction": self.policy["instruction_prompt_cap"], "verification": self.policy["verification_prompt_cap"],
                "strategy_change": self.policy["strategy_change_cap"]}
        return {"used": used, "caps": caps, "remaining": {key: caps[key] - used[key] for key in used},
                "reservation": "Every information probe leaves one instruction and one verification slot."}

    def _check_restored_state(self):
        state = self._state
        if state.get("status") not in {"active", "awaiting_delivery", "awaiting_real_observation", "operator_review_pending", "review_required", "closed"}:
            raise ValueError("Unknown persisted session status.")
        actions = state.get("actions", [])
        ids = [action["action_id"] for action in actions]
        if len(set(ids)) != len(ids) or any(action.get("sequence") != i + 1 for i, action in enumerate(actions)):
            raise ValueError("Issued prompt identities/sequence are inconsistent.")
        if any(value < 0 for value in self.budget()["remaining"].values()):
            raise ValueError("Persisted prompt ledger exceeds its declared root budget.")
        delivery_ids = [delivery["action_id"] for delivery in state.get("deliveries", [])]
        if len(set(delivery_ids)) != len(delivery_ids) or not set(delivery_ids) <= set(ids):
            raise ValueError("Delivery ledger cannot duplicate or invent issued prompts.")
        expected_hashes = {entry["event"]["event_id"]: digest(entry["event"]) for entry in state.get("events", [])}
        expected_hashes.update({entry["event_id"]: digest(entry["event"]) for entry in state.get("annotations", [])})
        if expected_hashes != state.get("event_hashes"):
            raise ValueError("Event identity ledger differs from persisted source/annotation history.")
        pending = state.get("pending_action")
        if pending is not None and pending.get("action_id") not in ids:
            raise ValueError("Pending action is not present in issued prompt history.")
        if state.get("outcome") and state["status"] != "closed":
            raise ValueError("Terminal outcomes cannot silently reopen an episode.")

    def _validate_event_type(self, event):
        if type(event) is not ObservationEvent:
            raise TypeError("Use a typed ObservationEvent; arbitrary reference dictionaries are not observations.")
        if event.session_id != self._state["session_id"]:
            raise ValueError("Observation belongs to a different root session.")
        if event.source_kind == "software_fixture" and self._state["session_kind"] != "debugging":
            raise ValueError("Typed software fixtures cannot enter authentic learner sessions.")
        if not event.source_hash or not event.received_time:
            raise ValueError("Source hash and received time are required, even when assistance is unknown.")

    def _record_initial(self, event, task):
        self._validate_event_type(event)
        if event.action_id is not None or event.task_id != task.task_id or event.task_version != task.version:
            raise ValueError("Initial evidence must bind the original task without claiming delivery of our action.")
        if event.source_kind not in {"recorded_excerpt", "actual_manual_learner_response", "software_fixture"}:
            raise ValueError("An operator annotation is not an initial learner observation.")
        validation = self.adapter.validate(event, task, [])
        self._append_observation(event, task, validation, action=None, initial=True)

    def _append_observation(self, event, task, validation, action, initial=False):
        if type(validation) is not ValidationResult:
            raise TypeError("Domain validation must return a typed ValidationResult.")
        self._state["events"].append({"event": event.to_dict(), "task": task.to_dict(),
                                      "base_validation": validation.to_dict(), "action": deepcopy(action),
                                      "initial": initial, "delivery_count": len(self._state["deliveries"])})
        self._state["event_hashes"][event.event_id] = digest(event.to_dict())

    def _exposures_before(self, delivery_count):
        actions = {action["action_id"]: action for action in self._state["actions"]}
        root = self._state["root_task"]
        result = [{"action_id": None, "candidate_id": None, "task_id": root["task_id"],
                   "parameters": deepcopy(root["parameters"]), "kind": "initial_task", "disclosure_concepts": [],
                   "exposure_key": root["parameters"].get("exposure_key", root["task_id"]), "received_time": None, "neutral_check": False}]
        for delivery in self._state["deliveries"][:delivery_count]:
            candidate = actions[delivery["action_id"]]["candidate"]
            result.append({"action_id": delivery["action_id"], "candidate_id": candidate["candidate_id"],
                           "task_id": candidate["task"]["task_id"], "parameters": deepcopy(candidate["task"]["parameters"]),
                           "kind": candidate["kind"], "disclosure_concepts": deepcopy(candidate["disclosure_concepts"]),
                           "neutral_check": candidate["requirements"].get("neutral_check") is True,
                           "exposure_key": candidate["requirements"].get("exposure_key", candidate["task"]["parameters"].get("exposure_key", candidate["task"]["task_id"])),
                           "received_time": delivery["received_time"]})
        return result

    def _effective_assistance(self, event, action, exposures):
        if event.assistance == "assisted":
            return "assisted"
        is_verification = action is not None and action["candidate"]["kind"] == "verification"
        if not is_verification and any(self._exposes_help(item) for item in exposures):
            return "assisted"
        return event.assistance

    @staticmethod
    def _exposes_help(exposure):
        return bool(exposure["disclosure_concepts"] or exposure["kind"] == "instruction" and not exposure.get("neutral_check"))

    def _recompute(self):
        state = self._state
        effective = []
        findings_all = []
        used_content_keys = []
        answered = []
        links_all = []
        for original in state["events"]:
            event = ObservationEvent.from_dict(original["event"])
            task = TaskContract.from_dict(original["task"])
            annotations = [item for item in state["annotations"] if item["target_event_id"] == event.event_id]
            current_annotation = annotations[-1] if annotations else None
            validation = ValidationResult.from_dict(current_annotation["validation"] if current_annotation else original["base_validation"])
            exposures = self._exposures_before(original["delivery_count"])
            assistance = self._effective_assistance(event, original["action"], exposures)
            provenance = deepcopy(event.parser_provenance)
            if original["action"]:
                provenance["candidate_id"] = original["action"]["candidate"]["candidate_id"]
                provenance["candidate_provenance_source"] = "Immutable pending action linkage; not an operator-created learner annotation."
            effective_event = replace(event, assistance=assistance, parser_provenance=provenance)
            links = self.adapter.map_evidence(effective_event, validation, task, deepcopy(effective))
            if any(type(link) is not EvidenceLink or link.event_id != event.event_id or link.hypothesis_id not in self.adapter.hypothesis_ids for link in links):
                raise ValueError("Domain evidence links must refer to this actual event and a declared hypothesis.")
            gate_blocks = task.context_status != "supported_local_task" or validation.operator_required
            mapper = getattr(self.adapter, "map_task_findings", None)
            findings = mapper(effective_event, validation, task, deepcopy(effective)) if mapper else []
            if gate_blocks and findings:
                raise ValueError("Blocked or unresolved task content cannot produce a validated task finding.")
            if any(type(finding) is not TaskFinding or finding.event_id != event.event_id
                   or finding.task_id != task.task_id or finding.task_version != task.version
                   or finding.validation_category != validation.category
                   or finding.validator_id != validation.validator_id for finding in findings):
                raise ValueError("Task findings must bind the effective event, task and validator.")
            findings = [replace(finding, annotation_id=current_annotation["event_id"] if current_annotation else None).to_dict()
                        for finding in findings]
            corrected_links = []
            for link in links:
                # No initial ambiguous answer can be promoted to a clear gap.
                relation = "inconclusive" if gate_blocks else link.relation
                link_assistance = assistance
                if event.assistance != "assisted" and assistance == "assisted" and not any(item["kind"] == "instruction" and not item.get("neutral_check") for item in exposures):
                    disclosed = {concept for item in exposures for concept in item["disclosure_concepts"]}
                    if link.hypothesis_id not in disclosed:
                        link_assistance = event.assistance
                corrected_links.append(replace(link, relation=relation, assistance=link_assistance).to_dict())
            helped = any(self._exposes_help(item) for item in exposures)
            phase = "verification" if original["action"] and original["action"]["candidate"]["kind"] == "verification" else "after_help" if helped else "before_help"
            row = {"event": event.to_dict(), "task": task.to_dict(), "validation": validation.to_dict(),
                   "links": corrected_links, "task_findings": findings, "effective_assistance": assistance, "source_assistance": event.assistance,
                   "phase": phase, "exposures": exposures, "action": deepcopy(original["action"]),
                   "effective_annotation_id": current_annotation["event_id"] if current_annotation else None,
                   "initial": original["initial"]}
            effective.append(row)
            findings_all.extend(findings)
            links_all.extend((link, phase) for link in corrected_links)
            if original["action"]:
                candidate = original["action"]["candidate"]
                answered.append(candidate["candidate_id"])
                key = candidate["answered_content_key"]
                if key:
                    used_content_keys.append(key)
        summaries = []
        for hypothesis in self.adapter.hypothesis_ids:
            linked = [(link, phase) for link, phase in links_all if link["hypothesis_id"] == hypothesis]
            relations = {link["relation"] for link, _ in linked}
            status = self._summary_status(relations)
            partitions = {}
            for link, phase in linked:
                key = f"{phase}:{link['assistance']}"
                partition = partitions.setdefault(key, {"status": "unassessed", "evidence_ids": [], "links": []})
                partition["links"].append(link)
                if link["event_id"] not in partition["evidence_ids"]:
                    partition["evidence_ids"].append(link["event_id"])
                partition["status"] = self._summary_status({item["relation"] for item in partition["links"]})
            summaries.append(HypothesisSummary(hypothesis, status, list(dict.fromkeys(link["event_id"] for link, _ in linked)),
                                               [link for link, _ in linked], partitions).to_dict())
        state["effective_history"] = effective
        state["task_findings"] = findings_all
        state["hypothesis_summaries"] = summaries
        state["used_content_keys"] = used_content_keys
        state["answered_candidate_ids"] = answered
        state["exposures"] = self._exposures_before(len(state["deliveries"]))
        if effective:
            state["stop_requested"] = state.get("manual_stop_requested", False) or any(row["validation"]["departure"] for row in effective)
        # Save versioned recomputation without counting annotations as learners.
        signature = digest({"links": [row["links"] for row in effective],
                            "findings": [row["task_findings"] for row in effective],
                            "annotations": [row["effective_annotation_id"] for row in effective]})
        if not state["evidence_link_history"] or state["evidence_link_history"][-1]["sha256"] != signature:
            state["evidence_link_history"].append({"revision": len(state["evidence_link_history"]) + 1,
                                                   "sha256": signature, "links": [deepcopy(row["links"]) for row in effective],
                                                   "task_findings": [deepcopy(row["task_findings"]) for row in effective],
                                                   "annotation_ids": [row["effective_annotation_id"] for row in effective]})

    @staticmethod
    def _summary_status(relations):
        return "mixed" if {"supports", "challenges"} <= relations else "supported" if "supports" in relations else "challenged" if "challenges" in relations else "unassessed"

    def _trace(self, rule, reason, *, selected=None, eligible=None, rejected=None, before=None):
        inventory = self._state.get("candidate_inventory", {"eligible": [], "rejected": []})
        trace = DecisionTrace(self._state["status"], rule, self.policy["version"], reason, deepcopy(selected),
                              deepcopy(inventory["eligible"] if eligible is None else eligible),
                              deepcopy(inventory["rejected"] if rejected is None else rejected),
                              [row["event"]["event_id"] for row in self._state["effective_history"]],
                              before or self.budget(), self.budget(), deepcopy(self._state["outcome"]),
                              deepcopy(self._state["hypothesis_summaries"]), self._state["pending_operator_review"],
                              deepcopy(self._state["task_findings"]))
        self._state["last_trace"] = trace.to_dict()
        return trace

    def _close(self, status, reason, assistance="unknown"):
        if status in {"stopped_by_learner", "unsupported_task"}:
            explanation = ("Explicit stop/departure overrides every further prompt; no academic failure is inferred."
                           if status == "stopped_by_learner" else
                           "The available context has no approved task capability. Requesting already-present context cannot fix unsupported content.")
            self._record_gate_rejections("gate." + status, explanation,
                                         {"context", "probe", "instruction", "verification"}, rebuild=True)
        outcome = SessionOutcome(status, reason, [row["event"]["event_id"] for row in self._state["effective_history"]],
                                 assistance, "session-only", self._state["session_kind"]).to_dict()
        if self._state["outcome"] != outcome:
            if self._state["outcome"] is not None:
                self._state["outcome_history"].append(deepcopy(self._state["outcome"]))
            self._state["outcome"] = outcome
        self._state["status"] = "closed"
        self._state["pending_action"] = None
        self._state["pending_operator_review"] = None
        return self._trace("close." + status, reason)

    def _review(self, reason):
        self._state["status"] = "review_required"
        return self._trace("review.unresolved_cause", reason)

    def _candidate_gate(self, candidate, *, check_discrimination=False, all_candidates=None, remaining_override=None):
        remaining = self.budget()["remaining"] if remaining_override is None else remaining_override
        context = self._state["task"]["context_status"]
        if self._state["stop_requested"]:
            return "Explicit stop/departure blocks all further learner prompts."
        if context == "unsupported_task":
            return "Unsupported task: the available context has no approved adapter/validator capability."
        if context in {"missing_context", "ambiguous_wording"} and candidate.kind != "context":
            return self._context_gate_reason()
        if not candidate.available:
            return candidate.reason or "Adapter has not validated the requirements for this authored action."
        if remaining["total"] < 1:
            return "No issued-prompt budget remains."
        if candidate.kind in {"context", "probe"} and remaining["information"] < 1:
            return "The combined context/restatement/probe allowance is exhausted."
        if candidate.kind == "instruction" and remaining["instruction"] < 1:
            return "The instructional prompt allowance is exhausted."
        if candidate.kind == "verification" and remaining["verification"] < 1:
            return "Only one verification attempt is allowed."
        prior_ids = [action["candidate"]["candidate_id"] for action in self._state["actions"]]
        repeat_reason = candidate.requirements.get("repeat_reason")
        if (candidate.candidate_id in prior_ids or candidate.answered_content_key and candidate.answered_content_key in self._state["used_content_keys"]) and not repeat_reason:
            return "This authored prompt/content has already been issued or answered; no explicit repeat rationale is present."
        if candidate.kind == "probe":
            if remaining["total"] < 3 or remaining["instruction"] < 1 or remaining["verification"] < 1:
                return "Probe rejected: one resulting instruction and one verification must still fit."
            if candidate.requirements.get("reveals_verification_answer"):
                return "This probe would disclose the intended verification answer."
        if candidate.kind == "verification":
            exposed_ids = {item["task_id"] for item in self._state["exposures"]}
            exposed_keys = {item["exposure_key"] for item in self._state["exposures"]}
            key = candidate.requirements.get("exposure_key", candidate.task.parameters.get("exposure_key", candidate.task.task_id))
            if candidate.task.task_id in exposed_ids or key in exposed_keys:
                return "Verification must use a different task not already shown in this session."
            if candidate.disclosure_concepts:
                return "Verification cannot disclose the exchange rule or provide corrective help."
        if candidate.requirements.get("missing_fields"):
            return "Necessary domain fields are missing: " + ", ".join(candidate.requirements["missing_fields"])
        if check_discrimination and candidate.kind == "probe":
            projected = self._projected_branches(candidate, all_candidates or [])
            next_actions = {branch.get("next_action") for branch in projected.values() if branch["feasible_after_probe"]}
            if len(next_actions) < 2:
                return "Documented response categories do not lead to two distinct feasible next actions after this probe consumes its prompt/information slot."
        return None

    def _context_gate_reason(self):
        task = self._state["task"]
        if task["context_status"] == "ambiguous_wording":
            return "Context gate ambiguous_wording: disambiguate the requested representation before any content probe, instruction, or verification."
        missing = ", ".join(task.get("missing_fields", [])) or "the required local task fields"
        return "Context gate missing_context: establish " + missing + " before any content probe, instruction, or verification."

    def _record_gate_rejections(self, rule, reason, blocked_kinds, *, rebuild=False):
        """Explain unavailable action families even when no content is supplied.

        Wildcards describe rejected action kinds; they are not authored prompts
        or learner-visible content invented for an unsupported task.
        """
        if rebuild:
            self._candidate_inventory()
        inventory = self._state["candidate_inventory"]
        eligible, rejected = [], []
        for item in inventory["eligible"] + inventory["rejected"]:
            item = deepcopy(item)
            if item["kind"] in blocked_kinds:
                item["prior_gate_reason"] = item["reason"]
                item["reason"] = reason
                item["gate_rule"] = rule
                rejected.append(item)
            elif any(previous["candidate_id"] == item["candidate_id"] for previous in inventory["eligible"]):
                eligible.append(item)
            else:
                rejected.append(item)
        represented = {item["kind"] for item in rejected}
        for kind in sorted(blocked_kinds - represented):
            rejected.append({"candidate_id": kind + ":*", "kind": kind, "reason": reason,
                             "gate_rule": rule, "scope": "action_kind_summary",
                             "target_hypotheses": [], "response_branches": {}, "human_review_status": "not_applicable"})
        self._state["candidate_inventory"] = {"eligible": eligible, "rejected": rejected}
        return eligible, rejected

    def _projected_branches(self, probe, candidates):
        """Project resources and declared conditions, not hypothetical learner data."""
        projected = deepcopy(self.budget()["remaining"])
        projected["total"] -= 1
        projected["information"] -= 1
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        result = {}
        for category, branch in probe.response_branches.items():
            item = deepcopy(branch)
            if branch.get("route") and not branch.get("next_action"):
                item["feasible_after_probe"] = False
                item["execution_state"] = "superseded_by_" + branch["route"]
                item["feasibility_reason"] = "The declared guard handles this response without another authored action."
                result[category] = item
                continue
            next_candidate = by_id.get(branch.get("next_action"))
            why = "The declared branch action is absent from the adapter inventory." if next_candidate is None else None
            if next_candidate is not None:
                why = self._candidate_gate(next_candidate, remaining_override=projected)
                if next_candidate.candidate_id == probe.candidate_id:
                    why = "A response cannot send the learner back to the same just-answered probe."
                if next_candidate.kind == "instruction" and (projected["total"] < 2 or projected["verification"] < 1):
                    why = "This branch cannot fit its instruction followed by the reserved verification."
                required = branch.get("required_finding")
                if required and required not in next_candidate.requirements.get("task_finding_codes", []):
                    why = "The declared task finding is not a precondition of the proposed cue."
            item["feasible_after_probe"] = why is None
            item["execution_state"] = "conditional" if why is None else "blocked"
            item["feasibility_reason"] = why or "Resources and declared cue preconditions permit this route; the actual parsed response must establish its required finding/evidence."
            if next_candidate is not None and next_candidate.kind == "instruction":
                item["evidence_condition"] = ("The same event must carry validated task finding " + branch["required_finding"] + "; this justifies task-level assistance, not a learner diagnosis."
                    if branch.get("required_finding") else "Actual relevant evidence is required; resources alone cannot unlock a cue.")
            result[category] = item
        return result

    def _candidate_inventory(self):
        task = TaskContract.from_dict(self._state["task"])
        candidates = self.adapter.candidate_actions(task, self.state)
        if any(type(candidate) is not ActionCandidate for candidate in candidates):
            raise TypeError("Domain actions must be typed ActionCandidate objects.")
        if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
            raise ValueError("Candidate IDs must be unique in a decision inventory.")
        eligible, rejected, normalized_candidates = [], [], []
        for candidate in candidates:
            _assert_no_answer_keys(candidate.to_dict())
            why = self._candidate_gate(candidate, check_discrimination=True, all_candidates=candidates)
            if candidate.kind == "probe":
                candidate = replace(candidate, response_branches=self._projected_branches(candidate, candidates))
            normalized_candidates.append(candidate)
            item = {"candidate_id": candidate.candidate_id, "kind": candidate.kind, "reason": why or "Domain and budget requirements met.",
                    "target_hypotheses": candidate.target_hypotheses, "response_branches": candidate.response_branches,
                    "human_review_status": candidate.human_review_status}
            (rejected if why else eligible).append(item)
        usable = [candidate for candidate in normalized_candidates if any(item["candidate_id"] == candidate.candidate_id for item in eligible)]
        self._state["candidate_inventory"] = {"eligible": deepcopy(eligible), "rejected": deepcopy(rejected)}
        return candidates, usable, eligible, rejected

    def _issue(self, candidate, rule, reason, eligible, rejected, *, strategy_change=False):
        before = self.budget()
        sequence = len(self._state["actions"]) + 1
        action = {"action_id": f"{self._state['session_id']}:action:{sequence}", "sequence": sequence,
                  "candidate": candidate.to_dict(), "text": candidate.text, "kind": candidate.kind,
                  "task_id": candidate.task.task_id, "task_version": candidate.task.version,
                  "rule_id": rule, "strategy_change": strategy_change, "delivery_status": "not_delivered"}
        self._state["actions"].append(deepcopy(action))
        self._state["pending_action"] = action
        self._state["status"] = "awaiting_delivery"
        self._state["strategy_changes"] = self.budget()["used"]["strategy_change"]
        return self._trace(rule, reason, selected=action, eligible=eligible, rejected=rejected, before=before)

    def _last_performance(self):
        relevant = [row for row in self._state["effective_history"] if not row["validation"]["operator_required"]]
        return relevant[-1] if relevant else None

    def _verified_failures(self):
        return [row for row in self._state["effective_history"]
                if row["action"] and row["action"]["candidate"]["kind"] == "instruction"
                and row["validation"]["full_representation"] and not row["validation"]["operator_required"]
                and (row["validation"]["correctness"] == "incorrect" or row["validation"]["reasoning"] == "inconsistent")]

    @staticmethod
    def _matching_findings(row, candidate):
        if row is None or row["validation"]["operator_required"]:
            return []
        codes = set(candidate.requirements.get("task_finding_codes", []))
        return [finding for finding in row.get("task_findings", [])
                if finding["code"] in codes
                and finding["classification"] in {"validated_error", "incomplete"}
                and finding["event_id"] == row["event"]["event_id"]]

    @staticmethod
    def _full_success(row):
        return (row is not None and row["validation"]["full_representation"] and row["validation"]["correctness"] == "correct"
                and row["validation"]["reasoning"] == "consistent" and not row["validation"]["operator_required"])

    def propose(self):
        state = self._state
        if state["status"] == "closed":
            return self._trace("guard.closed", "Episode remains closed; outcomes and issued budgets are retained.")
        if state["stop_requested"]:
            return self._close("stopped_by_learner", "An explicit observed departure overrides academic interpretation.")
        if state["pending_operator_review"]:
            state["status"] = "operator_review_pending"
            return self._trace("guard.operator_review", "Preserve the raw response and await documented operator categorization; no prompt is consumed.")
        if state["pending_action"]:
            pending = state["pending_action"]
            return self._trace("guard.pending", "Keep the same pending prompt; rerendering neither delivers it nor creates learner evidence.", selected=pending)
        task = TaskContract.from_dict(state["task"])
        if task.context_status == "unsupported_task":
            return self._close("unsupported_task", task.reason or "Context is available, but no approved domain capability applies.")
        candidates, usable, eligible, rejected = self._candidate_inventory()
        if task.context_status in {"missing_context", "ambiguous_wording"}:
            eligible, rejected = self._record_gate_rejections("context." + task.context_status,
                                                              self._context_gate_reason(),
                                                              {"probe", "instruction", "verification"})
            options = [candidate for candidate in usable if candidate.kind == "context"]
            if options:
                chosen = min(options, key=lambda item: (item.priority, item.candidate_id))
                return self._issue(chosen, "context." + task.context_status,
                                   "Request only the missing field or disambiguate units; do not mark an ambiguous reply wrong.", eligible, rejected)
            return self._close("missing_context_unresolved", "The single relevant context/restatement path did not establish a supported local task.")
        last = self._last_performance()
        if self._full_success(last):
            if last["effective_assistance"] == "unassisted":
                return self._close("local_unassisted_success", "A complete correct local response with consistent reasoning and no recorded help was observed.", "unassisted")
            verification = [candidate for candidate in usable if candidate.kind == "verification"]
            if verification:
                return self._issue(min(verification, key=lambda item: (item.priority, item.candidate_id)), "performance.fresh_verification",
                                   "Local success after help or with unknown assistance requires one fresh session-unseen unassisted full task.", eligible, rejected)
            return self._close("assisted_success_only" if last["effective_assistance"] == "assisted" else "answer_correct_reasoning_unverified",
                               "A complete local response is present, but no eligible fresh verification can be issued; independence is unconfirmed.", last["effective_assistance"])
        if len(self._verified_failures()) >= self.policy["post_instruction_failure_cap"]:
            return self._close("repeated_failure", "Two mathematically verified post-instruction failures require human review; unclear responses were not counted.")
        if self.budget()["remaining"]["total"] == 0:
            if last and last["validation"]["correctness"] == "correct":
                return self._close("answer_correct_reasoning_unverified", "The answer is correct but the full explanation/independence criterion was not verified before the prompt budget ended.")
            return self._close("budget_exhausted", "The six-prompt root budget ended without a successful verification.")
        instructions = [candidate for candidate in usable if candidate.kind == "instruction"]
        summaries = {item["hypothesis_id"]: item for item in state["hypothesis_summaries"]}
        clear_targets = {name for name, summary in summaries.items() if summary["status"] == "supported"
                         and any(link["relation"] == "supports" and link["strength"] == "reasoning_supported" for link in summary["links"])}
        failure = self._verified_failures()
        if failure:
            if self.budget()["remaining"]["strategy_change"] < 1:
                return self._review("The single evidence-justified strategy change has been used; no mechanical hint rotation is allowed.")
            recent_targets = {link["hypothesis_id"] for link in failure[-1]["links"] if link["relation"] == "supports"}
            options = [candidate for candidate in instructions if recent_targets & set(candidate.target_hypotheses)
                       or self._matching_findings(failure[-1], candidate)]
            if options:
                selected = self._order(options)[0]
                findings = self._matching_findings(failure[-1], selected)
                basis = ("validated task finding " + ", ".join(f["code"] for f in findings)
                         if findings else "qualitative evidence for " + ", ".join(sorted(recent_targets & set(selected.target_hypotheses))))
                return self._issue(selected, "instruction.evidence_justified_change",
                                   "One unused alternative addresses " + basis + " after verified failure; no hidden cause is asserted by a task finding.",
                                   eligible, rejected, strategy_change=True)
        if state["policy_mode"] == "fixed" and self.budget()["used"]["instruction"] == 0:
            fixed_id = getattr(self.adapter, "fixed_instruction_id", None)
            options = [candidate for candidate in instructions if candidate.candidate_id == fixed_id]
            if options:
                return self._issue(options[0], "comparator.fixed_prerequisite", "Authored fixed comparator: prerequisite explanation plus attempt under the identical context and budget gates.", eligible, rejected)
        direct = [candidate for candidate in instructions if clear_targets & set(candidate.target_hypotheses)]
        if direct and self.budget()["used"]["instruction"] == 0:
            return self._issue(self._order(direct)[0], "instruction.reasoning_supported_target", "A reasoning-supported target permits reviewed/provisionally validated instruction without an unnecessary probe.", eligible, rejected)
        if last and last["action"] and last["action"]["candidate"]["kind"] == "probe":
            branch = last["action"]["candidate"]["response_branches"].get(last["validation"].get("category"))
            if branch and branch.get("feasible_after_probe", True):
                next_candidate = next((candidate for candidate in usable if candidate.candidate_id == branch.get("next_action")), None)
                if next_candidate is not None:
                    if next_candidate.kind == "instruction":
                        supported_here = {link["hypothesis_id"] for link in last["links"] if link["relation"] == "supports"}
                        targets = supported_here & set(next_candidate.target_hypotheses)
                        if targets and all(summaries[target]["status"] != "mixed" for target in targets) and self.budget()["used"]["instruction"] == 0:
                            return self._issue(next_candidate, "instruction.provisional_probe_response",
                                               "The actual response category supports this provisional task-specific intervention. Response-only evidence is weak and does not confirm a hidden misconception.", eligible, rejected)
                        required = branch.get("required_finding")
                        matching = [f for f in self._matching_findings(last, next_candidate) if f["code"] == required] if required else []
                        if matching and self.budget()["used"]["instruction"] == 0:
                            finding = matching[0]
                            return self._issue(next_candidate, "instruction.observed_task_finding",
                                               f"Validated task finding {finding['code']} from event {finding['event_id']} on task {finding['task_id']} justifies this procedural cue; it does not establish a learner misconception.",
                                               eligible, rejected)
                    elif next_candidate.kind == "probe":
                        return self._issue(next_candidate, "probe.observed_response_branch",
                                           "The actual parsed response follows the declared feasible content-probe branch; no response or likelihood was invented.", eligible, rejected)
        if last and self.budget()["used"]["instruction"] == 0:
            grounded = [candidate for candidate in instructions if self._matching_findings(last, candidate)]
            if grounded:
                selected = self._order(grounded)[0]
                finding = self._matching_findings(last, selected)[0]
                return self._issue(selected, "instruction.observed_task_finding",
                                   f"Validated task finding {finding['code']} from event {finding['event_id']} on task {finding['task_id']} justifies task-level assistance before another probe; the learner's underlying cause remains uncertain.",
                                   eligible, rejected)
        probes = [candidate for candidate in usable if candidate.kind == "probe"]
        numerical_success = bool(last and last["validation"]["correctness"] == "correct" and last["validation"]["reasoning"] != "consistent")
        if numerical_success:
            reasoning = [candidate for candidate in probes if candidate.reasoning_request]
            if reasoning:
                return self._issue(self._order(reasoning)[0], "probe.reasoning_after_answer", "Numerical success alone does not establish understanding; request interpretable reasoning once.", eligible, rejected)
        if probes:
            return self._issue(self._order(probes)[0], "probe.earliest_unresolved_prerequisite", "Use the adapter's earliest unresolved prerequisite among discriminating, nonrepeated, budget-feasible content probes.", eligible, rejected)
        if numerical_success:
            return self._close("answer_correct_reasoning_unverified", "A correct answer is preserved, but no eligible reasoning request remains; no understanding claim is made.")
        return self._review("No nonrepeated discriminating probe or reasoning-supported instructional target is available. Unknown cause remains unresolved; human review is required.")

    def _order(self, candidates):
        ranks = {name: i for i, name in enumerate(self.adapter.prerequisite_order)}
        return sorted(candidates, key=lambda item: (item.priority, min([ranks.get(name, len(ranks)) for name in item.target_hypotheses] or [len(ranks)]), item.candidate_id))

    def record_delivery(self, action_id: str, source: str | dict, received_time: str):
        if not source or not isinstance(received_time, str) or not received_time:
            raise ValueError("Actual delivery needs explicit source provenance and received time.")
        known = next((item for item in self._state["deliveries"] if item["action_id"] == action_id), None)
        if known is not None:
            return self._trace("delivery.idempotent", "This action's actual delivery is already recorded; no prompt or evidence is added.", selected=self._state["pending_action"])
        pending = self._state["pending_action"]
        if self._state["status"] == "closed" or pending is None or pending["action_id"] != action_id:
            raise ValueError("Delivery must identify the current unclosed pending prompt.")
        self._state["deliveries"].append({"action_id": action_id, "source": deepcopy(source), "received_time": received_time,
                                           "content_version": pending["candidate"]["content_version"], "text_hash": digest(pending["text"])})
        pending["delivery_status"] = "delivered"
        self._state["status"] = "awaiting_real_observation"
        self._recompute()
        return self._trace("delivery.recorded", "Actual delivery was explicitly recorded; a learner response is still required.", selected=pending)

    def observe(self, event: ObservationEvent):
        self._validate_event_type(event)
        prior = self._state["event_hashes"].get(event.event_id)
        if prior is not None:
            if prior != digest(event.to_dict()):
                raise ValueError("An existing event identity cannot be changed; append an operator supersession instead.")
            # Strict idempotence: return the saved trace without mutating state.
            return DecisionTrace.from_dict(self._state["last_trace"]) if self._state["last_trace"] else self.propose()
        if self._state["status"] == "closed":
            raise ValueError("A closed episode cannot be resumed by adding another attempt.")
        pending = self._state["pending_action"]
        if pending is None or event.action_id != pending["action_id"]:
            raise ValueError("Actual response must link to the current pending action.")
        if self._state["pending_operator_review"]:
            raise ValueError("Resolve the existing raw response's operator review before adding another attempt.")
        if not any(delivery["action_id"] == event.action_id for delivery in self._state["deliveries"]):
            raise ValueError("Record actual delivery separately before accepting a learner response.")
        task = TaskContract.from_dict(pending["candidate"]["task"])
        if event.task_id != task.task_id or event.task_version != task.version:
            raise ValueError("Response must bind the exact delivered task identity and version.")
        if event.source_kind not in {"actual_manual_learner_response", "software_fixture"}:
            raise ValueError("Recorded excerpt continuations cannot be replayed as responses to a newly authored action.")
        validation = self.adapter.validate(event, task, deepcopy(self._state["exposures"]))
        self._append_observation(event, task, validation, pending)
        self._recompute()
        return self._after_response(event.event_id)

    def _after_response(self, event_id):
        row = next(item for item in self._state["effective_history"] if item["event"]["event_id"] == event_id)
        validation = ValidationResult.from_dict(row["validation"])
        if validation.departure:
            return self._close("stopped_by_learner", "The actual response explicitly requests departure; it is not academic failure.")
        if row["action"] and row["action"]["candidate"]["kind"] == "verification":
            # Even an unparseable verification ends the learner episode.
            # An operator may later append a categorization without a new prompt.
            self._state["pending_action"] = None
            self._state["pending_operator_review"] = None
            return self._verification_outcome(row)
        if validation.operator_required:
            self._state["pending_operator_review"] = event_id
            self._state["status"] = "operator_review_pending"
            return self._trace("response.operator_review", "Unrestricted/unparseable response retained verbatim. Operator categorization and rationale are needed; no additional prompt is issued.", selected=self._state["pending_action"])
        self._state["pending_operator_review"] = None
        self._state["pending_action"] = None
        self._state["status"] = "active"
        updated = self.adapter.context_after(TaskContract.from_dict(row["task"]), ObservationEvent.from_dict(row["event"]), validation)
        if type(updated) is not TaskContract:
            raise TypeError("Domain context updates must return a typed TaskContract.")
        _assert_no_answer_keys(updated.to_dict())
        self._state["task"] = updated.to_dict()
        return self.propose()

    def _verification_outcome(self, row):
        validation = row["validation"]
        assistance = row["effective_assistance"]
        if self._full_success(row) and assistance == "unassisted":
            return self._close("local_unassisted_success", "One different session-unseen task was completed correctly with consistent reasoning and explicitly recorded absence of help.", assistance)
        if self._full_success(row) and assistance == "assisted":
            return self._close("assisted_success_only", "The verification response was correct but outside/current-task help was recorded; independence is not established.", assistance)
        if validation["correctness"] == "incorrect" or validation["reasoning"] == "inconsistent":
            return self._close("verification_incorrect", "The single verification attempt failed exact task or reasoning requirements; it is not converted into more instruction.", assistance)
        if validation["correctness"] == "correct" and (validation["reasoning"] != "consistent" or assistance == "unknown"):
            return self._close("answer_correct_reasoning_unverified", "The verification answer is correct, but reasoning or assistance uncertainty prevents the full criterion.", assistance)
        return self._close("verification_unresolved", "The single verification remains partial, ambiguous, or unresolved; no additional learner prompt is issued.", assistance)

    def annotate(self, target_event_id: str, category: str, parsed_fields: dict, rationale: str,
                 operator_id: str, annotation_id: str, received_time: str, supersedes: str | None = None):
        if not all(isinstance(value, str) and value.strip() for value in (target_event_id, category, rationale, operator_id, annotation_id, received_time)):
            raise ValueError("Operator annotation requires category, rationale, operator identity, event identity and time.")
        original = next((item for item in self._state["events"] if item["event"]["event_id"] == target_event_id), None)
        if original is None:
            raise ValueError("Operator annotations must target an existing actual observation.")
        prior_annotations = [item for item in self._state["annotations"] if item["target_event_id"] == target_event_id]
        existing = next((item for item in self._state["annotations"] if item["event_id"] == annotation_id), None)
        payload = {"target_event_id": target_event_id, "category": category, "parsed_fields": deepcopy(parsed_fields),
                   "rationale": rationale, "operator_id": operator_id, "supersedes": supersedes}
        source = ObservationEvent.from_dict(original["event"])
        annotation_event = ObservationEvent(annotation_id, self._state["session_id"], source.task_id, source.task_version,
                                            source.action_id, "operator_annotation", rationale, source.source_spans,
                                            digest(payload), received_time, source.assistance,
                                            {"operator_id": operator_id, "target_event_id": target_event_id, "supersedes": supersedes}, payload)
        if existing is not None:
            if existing["event"] != annotation_event.to_dict():
                raise ValueError("An annotation ID is immutable; append a superseding annotation.")
            return DecisionTrace.from_dict(self._state["last_trace"])
        if annotation_id in self._state["event_hashes"]:
            raise ValueError("Annotation identity is already used by another source event.")
        previous_id = prior_annotations[-1]["event_id"] if prior_annotations else None
        if supersedes != previous_id:
            raise ValueError("A correction must explicitly supersede the latest annotation for this observation.")
        validation = self.adapter.operator_validate(source, TaskContract.from_dict(original["task"]), category, deepcopy(parsed_fields), rationale)
        if type(validation) is not ValidationResult:
            raise TypeError("Operator parsing must still return a typed domain ValidationResult.")
        self._state["annotations"].append({"event_id": annotation_id, "target_event_id": target_event_id, "event": annotation_event.to_dict(),
                                            "validation": validation.to_dict(), "supersedes": supersedes})
        self._state["event_hashes"][annotation_id] = digest(annotation_event.to_dict())
        was_closed = self._state["status"] == "closed"
        was_pending_review = self._state["pending_operator_review"] == target_event_id
        self._recompute()
        row = next(item for item in self._state["effective_history"] if item["event"]["event_id"] == target_event_id)
        if was_closed:
            if self._state["stop_requested"]:
                return self._close("stopped_by_learner", "Explicit departure/stop remains controlling after appended operator review.")
            if row["action"] and row["action"]["candidate"]["kind"] == "verification":
                return self._verification_outcome(row)
            return self._trace("annotation.closed_recomputed", "Superseding judgment appended and temporal evidence recomputed; the historical closure and spent prompts remain recorded.")
        if was_pending_review:
            return self._after_response(target_event_id)
        return self.propose()

    def stop(self):
        if self._state["status"] == "closed":
            return self._trace("guard.closed", "The closed episode and its budget are preserved.")
        self._state["stop_requested"] = True
        self._state["manual_stop_requested"] = True
        return self._close("stopped_by_learner", "An explicit stop was recorded; no academic failure is inferred.")
