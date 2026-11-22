# auto_experiment

A master/worker system for reinforcement-learning experiments on one machine.
One master decides what to run, watches runs while they train, prunes weak
ones and decides the next step; workers in Herdr panes execute and debug the
runs; a local dashboard shows what has happened.

**Status: architecture skeleton for review.** The design is in
[docs/architecture.md](docs/architecture.md). The package under
`src/autoexp/` is that design as code: data models, interfaces and module
stubs. Most behaviour is not implemented yet.

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
  decision/    deterministic engine, LLM engine seam, validation
  budget/      budget ledger, stop rules
  llm/         the single metered LLM gateway
  herdr/       Herdr adapter (CLI) and a fake for tests
  master/      control loop, scheduler, steering, event log
  worker/      pane runtimes, inbox handler, run supervisor, worker brief
  trainers/    trainer contract, CleanRL-style PPO for CartPole
  dashboard/   Python JSON API over the state directory (routes, handlers, schemas)
    web/       JavaScript frontend: index.html, ES modules, SVG charts (no build step)
tests/         Python tests; tests/web/ holds frontend tests run with node --test
```

## Development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv run --python 3.12 --with pytest --no-project python -m pytest -q
npm test   # or: node --test tests/web/*.test.mjs  (Node 20+, no dependencies)
```

Training needs the `train` extra (`torch`, `gymnasium`, `numpy`).

## License

MIT, see [LICENSE](LICENSE).
