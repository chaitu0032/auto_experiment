"""Command surface (docs/architecture.md 7).

The parser is real; command handlers are stubs until the build phase and exit
with a clear message instead of a traceback.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoexp", description=__doc__)
    parser.add_argument("--state-dir", default="autoexp-state", help="state root directory")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the master and the dashboard")
    up.add_argument("--config", required=True)

    sub.add_parser("status", help="text summary of runs, budgets, approvals")
    sub.add_parser("stop", help="set the user stop flag")
    for verb in ("approve", "reject"):
        answer = sub.add_parser(verb, help=f"{verb} a pending approval")
        answer.add_argument("approval_id")
    sub.add_parser("dashboard", help="serve the dashboard only")

    worker = sub.add_parser("worker", help="worker-side commands")
    wsub = worker.add_subparsers(dest="worker_command", required=True)
    for name in ("handle", "serve"):
        cmd = wsub.add_parser(name)
        cmd.add_argument("--worker", required=True)
    report = wsub.add_parser("report")
    report.add_argument("--worker", required=True)
    report.add_argument("--state", required=True, choices=["idle", "working", "blocked", "done"])
    report.add_argument("--run")
    report.add_argument("note", nargs="?", default="")
    takeover = wsub.add_parser("takeover")
    takeover.add_argument("worker_id")
    takeover.add_argument("mode", choices=["on", "off"])

    run_exec = sub.add_parser("run-exec", help="run supervisor for one run (spawned by a worker)")
    run_exec.add_argument("run_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    name = args.command if args.command != "worker" else f"worker {args.worker_command}"
    sys.stderr.write(f"autoexp {name}: not implemented yet (architecture skeleton)\n")
    return 2
