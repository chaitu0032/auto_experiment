"""The written finding shown when the loop ends (deterministic template, with an
optional narrative from the decision engine)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from autoexp.core.ids import RunId
from autoexp.core.models import CurveSummary, Finding, PruneVerdict, RunRecord, RunStatus
from autoexp.decision.base import DecisionContext, winner
from autoexp.state.codec import encode
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout


def _fmt_config(config: Mapping[str, object]) -> str:
    return ", ".join(
        f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in sorted(config.items())
    )


def build_finding(
    context: DecisionContext,
    stop_rule: str,
    stop_reason: str,
    prunes: tuple[PruneVerdict, ...],
    narrative: str | None,
) -> Finding:
    runs: tuple[RunRecord, ...] = context.runs
    summaries: Mapping[RunId, CurveSummary] = context.summaries
    found = winner(context)
    lines = [f"Stopped by **{stop_rule}**: {stop_reason}.", ""]
    rounds = max((r.spec.round_index for r in runs), default=0)
    statuses = Counter(r.status.value for r in runs)
    lines.append(
        f"Tried {len(runs)} runs over {rounds} rounds: "
        + ", ".join(f"{n} {s}" for s, n in sorted(statuses.items()))
        + "."
    )
    by_pruner = Counter(p.pruner for p in prunes if p.action.value == "prune")
    if by_pruner:
        lines.append(
            "Pruned by: "
            + ", ".join(f"{name} ({n})" for name, n in sorted(by_pruner.items()))
            + "."
        )
    lines += [
        "",
        "| run | round | purpose | seed | status | best return(100) | at step | config |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        s = summaries.get(r.spec.run_id)
        best = f"{s.best_rolling_return:.1f}" if s and s.best_rolling_return is not None else "-"
        at = str(s.best_at_step) if s and s.best_at_step is not None else "-"
        lines.append(
            f"| {r.spec.run_id} | {r.spec.round_index} | {r.spec.purpose.value} | {r.spec.seed} "
            f"| {r.status.value} | {best} | {at} | {_fmt_config(r.spec.config)} |"
        )
    lines.append("")
    if found is not None:
        lines.append(
            f"**Winner**: {_fmt_config(found.config)} held the goal "
            f"(rolling return >= {context.goal.threshold:g}) on seeds "
            f"{sorted(found.solved_seeds)} (runs {', '.join(found.run_ids)})."
        )
    else:
        solved = [r for r in runs if r.status is RunStatus.SOLVED]
        if solved:
            lines.append(
                "No configuration held the goal on the required "
                f"{context.goal.seeds_required} seeds; solved once: "
                + ", ".join(r.spec.run_id for r in solved)
                + "."
            )
        else:
            lines.append("No run reached the goal.")
    if narrative:
        lines += ["", "### Why (decision agent)", "", narrative]
    return Finding(
        stop_rule=stop_rule,
        winner_config=dict(found.config) if found else None,
        winner_run_ids=tuple(found.run_ids) if found else (),
        tried=len(runs),
        text="\n".join(lines),
    )


def write_finding(layout: StateLayout, finding: Finding) -> None:
    layout.finding_file.write_text(finding.text + "\n", encoding="utf-8")
    write_json_atomic(layout.finding_json, encode(finding))
