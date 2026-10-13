"""Run from the project directory: python -m adaptive_hints_v03 [command]."""
import argparse
import json


def main():
    parser=argparse.ArgumentParser(description="Bounded regrouping evidence laboratory; no learner simulation.")
    sub=parser.add_subparsers(dest="command")
    server=sub.add_parser("serve"); server.add_argument("--port",type=int,default=8520)
    sub.add_parser("inspect",help="Initial decisions on frozen development data only")
    history=sub.add_parser("verify-history",help="Require bundled historical hashes; optionally audit external originals")
    history.add_argument("--compare-originals",action="store_true",help="Report per-file status of external original workspaces (never gates setup)")
    trace=sub.add_parser("trace"); trace.add_argument("--record",required=True)
    args=parser.parse_args()
    if args.command in {None,"serve"}:
        from .web import serve
        serve(getattr(args,"port",8520))
    elif args.command=="verify-history":
        from .data import verify_history
        print(json.dumps(verify_history(compare_originals=args.compare_originals),indent=2))
    elif args.command=="inspect":
        from .inspection import run_inspection
        result=run_inspection()
        print(json.dumps({key:result[key] for key in ("status","aggregate","human_content_review","new_real_followup_observations")},indent=2))
    else:
        from .service import preview
        print(json.dumps(preview(args.record),indent=2,ensure_ascii=False))


if __name__=="__main__": main()
