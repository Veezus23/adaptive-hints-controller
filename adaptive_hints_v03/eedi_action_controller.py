"""Shared, subject-agnostic action policy over cited structured evidence.

This policy knows no Eedi question, topic, mathematical validator, or preferred
action annotation. It chooses an action kind; authored prompts remain separate.
"""
from __future__ import annotations

CONTEXT = {"sufficient", "missing", "unknown"}
PERFORMANCE = {"correct", "incorrect", "partial", "no_attempt", "unknown"}
EXPLANATIONS = {"multiple", "single", "unknown"}
ASSISTANCE = {"none_visible", "prompt_only", "strategy_given", "worked_step_given", "unknown"}
SUBSTANTIVE_ASSISTANCE = {"strategy_given", "worked_step_given"}


def select_action(evidence: dict) -> dict:
    """Return a deterministic action kind and inspectable ordered-rule trace."""
    expected = {"context", "performance", "competing_explanations", "prior_assistance"}
    if set(evidence) != expected:
        raise ValueError("Controller requires exactly four structured evidence fields.")
    for name, allowed in (("context", CONTEXT), ("performance", PERFORMANCE),
                          ("competing_explanations", EXPLANATIONS), ("prior_assistance", ASSISTANCE)):
        field = evidence[name]
        if not isinstance(field, dict) or field.get("value") not in allowed:
            raise ValueError(f"Invalid {name} evidence value.")
        if not isinstance(field.get("citations"), list) or not isinstance(field.get("note"), str):
            raise ValueError(f"{name} needs citations and an evidence note.")
    context = evidence["context"]["value"]
    performance = evidence["performance"]["value"]
    explanations = evidence["competing_explanations"]["value"]
    assistance = evidence["prior_assistance"]["value"]
    substantive = assistance in SUBSTANTIVE_ASSISTANCE
    rules = [
        ("R1_CONTEXT", "clarification", context != "sufficient",
         "A verified local step lacks sufficient visible context; ask what is missing."),
        ("R2_CORRECT", "verification", performance == "correct" and explanations != "multiple",
         "A visible local step is correct with no unresolved competing explanations; request an unassisted check before inferring understanding."
         if substantive else "A visible local step is correct with no unresolved competing explanations; verify the next unassisted step."),
        ("R3_COMPETING", "diagnostic_probe", explanations == "multiple",
         "Several explanations remain; ask a probe whose answer could change the intervention."),
        ("R4_REPEATED_FAILURE", "strategy_cue", performance == "incorrect" and substantive,
         "An incorrect step follows substantive visible assistance; change strategy rather than repeat it."),
        ("R5_ERROR_OR_PARTIAL", "diagnostic_probe", performance in {"incorrect", "partial"},
         "An incorrect or partial step needs a focused probe before a specific hint."),
        ("R6_STALLED_AFTER_HELP", "strategy_cue",
         performance in {"no_attempt", "unknown"} and substantive,
         "No verified step follows substantive help; offer a different strategy cue."),
        ("R7_SUFFICIENT_NO_STEP", "strategy_cue", context == "sufficient",
         "Context supports a local move but no earlier rule establishes a different action."),
    ]
    trace = [{"priority": index, "rule": code, "action": action, "triggered": triggered,
              "reason": reason} for index, (code, action, triggered, reason) in enumerate(rules, 1)]
    selected = next((row for row in trace if row["triggered"]), None)
    if selected is None:
        raise AssertionError("The controller policy is not total.")
    for row in trace:
        row["disposition"] = ("selected" if row is selected else
                              "overridden_by_higher_priority" if row["triggered"] else "predicate_false")
    return {
        "action": selected["action"],
        "selected_rule": selected["rule"],
        "selection_reason": selected["reason"],
        "rule_trace": trace,
        "evaluated_evidence_values": {name: evidence[name]["value"] for name in
                                      ("context", "performance", "competing_explanations", "prior_assistance")},
        "assistance_is_substantive": substantive,
        "policy_version": "eedi-shared-action-policy-v1",
    }
