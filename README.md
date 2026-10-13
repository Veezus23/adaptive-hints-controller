# Adaptive Hints Controller (public research code)

A Python research prototype for choosing bounded diagnostic probes, hints, and independent checks from observed learner work. The generic evidence-ledger controller is separate from a three-digit regrouping adapter. It records what was observed, what remains uncertain, and why an action was selected.

This is a **code release** from a broader education research project. The original workspace contains licensed tutoring data and historical experiment artifacts that are not included here. The software fixtures in this repository are not learner outcomes. No improvement in learning or AI safety is claimed.

## Run the data-free controller tests

```bash
python -m pip install -e '.[test]'
python -m pytest tests/test_engine.py -q
```

`test_engine.py` uses typed software fixtures. Some features in `adaptive_hints_v03` require the separate historical research dataset and are intentionally not runnable from this release.

## Research notes

- [v0.3.1 repair and verification](reports/REPAIR_IMPLEMENTATION_REPORT_V031.md)
- [Eedi action pilot](reports/EEDI_ACTION_PILOT_V2.md): development-only analysis; source dialogues and annotations are excluded from this repository.

## Disclosure

ChatGPT and Codex assisted with implementation, evidence annotation, and reporting. The project owner reviewed the reported pilot cases. The reports distinguish software checks and development annotations from real learner outcomes.
