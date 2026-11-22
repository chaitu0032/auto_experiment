"""Command surface (docs/architecture.md 7)."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from autoexp.config.loader import ConfigError, load_config
from autoexp.config.schema import DashboardConfig, EngineKind, WorkerRuntimeKind
from autoexp.core.ids import RunId, validate_worker_id
from autoexp.core.models import ApprovalStatus, WorkerState
from autoexp.state.approvals import ApprovalError, FileApprovalStore
from autoexp.state.jsonl import read_json
from autoexp.state.layout import StateLayout
from autoexp.state.locks import FlockLockManager, LockHeld

EXIT_USAGE = 2
EXIT_LOCKED = 3


class CliError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoexp", description="RL experiment master/worker.")
    parser.add_argument(
        "--state-dir",
        default="autoexp-state",
        help="state root, or a directory holding one experiment per subdirectory",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the master and the dashboard")
    up.add_argument("--config", required=True)
    up.add_argument(
        "--no-herdr", action="store_true", help="do not open panes; start workers by hand"
    )
    up.add_argument("--engine", choices=[e.value for e in EngineKind])
    up.add_argument("--runtime", choices=[r.value for r in WorkerRuntimeKind])
    up.add_argument("--no-dashboard", action="store_true")
    up.add_argument("--port", type=int)

    sub.add_parser("status", help="text summary of runs, budgets, approvals")
    sub.add_parser("stop", help="set the user stop flag")
    for verb in ("approve", "reject"):
        answer = sub.add_parser(verb, help=f"{verb} a pending approval")
        answer.add_argument("approval_id")
        answer.add_argument("--note", default="")
    dash = sub.add_parser("dashboard", help="serve the dashboard only")
    dash.add_argument("--port", type=int, default=8765)

    worker = sub.add_parser("worker", help="worker-side commands")
    wsub = worker.add_subparsers(dest="worker_command", required=True)
    for name in ("handle", "serve"):
        cmd = wsub.add_parser(name)
        cmd.add_argument("--worker", required=True)
    report = wsub.add_parser("report")
    report.add_argument("--worker", required=True)
    report.add_argument("--state", required=True, choices=["idle", "working", "blocked", "done"])
    report.add_argument("--run")
    report.add_argument("--verdict", choices=["retry", "unfixable"])
    report.add_argument("note", nargs="?", default="")
    takeover = wsub.add_parser("takeover")
    takeover.add_argument("worker_id")
    takeover.add_argument("mode", choices=["on", "off"])

    pane = sub.add_parser("pane", help="read or type into a worker pane (as the master)")
    psub = pane.add_subparsers(dest="pane_command", required=True)
    pread = psub.add_parser("read")
    pread.add_argument("worker_id")
    pread.add_argument("--lines", type=int, default=40)
    psend = psub.add_parser("send")
    psend.add_argument("worker_id")
    psend.add_argument("text")

    run_exec = sub.add_parser("run-exec", help="run supervisor for one run (spawned by a worker)")
    run_exec.add_argument("run_id")
    return parser


def resolve_root(state_dir: str) -> Path:
    """The experiment state root: `state_dir` itself or its only experiment."""
    base = Path(state_dir).resolve()
    if (base / "experiment.json").exists():
        return base
    found = sorted(p for p in base.glob("*/experiment.json")) if base.exists() else []
    if len(found) == 1:
        return found[0].parent
    if not found:
        raise CliError(f"no experiment state under {base}; run `autoexp up` first")
    names = ", ".join(p.parent.name for p in found)
    raise CliError(f"several experiments under {base} ({names}); pass --state-dir <one of them>")


# ------------------------------------------------------------------ commands


def cmd_up(args: argparse.Namespace) -> int:
    from autoexp.dashboard import server as dashboard
    from autoexp.herdr.cli_adapter import CliHerdrAdapter
    from autoexp.master.app import BuildOptions, build_master, write_experiment
    from autoexp.master.loop import MasterAlreadyFinished

    config_path = Path(args.config)
    config = load_config(config_path)
    if args.port:
        config = replace(config, dashboard=replace(config.dashboard, port=args.port))
    base = Path(args.state_dir).resolve()
    root = base if (base / "experiment.json").exists() else base / config.experiment_id
    root.mkdir(parents=True, exist_ok=True)
    layout = StateLayout(root)
    write_experiment(layout, config, config_path.read_text(encoding="utf-8"))
    herdr = None if args.no_herdr else CliHerdrAdapter(config.herdr.command, config.herdr.session)
    options = BuildOptions(
        herdr=herdr,
        cwd=str(Path.cwd()),
        engine=EngineKind(args.engine) if args.engine else None,
        runtime=WorkerRuntimeKind(args.runtime) if args.runtime else None,
    )
    master = build_master(config, root, options)
    server = None
    if not args.no_dashboard:
        server = dashboard.start(layout, config.dashboard)
        print(f"dashboard: http://{config.dashboard.host}:{config.dashboard.port}/", flush=True)
    print(f"state: {root}", flush=True)
    try:
        verdict = master.run()
    except MasterAlreadyFinished as err:
        print(str(err))
        return 0
    finally:
        if server is not None:
            dashboard.stop(server)
    print(f"stopped: {verdict.rule}: {verdict.reason}")
    if layout.finding_file.exists():
        print(layout.finding_file.read_text(encoding="utf-8"))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from autoexp.dashboard.handlers import get_overview

    overview = get_overview(StateLayout(resolve_root(args.state_dir)))
    print(
        f"{overview['experiment_id']}: master {overview['master']}, round {overview['round_index']}"
    )
    for run in overview["runs"]:
        best = run["best_rolling_return"]
        print(
            f"  {run['run_id']} r{run['round_index']} {run['purpose']:<10} {run['status']:<9} "
            f"step {run['step']:>7} best {best if best is None else round(best, 1)} "
            f"{run['stop_reason'] or ''}"
        )
    if overview["budget"]:
        for line in overview["budget"]["lines"]:
            print(f"  budget {line['name']}: {line['used']:.1f} / {line['limit']:.1f}")
    for approval in overview["approvals"]:
        if approval["status"] == "pending":
            print(f"  PENDING {approval['approval_id']}: {approval['summary']}")
    if overview["finding"]:
        print("\n" + overview["finding"]["text"])
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    layout = StateLayout(resolve_root(args.state_dir))
    layout.stop_flag.write_text(f"requested from the CLI at {time.time()}\n", encoding="utf-8")
    print("stop requested; the master stops on its next tick")
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    store = FileApprovalStore(StateLayout(resolve_root(args.state_dir)))
    status = ApprovalStatus.APPROVED if args.command == "approve" else ApprovalStatus.REJECTED
    try:
        result = store.answer(args.approval_id, status, args.note)
    except ApprovalError as err:
        raise CliError(str(err)) from None
    print(f"{result.approval_id}: {result.status.value}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from autoexp.dashboard import server as dashboard

    layout = StateLayout(resolve_root(args.state_dir))
    config = DashboardConfig(host="127.0.0.1", port=args.port)
    print(f"dashboard: http://{config.host}:{config.port}/", flush=True)
    dashboard.serve(layout, config)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from autoexp.state.inbox import FileInbox
    from autoexp.worker.handler import handle_inbox
    from autoexp.worker.serve import serve
    from autoexp.worker.state import report, set_takeover

    layout = StateLayout(resolve_root(args.state_dir))
    if args.worker_command == "takeover":
        worker_id = validate_worker_id(args.worker_id)
        set_takeover(layout, worker_id, args.mode == "on")
        print(f"{worker_id}: takeover {args.mode}")
        return 0
    worker_id = validate_worker_id(args.worker)
    if args.worker_command == "handle":
        result = handle_inbox(layout, FileInbox(layout), worker_id)
        for line in result.lines or ("inbox empty",):
            print(line)
        for message in result.needs_agent:
            print(f"NEEDS YOU: {message.kind.value} {message.msg_id}: {dict(message.body)}")
        return 1 if result.errors else 0
    if args.worker_command == "serve":
        serve(layout, worker_id)
        return 0
    run_id = RunId(args.run) if args.run else None
    report(layout, worker_id, WorkerState(args.state), run_id, args.note, args.verdict)
    print(f"reported {args.state}" + (f" for {run_id}" if run_id else ""))
    return 0


def cmd_pane(args: argparse.Namespace) -> int:
    import tomllib

    from autoexp.config.loader import parse_config
    from autoexp.herdr.cli_adapter import CliHerdrAdapter
    from autoexp.master.steering import Steering
    from autoexp.state.inbox import FileInbox
    from autoexp.worker.state import read_worker

    layout = StateLayout(resolve_root(args.state_dir))
    experiment = read_json(layout.experiment_file) or {}
    config = parse_config(tomllib.loads(str(experiment.get("config_toml", ""))))
    worker = read_worker(layout, validate_worker_id(args.worker_id))
    if worker is None:
        raise CliError(f"no worker {args.worker_id}")
    herdr = CliHerdrAdapter(config.herdr.command, config.herdr.session)
    steering = Steering(FileInbox(layout), herdr, agent_runtime=True)
    if args.pane_command == "read":
        print(steering.read_context(worker, args.lines))
        return 0
    allowed, reason = steering.can_type(worker)
    if not allowed:
        raise CliError(f"not typing into {worker.worker_id}: {reason}")
    steering.type_line(worker, args.text)
    print(f"typed into {worker.worker_id} ({worker.pane_id})")
    return 0


def cmd_run_exec(args: argparse.Namespace) -> int:
    from autoexp.worker.supervisor import SupervisorError, supervise

    layout = StateLayout(resolve_root(args.state_dir))
    try:
        outcome = supervise(layout, FlockLockManager(), RunId(args.run_id))
    except SupervisorError as err:
        raise CliError(str(err)) from None
    print(f"{outcome.run_id}: {outcome.status.value} ({outcome.reason})")
    return 0


COMMANDS = {
    "up": cmd_up,
    "status": cmd_status,
    "stop": cmd_stop,
    "approve": cmd_answer,
    "reject": cmd_answer,
    "dashboard": cmd_dashboard,
    "worker": cmd_worker,
    "pane": cmd_pane,
    "run-exec": cmd_run_exec,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return COMMANDS[args.command](args)
    except LockHeld as err:
        sys.stderr.write(f"autoexp: {err}\n")
        return EXIT_LOCKED
    except (CliError, ConfigError, ValueError) as err:
        sys.stderr.write(f"autoexp: {err}\n")
        return EXIT_USAGE
