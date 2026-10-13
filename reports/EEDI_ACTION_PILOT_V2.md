# Eedi development pilot: shared evidence-to-action controller

**Scope:** a separate, exploratory development pilot on the 24 frozen real Eedi decision inputs. The [v1 context audit](EEDI_CONTEXT_PILOT_V1.md), its original inputs, and its results remain unchanged. This run tests whether a shared deterministic controller can make inspectable action choices from cited structured evidence. It does **not** measure action quality, diagnostic accuracy, generalization, or learning benefit.

## Implemented behavior

The [pre-run protocol](../docs/EEDI_ACTION_PILOT_V2_PROTOCOL.md) defines a revised local context window. It selects the latest non-social tutor question, or falls back to the nearest tutor utterance, and retains contiguous adjacent messages through the student cutoff. If the selected prompt lacks an explicit mathematical symbol, it also includes up to two preceding messages. The full condition uses the entire recorded prefix. Both share the anchored question and latest student reply, and neither includes a future turn. The [window manifest](eedi_action_windows_v2.json) records the exact sequences and fallback flag for every case; the v1 packet is untouched.

For each condition, a separate [AI-assisted evidence annotation](../reviews/eedi_ai_action_evidence_v2.json) records context sufficiency, observed mathematical performance, competing explanations, and visible prior assistance with source-message citations. Unknown values are allowed. Candidate explanations are hypotheses, not validated misconceptions or prerequisite-mastery labels. The annotations contain no preferred action. The same AI author had seen the cases during v1, so these are unblinded development annotations **pending independent human review**.

The [shared controller](../adaptive_hints_v03/eedi_action_controller.py) receives exactly those four fields. It knows no question ID, topic screen, transcript, reference label, or preferred action. Its ordered rules choose clarification for insufficient/unknown context; verification for a correct visible step without unresolved competing explanations; a diagnostic probe when explanations compete or a step is wrong/partial; and a strategy cue when no step is visible or an incorrect step follows substantive assistance. The controller evaluates and logs every rule, marks the selected rule, and shows why lower-priority alternatives did not win. It selects an **action kind**, not subject-specific prompt wording.

## Observed run

| Measure | Result |
| --- | ---: |
| Frozen development cases | 24 |
| Revised local windows longer than v1's two-message view | 12 |
| Selected tutor prompt shifted earlier than v1's nearest utterance | 2 |
| Fallback to nearest tutor utterance because no qualifying prompt was found | 5 |
| Local and full input sequences identical | 2 |
| Different evidence **values** between paired conditions: context / performance / competing explanations / assistance | 0 / 0 / 1 / 3 |
| Different controller action kinds between paired conditions | 1 |

Local decisions were 12 diagnostic probes, 8 verification prompts, and 4 strategy cues. Full-prefix decisions were 13 diagnostic probes, 7 verification prompts, and 4 strategy cues. No case triggered clarification, because the anchored questions and visible turns were annotated sufficient for a local mathematical move; the clarification branch is nevertheless implemented and software-tested. Relative to fixed action rules, the full-prefix controller differs from always-clarify in 24/24 cases and from always-diagnostic-probe in 11/24. Those are **decision differences only**.

The one paired action difference is **intervention 1415** at sequence 9. Both views contain the tutor's question about 10% of 40 (sequence 8) and the student's correct “4” (sequence 9). The local evidence has no visible competing explanation, so rule `R2_CORRECT` selects **verification**. The full prefix additionally contains earlier, conflicting percentage claims (sequences 5–7). The AI-assisted evidence lists two possible explanations without assigning a misconception; `R2_CORRECT` is ineligible while those alternatives remain unresolved, and `R3_COMPETING` selects a **diagnostic probe**. This is an inspectable policy response to the annotated evidence, not proof that probing is better.

The revised window resolves an input defect exposed by v1. In **6692**, v1's nearest tutor utterance was reassurance at sequence 23. V2 retains the mathematical tutor prompt at 22 and the intervening reassurance through the student's “2/8” at 24. The local and full controller decisions are both **verification**; the full evidence separately records the earlier worked assistance. In **5057**, v2 retains the percentage-conversion question at 15, adjacent fraction confirmation at 14, and intervening turns through 19, rather than only the reassurance at 18. These examples show why the window definition matters before interpreting a context comparison.

The [paired result JSON](eedi_action_pilot_v2.json) contains every input sequence list, both evidence objects, both selected actions, all rule evaluations and dispositions, and the source hashes. The [readable case traces](EEDI_ACTION_PILOT_V2_CASE_TRACES.md) show the same evidence and selected rules without republishing raw dialogue. No validation or official test case was used for annotation or action selection. The broad topic screen still includes an off-topic quadratic case (286); no regrouping validator or subject-specific Eedi validator was applied to it.

## Provisional assumptions and untested claims

The evidence classifications, including whether a short answer is correct and whether two explanations genuinely compete, require independent mathematical and educational review. The local-window rule is reproducible but heuristic; its five fallback cases should be reviewed for context quality. With only one AI annotator and no gold action labels, these counts do not establish decision correctness. With no delivered prototype action or subsequent response to it, the recorded historical continuation cannot establish hint effectiveness. The next empirical step is independent, condition-specific human evidence review with disagreements preserved; a later intervention study would also need actual delivery, subsequent learner work, and an unassisted outcome measure.

## Reproduce and verify

From the v0.3.1 project directory in PowerShell:

```powershell
$runtime = (Get-Content .runtime.json -Raw | ConvertFrom-Json).python
& $runtime -m adaptive_hints_v03.eedi_action_pilot windows
& $runtime -m adaptive_hints_v03.eedi_action_pilot run
& $runtime -m pytest tests\test_eedi_action_pilot.py -q -p no:cacheprovider
.\Test.ps1
```

The runner checks pinned Eedi source hashes, development membership, condition-specific citations, cutoff boundaries, the blank human-review template, and four v1 artifact hashes. The authored annotation JSON is preserved on rerun; the authoring script refuses to overwrite it. The controller's policy tests are software-invariant checks, not empirical learner results.

Verification on 2026-10-05: `Test.ps1` passed **125/125** tests. Mandatory bundled history verification passed for 137 v0.2 and 67 v0 files; external original workspaces were `not_checked` and are not claimed unchanged.
