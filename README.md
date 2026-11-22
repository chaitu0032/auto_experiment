# auto_experiment

A master/worker system for reinforcement-learning experiments on one machine.
One master decides what to run, watches runs while they train, prunes weak
ones and decides the next step; workers in Herdr panes execute and debug the
runs; a local dashboard shows what has happened.

The design is in [docs/architecture.md](docs/architecture.md).

![Dashboard after the first milestone: 4 CartPole configs, 3 pruned by successive halving, the winner verified on 3 seeds](docs/images/milestone-dashboard.png)

## How it works

- `autoexp up` starts the **master**: it takes the master lock, opens one
  Herdr pane per **worker** (Claude Code by default), and serves the
  **dashboard** on `http://127.0.0.1:8765/`.
- Every experiment gets a **run ID** (`r0001`, ...). Commands, logs,
  checkpoints, telemetry and dashboard rows are keyed by it.
- The master steers workers through **durable inbox files** and a nudge typed
  into their pane; workers report back as **event lines**. Each assigned run
  is executed by a detached **run supervisor** that holds the run lock and a
  device lock, so a run is never driven twice and two runs never share a
  device.
- Each tick the master **observes** telemetry (JSONL), **prunes** (divergence,
  plateau, successive halving / ASHA, optional LLM judge), checks **budgets and
  stop rules**, and when a round has settled **decides** the next one (LLM
  proposal with a deterministic fallback; a solver is always re-run on fresh
  seeds before it is called the winner).
- Training-code changes and budget overruns become **pending approvals** on the
  dashboard; the loop keeps going meanwhile.
- Everything lives under the state directory, so killing and restarting the
  master recovers every run. Anything a human types into a worker pane wins
  over the master.

## Quick start

Requires macOS or Linux, Python 3.11+, [uv](https://docs.astral.sh/uv/),
Herdr (terminal workspace manager) for worker panes, and Claude Code
for the agent workers and the LLM decision engine.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[train,dev]"

# The first milestone: 4 CartPole PPO configurations, 2 workers x 2 runs.
.venv/bin/autoexp up --config configs/cartpole.toml
```

Without Herdr or Claude Code:

```sh
.venv/bin/autoexp up --config configs/cartpole.toml --no-herdr --engine deterministic --runtime shell
# in two other terminals:
.venv/bin/autoexp --state-dir autoexp-state/cartpole-v1 worker serve --worker w1
.venv/bin/autoexp --state-dir autoexp-state/cartpole-v1 worker serve --worker w2
```

Other commands: `autoexp status`, `autoexp stop`, `autoexp approve|reject <id>`,
`autoexp dashboard`, `autoexp pane read|send <worker>`, `autoexp worker
takeover <worker> on|off`. To point the master at a specific Herdr session or a
wrapper, set `herdr.session` in the config or `AUTOEXP_HERDR_COMMAND` in the
environment.

Any program that follows the trainer contract in
[`src/autoexp/trainers/base.py`](src/autoexp/trainers/base.py) (write
telemetry JSONL, honour SIGTERM) can replace the bundled PPO trainer.

## Layout

```text
docs/architecture.md   the design
configs/cartpole.toml  first milestone: 4 CartPole PPO configurations
src/autoexp/
  core/        run/worker/device ids, data models
  config/      typed config schema and loader
  state/       state layout, JSONL logs, registry, locks, inbox, approvals, recovery
  telemetry/   trainer-side writer, incremental reader, curve summaries
  observe/     observability: telemetry + logs + pane context -> observations
  pruning/     divergence, plateau, successive halving / ASHA, LLM-judge seam
  decision/    verification protocol, deterministic engine, LLM engine seam, validation
  budget/      budget ledger, stop rules
  llm/         the single metered LLM gateway
  herdr/       Herdr adapter (CLI), a null adapter, and a fake for tests
  master/      control loop and its phases, scheduler, steering, panes, finding
  worker/      pane runtimes, inbox handler, shell worker loop, run supervisor, brief
  trainers/    trainer contract, CleanRL-style PPO for CartPole
  dashboard/   Python JSON API over the state directory (routes, handlers, schemas)
    web/       JavaScript frontend: index.html, ES modules, SVG charts (no build step)
tests/         Python tests; tests/web/ holds frontend tests run with node --test
```

## Development

```sh
.venv/bin/python -m pytest -q          # deterministic parts, no Herdr or LLM needed
npm test                               # frontend modules (Node 20+, no dependencies)
uvx ruff check src tests && uvx ruff format --check src tests
```

## License

MIT, see [LICENSE](LICENSE).
