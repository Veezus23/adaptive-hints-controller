"""Dependency-free localhost manual interface; no automatic learner responses."""
from __future__ import annotations
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4
from . import ROOT
from . import service
from .data import DevelopmentSource, read_json

TOKEN = secrets.token_urlsafe(32)


def esc(value):
    return html.escape(str(value), quote=True)


def details(title, value, opened=False):
    return f'<details {"open" if opened else ""}><summary>{esc(title)}</summary><pre>{esc(json.dumps(value,ensure_ascii=False,indent=2))}</pre></details>'


def field(name, label, value="", kind="text", required=False):
    return f'<label>{esc(label)}<input type="{esc(kind)}" name="{esc(name)}" value="{esc(value)}" {"required" if required else ""}></label>'


def textarea(name, label, value="", required=False):
    return f'<label>{esc(label)}<textarea name="{esc(name)}" rows="4" {"required" if required else ""}>{esc(value)}</textarea></label>'


def select(name,label,values,chosen=None):
    return f'<label>{esc(label)}<select name="{esc(name)}">'+''.join(f'<option value="{esc(v)}" {"selected" if v==chosen else ""}>{esc(label_text)}</option>' for v,label_text in values)+'</select></label>'


def checkbox(name, label):
    return f'<label class="check"><input type="checkbox" name="{esc(name)}" value="yes">{esc(label)}</label>'


def form(operation, content, button, session=None, kind=None):
    hidden = {"csrf":TOKEN,"operation":operation}
    if session: hidden["session_id"] = session
    if kind: hidden["session_kind"] = kind
    return '<form method="post" action="/action">'+''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k,v in hidden.items())+content+f'<button type="submit">{esc(button)}</button></form>'


def trace_view(trace):
    selected = trace.get("selected")
    result = f'<p class="eyebrow">{esc(trace["rule_id"])} · {esc(trace["status"])}</p><p>{esc(trace["reason"])}</p>'
    if selected:
        result += f'<div class="prompt"><span class="tag">{esc(selected["candidate"]["candidate_id"])}</span><p>{esc(selected["text"])}</p><small>Authored tutor prompt · human review pending · {esc(selected.get("delivery_status","not delivered"))}</small></div>'
    if trace.get("outcome"):
        result += details("Session outcome — local observation only",trace["outcome"],True)
    result += details("Remaining budget and counters",trace.get("budget_after",{}),True)
    result += details("Eligible and rejected actions",{"eligible":trace.get("eligible_candidates",[]),"rejected":trace.get("rejected_candidates",[])})
    result += details("Hypotheses and evidence partitions",trace.get("hypotheses",[]))
    result += details("Validated task findings — separate from learner hypotheses",trace.get("task_findings",[]))
    return result


def page(query):
    source = DevelopmentSource()
    record = query.get("record",["2900864_55"])[0]
    all_cases = query.get("set",["selected"])[0] == "all"
    ids = source.ids if all_cases else source.selected_ids
    session_id = query.get("session",[""])[0]
    kind = query.get("kind",["debugging"])[0]
    nav = '<form method="get">'+select("set","Inspection set",[("selected","17 purposive audit cases"),("all","All 92 development excerpts")],"all" if all_cases else "selected")+select("record","Recorded excerpt",[(rid,rid) for rid in ids],record)+'<button>Inspect case</button></form>'
    nav += '<p class="muted">Historical test examples are excluded. The 17 cases are included in the 92-case development split.</p><a href="#manual">Start a manual session</a>'
    sessions = service.list_sessions()
    if sessions:
        nav += '<h3>Saved sessions</h3>' + ''.join(f'<p><a href="/?{urlencode({"session":s["session_id"],"kind":s["session_kind"],"record":record})}">{esc(s["session_id"])}</a><br><small>{esc(s["session_kind"])}</small></p>' for s in sessions)
    body = '<p class="eyebrow">REGROUPING · VERSION 0.3.1 · LOCAL RESEARCH PROTOTYPE</p><h1>Evidence before diagnosis</h1><p class="lead">A bounded pathway for exchanging one hundred into tens. Inspect what is known, record actual responses, and keep uncertainty visible.</p>'
    if query.get("message"):
        body += '<p class="notice">'+esc(query["message"][0])+'</p>'
    if session_id:
        saved = service.load(session_id,kind)
        if saved is None: raise ValueError("Session not found.")
        state, trace = saved["state"], saved["trace"]
        body += f'<section><h2>Session {esc(session_id)}</h2><p class="tag">{esc(kind)} — provenance category is fixed</p>'
        body += trace_view(trace)
        pending = state.get("pending_action")
        review_id = trace.get("pending_operator_review") or state.get("pending_operator_review")
        if not review_id and state.get("status") == "closed":
            unresolved = [row for row in state.get("effective_history",[]) if row["validation"].get("operator_required")]
            review_id = unresolved[-1]["event"]["event_id"] if unresolved else None
        if pending:
            action_id = pending["action_id"]
            delivered = any(row["action_id"] == action_id for row in state.get("deliveries",[]))
            body += '<h3>Record actual delivery</h3><p>Issuing this proposal does not mean it was delivered. Delivery does not create a response.</p>'
            if not delivered:
                body += form("deliver",field("action_id","Pending action ID",action_id)+field("source","Delivery provenance (session/log, recipient reference and time)",required=True)+checkbox("delivered","I actually delivered exactly the displayed prompt"),"Record delivery",session_id,kind)
            elif not review_id:
                body += '<p class="notice">Delivery recorded. Awaiting an actual learner response.</p>'
                content = field("action_id","Linked action",action_id)+field("task_id","Linked task",pending["task_id"])+field("task_version","Task version",pending["task_version"])
                content += field("event_id","Unique source event ID","response-"+uuid4().hex[:12],required=True)
                content += textarea("raw_text","Exact actual response — never auto-filled",required=True)+field("source","Response source/log and recorder",required=True)
                content += select("assistance","Outside assistance while answering",[("unknown","Unknown"),("assisted","Assistance was given"),("unassisted","Explicitly no outside assistance")],"unknown")
                content += checkbox("attested_actual","This is an actual response; its provenance and assistance are recorded accurately")
                body += form("respond",content,"Record response and continue",session_id,kind)
        if review_id:
            body += f'<h3>Operator categorization required</h3><p>The raw response is preserved. Categorization is a documented judgment, not an automatically validated diagnosis.</p>'
        from .regrouping import RegroupingAdapter
        categories = getattr(RegroupingAdapter(),"operator_categories",{})
        if isinstance(categories,dict): choices = [(str(k),str(k)) for k in categories]
        else: choices = [(str(k),str(k)) for k in categories]
        annotation_content = field("target_event_id","Response event to categorize or correct",review_id or "",required=True)
        annotation_content += select("category","Documented response category",choices) if choices else field("category","Documented response category",required=True)
        annotation_content += details("Operator category contract",categories)
        annotation_content += '<p>Parse only statements actually present in the response. Full exchange: <code>counts</code> with integer <code>hundreds, tens, ones</code>. Unit question: integer <code>unit_count, unit_value</code>. Equivalence/combining: integer <code>tens_count</code>. Optional <code>reasoning</code> preserves explicit statements; see <code>docs/MANUAL_PROTOCOL.md</code>. Do not supply missing reasoning or answer keys.</p>'
        annotation_content += textarea("parsed_fields","Structured parse JSON (fields supported by the category)","{}",True)
        annotation_content += textarea("rationale","Rationale grounded in the actual response",required=True)+field("operator_id","Operator identifier",required=True)
        annotation_content += field("annotation_id","Unique annotation ID","annotation-"+uuid4().hex[:12],required=True)+field("supersedes","Prior annotation ID being corrected, if any")
        body += '<details><summary>Append or correct an operator categorization</summary>'+form("annotate",annotation_content,"Append categorization",session_id,kind)+'</details>'
        body += form("stop","<p>Stopping retains history and budget. It cannot certify understanding.</p>","Stop this session",session_id,kind)
        body += details("Evidence ledger, assistance and append-only annotations",state)
        body += f'<p><a href="/export?{urlencode({"session":session_id,"kind":kind})}">Download session JSON — {esc(kind)}</a></p></section>'
    else:
        source.require_development(record)
        preview = service.preview(record)
        task, trace = preview["task"], preview["trace"]
        body += f'<section><div class="split"><div><h2>Recorded case {esc(record)}</h2>'
        for message in preview["observation"]["observed_context"]:
            body += f'<div class="message {esc(message["role"])}"><small>{esc(message["role"])} · source message {esc(message["message_id"])}</small><pre>{esc(message["text"])}</pre></div>'
        body += '</div><div><h2>Context gate</h2>'+f'<p class="tag">{esc(task["context_status"])}</p><p>{esc(task["reason"])}</p>'
        if task["missing_fields"]: body += '<p>Missing: '+esc(', '.join(task["missing_fields"]))+'</p>'
        body += details("Task and exact source spans",task)+'</div></div><h2>Initial decision</h2><p class="muted">Offline proposal. No delivered action or new learner response is claimed.</p>'+trace_view(trace)
        body += form("start_recorded",field("record_id","Recorded source",record)+field("session_id","Stable debugging session ID","debug-"+record,required=True),"Start or resume debugging episode")
        body += f'<p><a href="/trace?{urlencode({"record":record})}">Download initial trace</a></p>'
        body += details("Original human annotations — separate review only",source.reference_for_review(record))
        body += details("Saved logistic baseline — descriptive only; unused in regrouping hypotheses",source.descriptive_baseline(record))+'</section>'
    body += '<section id="manual"><h2>Start a separate manual session</h2><p>Enter the task actually used. Choose authentic learner only for a genuine session. Debugging sessions are excluded from learner metrics. Reusing a session ID resumes its existing budget.</p>'
    manual = field("session_id","Stable session ID",required=True)+select("session_kind","Session provenance",[("debugging","Builder/operator debugging"),("authentic_learner","Authentic learner session")],"debugging")
    manual += textarea("task_text","Exact local task prompt — number, exchange direction and requested representation",required=True)+field("source","Task provenance",required=True)
    manual += textarea("initial_response","Initial actual attempt, if already recorded (otherwise leave blank)")
    manual += select("assistance","Assistance for the initial attempt",[("unknown","Unknown"),("assisted","Assisted"),("unassisted","Explicitly unassisted")],"unknown")
    manual += checkbox("attested_actual","Any initial attempt entered above is an actual response with known provenance")
    body += form("start_manual",manual,"Start or resume manual session")+'</section>'
    comparison = ROOT / "reports/development_inspection_v031.json"
    if not comparison.exists(): comparison = ROOT / "reports/development_inspection.json"
    if comparison.exists():
        data = read_json(comparison)
        body += '<section><h2>Development inspection</h2><p>Initial actions and rationales only. Different tutoring actions have no recorded follow-up outcomes.</p>'+details("Recorded-data summary",data.get("aggregate",data.get("summary",{})))+'</section>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Regrouping evidence laboratory</title><style>'+CSS+'</style></head><body><aside><a class="brand" href="/">Regrouping<br>evidence laboratory</a>'+nav+'</aside><main>'+body+'<footer>Implemented; learner evaluation pending. Authored prompts are tutor content, not learner observations. No Bayesian, calibrated-confidence or learning-gain claim.</footer></main></body></html>'


CSS = '''*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#172635;font:16px/1.55 system-ui,sans-serif}aside{position:fixed;inset:0 auto 0 0;width:260px;background:#e8edf1;padding:30px 24px;overflow:auto;border-right:1px solid #d8dfe5}main{max-width:1350px;margin-left:260px;padding:44px 48px}.brand{display:block;color:#123b40;font-weight:750;font-size:22px;text-decoration:none;line-height:1.2;margin-bottom:30px}.eyebrow{font-size:12px;letter-spacing:.12em;color:#477073;font-weight:700}h1{font-size:44px;letter-spacing:-.04em;line-height:1.12;margin:12px 0}h2{font-size:25px;line-height:1.2;margin:0 0 20px}h3{font-size:18px;margin-top:26px}.lead{max-width:760px;color:#52616c;font-size:19px}section{background:#fff;border:1px solid #dde4e9;border-radius:14px;padding:28px;margin:28px 0}.split{display:grid;grid-template-columns:1fr 1fr;gap:30px}.message{border-left:3px solid #c6d1da;padding:12px 14px;margin-bottom:12px;background:#f5f7f9}.message.student{border-color:#27867c;background:#eff7f4}small,.muted,footer{color:#61707b;font-size:13px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 ui-monospace,monospace;margin:8px 0}.tag{background:#e2f0ec;color:#165d53;border-radius:5px;padding:5px 9px;font-size:12px;font-weight:650;display:inline-block}.prompt{padding:22px;border:1px solid #a8cec5;border-radius:10px;background:#f0f8f4;margin:18px 0}.prompt p{font-size:19px}.notice{background:#edf4fb;padding:14px;border-left:4px solid #5084b1}label{display:block;margin:14px 0;font-weight:550;font-size:14px}input,select,textarea{width:100%;margin-top:6px;padding:10px;border:1px solid #afbbc6;border-radius:6px;background:white;color:#172635;font:15px system-ui}textarea{resize:vertical}.check{font-weight:400;display:flex;gap:10px;align-items:flex-start}.check input{width:auto;margin-top:5px}button{padding:11px 18px;background:#17675e;color:white;border:0;border-radius:7px;cursor:pointer;font:600 14px system-ui}button:hover{background:#104f48}form{margin:18px 0}details{border-top:1px solid #e2e7eb;padding:14px 0}summary{cursor:pointer;font-weight:600}a{color:#185d78}footer{padding:16px 0 40px}@media(max-width:850px){aside{position:static;width:auto}main{margin:0;padding:24px}.split{grid-template-columns:1fr}h1{font-size:34px}}'''


class Handler(BaseHTTPRequestHandler):
    def _reply(self,status,content,mime="text/html; charset=utf-8"):
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/health": return self._reply(200,"ok","text/plain")
            if parsed.path == "/trace":
                return self._reply(200,json.dumps(service.preview(query["record"][0]),ensure_ascii=False,indent=2),"application/json")
            if parsed.path == "/export":
                return self._reply(200,json.dumps(service.load(query["session"][0],query.get("kind",["debugging"])[0]),ensure_ascii=False,indent=2),"application/json")
            if parsed.path != "/": return self._reply(404,"Not found","text/plain")
            self._reply(200,page(query))
        except (ValueError,KeyError,TypeError) as exc:
            self._reply(400,"<h1>Request could not be completed</h1><p>"+esc(exc)+'</p><a href="/">Return to inspection</a>')

    def do_POST(self):
        if self.path != "/action": return self._reply(404,"Not found","text/plain")
        try:
            size = int(self.headers.get("Content-Length","0"))
            if not 0 <= size <= 100000: raise ValueError("Form exceeds the local response size limit.")
            raw = parse_qs(self.rfile.read(size).decode("utf-8"),keep_blank_values=True)
            values = {k:v[0] for k,v in raw.items()}
            if not secrets.compare_digest(values.pop("csrf",""),TOKEN): return self._reply(403,"Invalid form token","text/plain")
            operation = values.pop("operation")
            session_id = values.get("session_id","")
            kind = values.get("session_kind","debugging")
            if operation == "start_recorded":
                kind = "debugging"
                service.start_recorded(session_id,values["record_id"],kind)
            elif operation == "start_manual":
                service.start_manual(session_id,values["task_text"],values["source"],kind,values.get("initial_response",""),values["assistance"],values.get("attested_actual")=="yes")
            elif operation == "deliver":
                if values.get("delivered") != "yes": raise ValueError("Explicit actual-delivery confirmation is required.")
                service.deliver(session_id,values["action_id"],values["source"],kind)
            elif operation == "respond":
                service.respond(session_id,values["event_id"],values["action_id"],values["task_id"],values["task_version"],values["raw_text"],values["source"],values["assistance"],values.get("attested_actual")=="yes",kind)
            elif operation == "annotate":
                service.annotate(session_id,values["target_event_id"],values["category"],json.loads(values["parsed_fields"]),values["rationale"],values["operator_id"],values["annotation_id"],values.get("supersedes") or None,kind)
            elif operation == "stop": service.stop(session_id,kind)
            else: raise ValueError("Unknown local operation.")
            self.send_response(303)
            self.send_header("Location","/?"+urlencode({"session":session_id,"kind":kind,"message":"Saved. Session history and budget retained."}))
            self.end_headers()
        except (ValueError,KeyError,TypeError) as exc:
            self._reply(400,'<h1>No change accepted</h1><p>'+esc(exc)+'</p><a href="/">Return to inspection</a>')


def serve(port=8520):
    service.verify_history()
    server = ThreadingHTTPServer(("127.0.0.1",port),Handler)
    print(f"Regrouping evidence laboratory: http://127.0.0.1:{port}",flush=True)
    server.serve_forever()
