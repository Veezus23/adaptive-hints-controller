"""Versioned subject-independent public contracts. No answer keys or probabilities."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping, Protocol

CONTEXT_STATUSES = ("supported_local_task", "missing_context", "ambiguous_wording", "unsupported_task")
OUTCOME_STATUSES = (
    "local_unassisted_success", "answer_correct_reasoning_unverified", "assisted_success_only",
    "verification_incorrect", "verification_unresolved", "stopped_by_learner",
    "missing_context_unresolved", "unsupported_task", "repeated_failure", "budget_exhausted",
)


class JsonContract:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        allowed = {item.name for item in fields(cls)}
        unexpected = set(value) - allowed
        if unexpected:
            raise ValueError(f"Unexpected {cls.__name__} fields: {sorted(unexpected)}")
        return cls(**dict(value))


@dataclass(frozen=True)
class TaskContract(JsonContract):
    task_id: str
    version: str
    family: str
    context_status: str
    prompt: str
    parameters: dict[str, Any] = field(default_factory=dict)
    source_spans: list[dict[str, Any]] = field(default_factory=list)
    source_hash: str = ""
    missing_fields: list[str] = field(default_factory=list)
    reason: str = ""
    freshness: str = "session-only"

    def __post_init__(self):
        if self.context_status not in CONTEXT_STATUSES or not self.task_id or not self.version or not self.family:
            raise ValueError("Task needs an identity, version, family, and exact supported context status.")


@dataclass(frozen=True)
class ObservationEvent(JsonContract):
    event_id: str
    session_id: str
    task_id: str
    task_version: str
    action_id: str | None
    source_kind: str
    raw_text: str
    source_spans: list[dict[str, Any]] = field(default_factory=list)
    source_hash: str = ""
    received_time: str = ""
    assistance: str = "unknown"
    parser_provenance: dict[str, Any] = field(default_factory=dict)
    structured_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.source_kind not in {"recorded_excerpt", "actual_manual_learner_response", "operator_annotation", "software_fixture"}:
            raise ValueError("Unknown observation source kind.")
        if self.assistance not in {"unassisted", "assisted", "unknown"}:
            raise ValueError("Assistance must be explicitly unassisted, assisted, or unknown.")
        if not self.event_id or not self.session_id or not self.task_id or not self.task_version or not isinstance(self.raw_text, str):
            raise ValueError("Events need stable session/task identities and verbatim response text.")


@dataclass(frozen=True)
class ValidationResult(JsonContract):
    correctness: str
    reasoning: str
    parsed_fields: dict[str, Any] = field(default_factory=dict)
    validator_id: str = "unvalidated"
    validator_version: str = "1"
    provenance: dict[str, Any] | str = field(default_factory=dict)
    full_representation: bool = False
    operator_required: bool = False
    departure: bool = False
    ambiguity_reason: str | None = None
    category: str | None = None

    def __post_init__(self):
        if self.correctness not in {"correct", "incorrect", "partial", "unparseable", "ambiguous"}:
            raise ValueError("Unknown mathematical correctness category.")
        if self.reasoning not in {"consistent", "inconsistent", "absent", "unresolved"}:
            raise ValueError("Unknown reasoning category.")


@dataclass(frozen=True)
class EvidenceLink(JsonContract):
    hypothesis_id: str
    event_id: str
    rule_id: str
    rule_version: str
    relation: str
    strength: str
    assistance: str
    rationale: str

    def __post_init__(self):
        if self.relation not in {"supports", "challenges", "inconclusive"}:
            raise ValueError("Unknown evidence relation.")
        if self.strength not in {"response_only", "reasoning_supported"}:
            raise ValueError("Unknown evidence strength.")
        if self.assistance not in {"unassisted", "assisted", "unknown"}:
            raise ValueError("Evidence must preserve assistance uncertainty.")


@dataclass(frozen=True)
class TaskFinding(JsonContract):
    """Validated statement about this response, without a learner-state claim."""
    code: str
    classification: str
    event_id: str
    task_id: str
    task_version: str
    validation_category: str
    validator_id: str
    validator_version: str
    validation_provenance: dict[str, Any] | str
    assistance: str
    rationale: str
    source_hash: str = ""
    source_kind: str = ""
    action_id: str | None = None
    received_time: str = ""
    annotation_id: str | None = None

    def __post_init__(self):
        if self.classification not in {"validated_error", "incomplete", "ambiguous", "reasoning_issue", "observed_success"}:
            raise ValueError("A task finding must describe the observed response category.")
        if self.assistance not in {"unassisted", "assisted", "unknown"}:
            raise ValueError("Task findings preserve assistance uncertainty.")
        if not all((self.code, self.event_id, self.task_id, self.task_version,
                    self.validation_category, self.validator_id, self.validator_version, self.rationale)):
            raise ValueError("Task findings need a source event, task, validator and rationale.")


@dataclass(frozen=True)
class HypothesisSummary(JsonContract):
    hypothesis_id: str
    status: str
    evidence_ids: list[str] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    partitions: dict[str, Any] = field(default_factory=dict)
    rationale: str = "Qualitative evidence aggregation; no probability or latent-state certainty."


@dataclass(frozen=True)
class ActionCandidate(JsonContract):
    candidate_id: str
    kind: str
    text: str
    task: TaskContract
    requirements: dict[str, Any] = field(default_factory=dict)
    target_hypotheses: list[str] = field(default_factory=list)
    response_branches: dict[str, dict[str, Any]] = field(default_factory=dict)
    disclosure_concepts: list[str] = field(default_factory=list)
    content_version: str = "1"
    provenance: dict[str, Any] | str = field(default_factory=dict)
    human_review_status: str = "pending_review"
    available: bool = True
    reason: str = ""
    priority: int = 100
    answered_content_key: str = ""
    response_format: str = ""
    reasoning_request: bool = False

    def __post_init__(self):
        if self.kind not in {"context", "probe", "instruction", "verification"}:
            raise ValueError("Action kind must be context, probe, instruction, or verification.")
        if not isinstance(self.task, TaskContract):
            raise TypeError("Action candidates require a typed public TaskContract.")
        if not self.candidate_id or not self.text or not self.content_version:
            raise ValueError("Action needs an identity, version, and authored prompt.")

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value["task"] = TaskContract.from_dict(value["task"])
        return super().from_dict(value)


@dataclass(frozen=True)
class SessionOutcome(JsonContract):
    status: str
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    assistance: str = "unknown"
    freshness: str = "session-only"
    session_kind: str = "debugging"
    claim_boundary: str = "Local observed performance only; no calibrated confidence, latent mastery, or learning-gain claim."

    def __post_init__(self):
        if self.status not in OUTCOME_STATUSES:
            raise ValueError("Outcome must use a specification-defined status.")


@dataclass(frozen=True)
class DecisionTrace(JsonContract):
    status: str
    rule_id: str
    rule_version: str
    reason: str
    selected: dict[str, Any] | None
    eligible_candidates: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    budget_before: dict[str, Any] = field(default_factory=dict)
    budget_after: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] | None = None
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    pending_operator_review: str | None = None
    task_findings: list[dict[str, Any]] = field(default_factory=list)


class DomainAdapter(Protocol):
    hypothesis_ids: list[str]
    prerequisite_order: list[str]

    def candidate_actions(self, task: TaskContract, state: dict[str, Any]) -> list[ActionCandidate]: ...
    def validate(self, event: ObservationEvent, task: TaskContract, assistance_history: list[dict[str, Any]]) -> ValidationResult: ...
    def map_evidence(self, event: ObservationEvent, validation: ValidationResult, task: TaskContract, history: list[dict[str, Any]]) -> list[EvidenceLink]: ...
    def map_task_findings(self, event: ObservationEvent, validation: ValidationResult, task: TaskContract, history: list[dict[str, Any]]) -> list[TaskFinding]: ...
    def context_after(self, task: TaskContract, event: ObservationEvent, validation: ValidationResult) -> TaskContract: ...
    def operator_validate(self, event: ObservationEvent, task: TaskContract, category: str, parsed_fields: dict[str, Any], rationale: str) -> ValidationResult: ...
