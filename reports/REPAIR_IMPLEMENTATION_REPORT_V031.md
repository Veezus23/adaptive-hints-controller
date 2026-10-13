# v0.3.1 repair and verification

26 September 2026. **Software behavior verified; learner effectiveness untested.** The repair allows a relevant task-level cue when the validated response shows an interpretable error, without treating that error as proof of a hidden learner difficulty.

## Reproduced defect and corrected path

The original v0.3 code was run before editing. A newly added regression test failed there because the response to `P_FULL` selected `P_REASON`, not the declared `H_APPLY`. The exact typed software fixture was: 346; correct equivalence answer; then `{hundreds: 3, tens: 4, ones: 6}` with conservation terms `[300, 40, 6]`. The representation keeps the value 346 but does not exchange one hundred for ten tens. The validator returned `exchange_incorrect`; evidence left `application_gap` inconclusive, so the engine blocked `H_APPLY`. Repeating the representation after `P_REASON` led to `review_required` after three information prompts and no instruction.

| Isolated software-debugging stage | v0.3 action and budget | v0.3.1 action and budget |
|---|---|---|
| Correct `P_EQ` response | `P_FULL`; total 2, information 2 | `P_FULL`; total 2, information 2 |
| Value-preserving but unchanged complete representation | `P_REASON`; total 3, information 3, instruction 0 | `H_APPLY`; total 3, information 2, instruction 1 |
| Next response in the scripted fixture | Same error → review required; no verification | Corrected response → `V_FRESH`; total 4, verification 1 |
| Final scripted fixture response | No independent claim | Correct fresh response → `local_unassisted_success` **in the software fixture only** |

Complete event-level traces are in `software_repro_before_v03.json` and `software_repro_after_v031.json`. Their `SOFTWARE_EVENT_*` identifiers are deliberately excluded from learner counts. The final scripted success demonstrates that the control path can verify; it is not evidence that any student learned from the cue.

## Repair

`TaskFinding` is a separate, provenance-bearing ledger record. Each finding keeps its source event, task and validator identity/version, source hash, validation category, assistance history, and rationale. The controller records it alongside, not inside, qualitative hypothesis evidence. The unchanged full representation creates `requested_exchange_not_performed` as a validated task error. It does **not** create support for `application_gap`. `H_APPLY` is eligible because the authored cue addresses the observed exchange error. The decision trace names the finding, the applicable content rule, and rejected alternatives. Duplicate event IDs do not add findings again; operator correction recomputes the finding from the preserved raw event.

Incorrect and incomplete are distinct. Changed totals create `full_value_changed`; explicit but missing unit counts create `representation_incomplete`. A bare number with no unit labels creates `numeric_reply_lacks_units`, which does not satisfy the cue's explicit-count precondition. Correct arithmetic paired with contradictory or unverified reasoning remains a reasoning issue and cannot establish independent understanding. Unparseable input stays with operator review. The hidden-state evidence thresholds remain conservative and visible.

The declared bank now states finding preconditions for cues and guard outcomes for correct answers, unresolved reasoning and unparseable input. The generic controller checks the *actual* parsed/validated response before executing a branch. A branch with a resource-feasible candidate is not assumed to be executable. Once a validated, relevant task finding supports a cue, the controller selects bounded assistance before spending another information prompt. A second validated post-instruction failure can select one unused grounded strategy change; the original caps remain six total, three information, two instruction, one verification and one strategy change. Proposed actions, delivery, and real responses remain separate; independent completion still requires a fresh actual unassisted response.

## Actual checks

- The original hint-repair run passed **112 tests**; the current `Test.ps1` result after the separate setup-integrity repair is **115 passed, 0 failed, 0 skipped**. The new hint regression was observed to fail against unedited v0.3, then pass after the repair. The updated XML is `pytest-results-v031.xml`.
- Branch audit: **28 declared branches over five probes**, plus a bare-number failed-precondition path. Each row in `branch_audit_v031.md` reports validation category, finding/evidence, preconditions, intended route, actual route and test ID. `P_UNIT/unit_correct` is appropriately superseded when its intended probe was used; correct full answers use the performance guard; `P_REASON/unparseable` requires operator review. These are documented control decisions, not forced hint diversity.
- Mathematical checks: **900 supported-number checks** recorded in `content_review_queue_v031.json`. Fixtures cover 346, 352, 507 (zero tens), and a nonzero-tens combination probe; value-preserving and value-changing failures, incomplete/ambiguous/unparseable answers, inconsistent reasoning, fresh verification, repeat failure, stop, budget exhaustion, event replay and one parser-to-controller text input.
- `Inspect.ps1`: 92 development excerpts, including the 17-case registry, still have **zero new real follow-up observations, zero deliveries and zero learning outcomes**. Its 92 initial actions are 66 `C_CONTEXT`, one `C_RESTATE`, one learner stop and 24 unsupported-task closures. The same-gate fixed comparator makes the same initial selection on all 92; 25 action types differ from saved v0.2 initial decisions. These context-gate differences do not assess hint effectiveness.
- Historical integrity: `verify-history` requires 137 v0.2 and 67 v0 bundled files to match their manifests, then checks the saved frozen v0 digests without loading models. External original workspaces are a separate optional audit with per-file status; setup does not claim they were checked. Original v0.3 reports and test XML are retained separately.

## Limits and pending review

The 92 recorded excerpts do not contain subsequent responses to this new policy, so they cannot estimate learning, response probabilities, expected utility or sequential outcomes. No classifier was tuned or new learner data generated. The diagnostic baseline is still the saved logistic regression artifact. Legacy classifier-dependent reruns remain blocked on this installation by Windows Application Control denying a scikit-learn compiled DLL; historical results are retained, not represented as freshly reproduced. The narrow parser can require operator interpretation, and a manually supplied typed record needs its stated provenance. All **16 authored content entries remain pending human pedagogical review**, even though their deterministic mathematical checks pass.

For a real sequential evaluation, collect consented prompt delivery, the learner's actual response to each delivered probe/cue, any outside assistance, operator parsing/corrections, and an actual fresh unassisted verification response. Keep the development split for changes and the historical test set frozen until a preregistered evaluation. Neither the software trace nor these development-only initial decisions support a learning-gain claim.
