"""Read-only authentic development records and immutable historical artifacts."""
from pathlib import Path
from dataclasses import asdict
from collections import Counter
from types import SimpleNamespace
from . import ROOT, HISTORY
import adaptive_hints_v02  # Only its stdlib path bootstrap; no model libraries.
from adaptive_hints.contracts import Observation, Message
from adaptive_hints.io import read_json, read_jsonl, digest, write_json, DataError

BASELINE = HISTORY / "baseline_v0"


def _require_bundled_digest(path, expected, label):
    """A bundled integrity failure always blocks use of the prototype."""
    try:
        actual = digest(path)
    except OSError as exc:
        raise DataError(f"{label} cannot be read: {path}") from exc
    if actual != expected:
        raise DataError(f"{label} changed: {path}")


def _compare_external_original(root, expected_files, *, enabled):
    """Best-effort comparison; unreadable originals are never called verified."""
    counts = {name: 0 for name in ("verified", "changed", "missing", "inaccessible", "not_checked")}
    entries = {}
    if enabled:
        for relative, expected in expected_files.items():
            path = root / relative
            try:
                actual = digest(path)
            except FileNotFoundError:
                status, actual = "missing", None
            except PermissionError:
                status, actual = "inaccessible", None
            except IsADirectoryError:
                status, actual = "changed", None  # An expected file was replaced by a directory.
            except OSError:
                status, actual = "inaccessible", None
            else:
                status = "verified" if actual == expected else "changed"
            counts[status] += 1
            entry = {"status": status}
            if actual is not None:
                entry["actual_sha256"] = actual
            entries[relative] = entry
    else:
        counts["not_checked"] = len(expected_files)
    present = [name for name, count in counts.items() if count]
    overall = present[0] if len(present) == 1 else "mixed"
    return {"status": overall, "root": str(root), "counts": counts, "files": entries}


def verify_history(*, compare_originals=False):
    """Require bundled hashes; optionally report external originals without gating setup."""
    manifest = read_json(ROOT / "artifacts/history_v02_manifest.json")
    for relative, expected in manifest["files"].items():
        _require_bundled_digest(HISTORY / relative, expected, "Historical v0.2 snapshot")
    v0 = read_json(HISTORY / "artifacts/v0_snapshot_manifest.json")
    for relative, expected in v0["files"].items():
        _require_bundled_digest(BASELINE / relative, expected, "Preserved v0 file")
    frozen = read_json(BASELINE / "artifacts/frozen_protocol.json")
    # Execute every saved immutable digest check without importing classifier
    # binaries. The original verifier remains unchanged in the source snapshot.
    for area in ("data", "protocol"):
        for relative, expected in frozen["signature"][area].items():
            _require_bundled_digest(BASELINE / relative, expected, f"Bundled frozen {area}")
    for relative, expected in {
        "split.json":frozen["signature"]["split_sha256"],
        "models.joblib":frozen["model_sha256"],
        "model_metadata.json":frozen["model_metadata_sha256"],
        **read_json(BASELINE / "artifacts/evaluation_seal.json")["hashes"],
    }.items():
        _require_bundled_digest(BASELINE / "artifacts" / relative, expected, "Bundled frozen artifact")
    # No external reads occur until every mandatory bundled check has passed.
    original = _compare_external_original(Path(manifest["original_path"]), manifest["files"], enabled=compare_originals)
    v0_original = _compare_external_original(Path(v0["original_path"]), v0["files"], enabled=compare_originals)
    return {"v02": {"status": "unchanged", "files": len(manifest["files"]),
                    "original_checked": original["status"] == "verified", "original": original},
            "v0": {"status": "unchanged", "files": len(v0["files"]),
                   "original_checked": v0_original["status"] == "verified", "original": v0_original,
                   "saved_frozen_digests": "verified_without_loading_models"}}


class DevelopmentSource:
    def __init__(self):
        self.split = read_json(BASELINE / "artifacts/split.json")
        self.labels = tuple(read_json(BASELINE / "config/labels.json")["labels"])
        self.data = self  # Compatibility for read-only split/label access.
        self.ids = tuple(sorted(self.split["partitions"]["development"]))
        self.selected_ids = tuple(read_json(HISTORY / "reviews/audit_selection.json")["record_ids"])
        self._observations = None
        self._references = None
        self._baseline = None
        self._old_decisions = None

    def require_development(self, record_id):
        if record_id not in self.ids:
            raise DataError("Only frozen original development records may enter regrouping inspection; historical test is excluded.")

    def observation(self, record_id):
        self.require_development(record_id)
        if self._observations is None:
            self._observations = {}
            for row in read_jsonl(BASELINE / "data/bridge_real_student_sample/full_inputs.jsonl"):
                if row["record_id"] not in self.ids:
                    continue
                if set(row) != {"record_id","lesson_topic","observed_context"}:
                    raise DataError("Mixed reference/input fields are forbidden.")
                messages = []
                for message in row["observed_context"]:
                    if message.get("is_revised") or message["user"] not in {"student","learner","tutor","teacher"} or not isinstance(message["text"],str):
                        raise DataError("Expected verbatim recorded learner/tutor text only.")
                    messages.append(Message(message["user"],message["text"],str(message["id"]) if message.get("id") is not None else None,message.get("time")))
                if len(messages) != 4 or row["record_id"] in self._observations:
                    raise DataError("Unexpected excerpt length or duplicate record.")
                self._observations[row["record_id"]] = Observation(row["record_id"],row["lesson_topic"],tuple(messages))
            if set(self._observations) != set(self.ids):
                raise DataError("Frozen development IDs do not match full inputs.")
        return self._observations[record_id]

    def observation_dict(self, record_id):
        return asdict(self.observation(record_id))

    def reference_for_review(self, record_id):
        self.require_development(record_id)
        if self._references is None:
            self._references = {row["record_id"]:row for row in read_jsonl(BASELINE / "data/bridge_real_student_sample/full_references.jsonl") if row["record_id"] in self.ids}
        row = self._references[record_id]
        labels = [annotation["error_label_raw"] for annotation in row["human_annotations"]]
        counts = Counter(label for label in labels if label in self.labels)
        total = sum(counts.values())
        unmapped = [label for label in labels if label not in self.labels]
        return {"record_id":record_id,"partition":"development","raw_human_annotations":row["human_annotations"],
                "original_tutor_responses":row["observed_original_tutor_responses"],
                "mapped_distribution":{label:counts[label]/total if total else 0 for label in self.labels},
                "unmapped_annotations":unmapped,"partial_mapping":bool(total and unmapped),
                "mapped_disagreement":len(counts)>1,"metric_status":"included" if total else "excluded_no_supported_label"}

    def descriptive_baseline(self, record_id):
        self.require_development(record_id)
        if self._baseline is None:
            self._baseline = {row["record_id"]:row for row in read_jsonl(HISTORY / "artifacts/development_predictions.jsonl") if row["model"] == "v0_logistic_C1" and row["record_id"] in self.ids}
        return self._baseline[record_id]

    def saved_old_decision(self, record_id):
        self.require_development(record_id)
        if self._old_decisions is None:
            self._old_decisions = {row["record_id"]:row for row in read_json(HISTORY / "reports/loop_comparison.json")["records"]}
        return self._old_decisions[record_id]
