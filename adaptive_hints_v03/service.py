"""Manual provenance boundary, atomic sessions and separate delivery recording."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from . import ROOT
from .contracts import TaskContract, ObservationEvent
from .data import DevelopmentSource, verify_history, read_json, write_json, digest, DataError

SESSION_ROOT = ROOT / "reviews/manual_sessions"
_LOCK = RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def signature():
    files = sorted((ROOT / "adaptive_hints_v03").glob("*.py"))
    files += sorted((ROOT / "config").glob("*.json")) + sorted((ROOT / "content").glob("*.json"))
    return {p.relative_to(ROOT).as_posix():digest(p) for p in files}


def _session_path(session_id, session_kind):
    if session_kind not in {"debugging", "authentic_learner"}:
        raise ValueError("Choose debugging or authentic_learner explicitly.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", session_id):
        raise ValueError("Session ID must be 1–96 letters, digits, underscores or hyphens.")
    return SESSION_ROOT / session_kind / (session_id + ".json")


@contextmanager
def _exclusive(session_id, session_kind):
    path = _session_path(session_id, session_kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.with_suffix(".lock").open("a+b") as stream:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if __import__("os").name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("Another request is updating this session; retry without changing its event ID.") from exc
            try:
                yield path
            finally:
                stream.seek(0)
                if __import__("os").name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def source_case(record_id, session_id="inspection"):
    from .regrouping import RegroupingAdapter
    source = DevelopmentSource()
    observation = source.observation(record_id)
    task = RegroupingAdapter().assess_context(observation)
    last = observation.observed_context[-1]
    event = ObservationEvent(event_id=f"bridge:{record_id}:{last.message_id}", session_id=session_id,
        task_id=task.task_id, task_version=task.version, action_id=None,
        source_kind="recorded_excerpt", raw_text=last.text,
        source_spans=[{"record_id":record_id,"message_id":last.message_id,"role":last.role,"text":last.text}],
        source_hash=canonical_hash(asdict(observation)), received_time=now(), assistance="unknown",
        parser_provenance={"source":"Immutable full_inputs.jsonl observation; no references or subsequent tutor feedback.","record_id":record_id})
    return task, event, asdict(observation)


def preview(record_id):
    from .regrouping import RegroupingAdapter
    from .engine import EvidenceLedgerController
    task, event, observation = source_case(record_id, "inspection-" + record_id)
    engine = EvidenceLedgerController(RegroupingAdapter(), task, initial_event=event,
        session_id=event.session_id, session_kind="debugging")
    return {"task":task.to_dict(),"observation":observation,"trace":engine.propose().to_dict(),
            "state":engine.state,"provenance":"Offline initial decision only. No action delivered or subsequent learner response observed."}


def load(session_id, session_kind="debugging"):
    path = _session_path(session_id, session_kind)
    if not path.exists():
        return None
    payload = read_json(path)
    if payload["implementation_signature"] != signature():
        raise DataError("This episode uses a different implementation version. Preserve its history; do not reset or silently migrate its budget.")
    if payload["session_id"] != session_id or payload["session_kind"] != session_kind:
        raise DataError("Session identity or provenance kind changed.")
    return payload


def _engine(payload):
    from .regrouping import RegroupingAdapter
    from .engine import EvidenceLedgerController
    return EvidenceLedgerController(RegroupingAdapter(), TaskContract.from_dict(payload["initial_task"]),
        session_id=payload["session_id"], session_kind=payload["session_kind"], state=payload["state"])


def _save(path, payload, engine, trace):
    payload.update(state=engine.state, trace=trace.to_dict(), updated_time=now())
    write_json(path, payload)
    return payload


def start_recorded(session_id, record_id, session_kind="debugging"):
    if session_kind != "debugging":
        raise ValueError("Historical excerpt replay is debugging only. Start a separate authentic manual session for a real learner.")
    verify_history()
    with _exclusive(session_id, session_kind) as path:
        saved = load(session_id, session_kind)
        if saved:
            if saved.get("record_id") != record_id:
                raise ValueError("This session already belongs to a different source task.")
            engine = _engine(saved)
            return _save(path, saved, engine, engine.propose())
        task, event, observation = source_case(record_id, session_id)
        from .engine import EvidenceLedgerController
        from .regrouping import RegroupingAdapter
        engine = EvidenceLedgerController(RegroupingAdapter(), task, initial_event=event,
            session_id=session_id, session_kind=session_kind)
        payload = {"session_id":session_id,"session_kind":session_kind,"record_id":record_id,
            "initial_task":task.to_dict(),"initial_observation":observation,"created_time":now(),
            "implementation_signature":signature(),"provenance":"Recorded initial excerpt; manual follow-ups require explicit delivery and source attestations. Debugging sessions are excluded from learner metrics."}
        return _save(path, payload, engine, engine.propose())


def start_manual(session_id, task_text, source, session_kind="debugging", initial_response="", assistance="unknown", attested_actual=False):
    if not task_text.strip() or not source.strip():
        raise ValueError("Exact task text and task source are required.")
    if initial_response and not attested_actual:
        raise ValueError("An initial attempt must be an explicitly attested actual response.")
    from .regrouping import RegroupingAdapter
    from .engine import EvidenceLedgerController
    with _exclusive(session_id, session_kind) as path:
        saved = load(session_id, session_kind)
        if saved:
            if saved.get("manual_task_text") != task_text:
                raise ValueError("Resuming cannot replace the task or reset the budget.")
            engine = _engine(saved)
            return _save(path, saved, engine, engine.propose())
        observation = {"record_id":"manual-"+session_id,"observed_context":[{"role":"tutor","text":task_text,"message_id":"manual-task"}]}
        task = RegroupingAdapter().assess_context(observation)
        event = None
        if initial_response:
            event = ObservationEvent(event_id=session_id+":initial",session_id=session_id,task_id=task.task_id,
                task_version=task.version,action_id=None,source_kind="actual_manual_learner_response",raw_text=initial_response,
                source_hash=canonical_hash({"text":initial_response,"source":source}),received_time=now(),assistance=assistance,
                parser_provenance={"source":source,"attested_actual":True,"initial_attempt":True})
        engine = EvidenceLedgerController(RegroupingAdapter(), task, initial_event=event,session_id=session_id,session_kind=session_kind)
        payload = {"session_id":session_id,"session_kind":session_kind,"record_id":None,"manual_task_text":task_text,
            "initial_task":task.to_dict(),"initial_observation":observation,"created_time":now(),
            "implementation_signature":signature(),"provenance":{"task_source":source,"classification":session_kind}}
        return _save(path,payload,engine,engine.propose())


def deliver(session_id, action_id, source, session_kind="debugging"):
    if not source.strip():
        raise ValueError("Record the actual delivery source, recipient/session reference and time.")
    with _exclusive(session_id, session_kind) as path:
        saved = load(session_id, session_kind)
        if saved is None:
            raise ValueError("Start a session first.")
        engine = _engine(saved)
        engine.record_delivery(action_id,source,now())
        return _save(path,saved,engine,engine.propose())


def respond(session_id, event_id, action_id, task_id, task_version, raw_text, source, assistance,
            attested_actual, session_kind="debugging"):
    if attested_actual is not True or not raw_text.strip() or not source.strip():
        raise ValueError("Enter an actual response, its source, and an explicit authenticity attestation.")
    with _exclusive(session_id, session_kind) as path:
        saved = load(session_id, session_kind)
        if saved is None:
            raise ValueError("Start a session first.")
        engine = _engine(saved)
        # Retry metadata is stable: an existing event retains its receipt time.
        prior = next((row for row in saved["state"].get("events",[]) if row.get("event",row).get("event_id") == event_id), None)
        prior_event = prior.get("event",prior) if prior else None
        event = ObservationEvent(event_id=event_id,session_id=session_id,task_id=task_id,task_version=task_version,
            action_id=action_id,source_kind="actual_manual_learner_response",raw_text=raw_text,
            source_hash=canonical_hash({"raw_text":raw_text,"source":source}),received_time=prior_event["received_time"] if prior_event else now(),
            assistance=assistance,parser_provenance={"source":source,"attested_actual":True,
                "boundary":"Operator attests delivery and assistance; deterministic validation only checks declared mathematical content."})
        return _save(path,saved,engine,engine.observe(event))


def annotate(session_id,target_event_id,category,parsed_fields,rationale,operator_id,annotation_id,supersedes=None,session_kind="debugging"):
    if not rationale.strip() or not operator_id.strip():
        raise ValueError("Operator categorization requires an identity and rationale.")
    with _exclusive(session_id, session_kind) as path:
        saved = load(session_id, session_kind)
        if saved is None:
            raise ValueError("Start a session first.")
        engine = _engine(saved)
        prior = next((row for row in saved["state"].get("annotations",[]) if row["event_id"] == annotation_id), None)
        receipt = prior["event"]["received_time"] if prior else now()
        trace = engine.annotate(target_event_id,category,parsed_fields,rationale,operator_id,annotation_id,receipt,supersedes=supersedes)
        return _save(path,saved,engine,trace)


def stop(session_id, session_kind="debugging"):
    with _exclusive(session_id, session_kind) as path:
        saved = load(session_id, session_kind)
        if saved is None:
            raise ValueError("Start a session first.")
        engine = _engine(saved)
        return _save(path,saved,engine,engine.stop())


def list_sessions():
    result = []
    for kind in ("debugging","authentic_learner"):
        for path in sorted((SESSION_ROOT / kind).glob("*.json")):
            row = read_json(path)
            result.append({"session_id":row["session_id"],"session_kind":kind,"record_id":row.get("record_id"),"updated_time":row["updated_time"]})
    return result
