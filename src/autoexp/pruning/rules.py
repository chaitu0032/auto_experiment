"""Deterministic per-run rules: divergence and plateau."""

from __future__ import annotations

from autoexp.config.schema import DivergenceConfig, PlateauConfig
from autoexp.core.models import CurveSummary, PruneAction, PruneVerdict
from autoexp.pruning.base import Cohort
from autoexp.telemetry.summary import best_until


def _verdict(
    run_id: str, pruner: str, action: PruneAction, reason: str, **evidence: object
) -> PruneVerdict:
    clean = {k: v for k, v in evidence.items() if v is not None}
    return PruneVerdict(run_id=run_id, pruner=pruner, action=action, reason=reason, evidence=clean)  # type: ignore[arg-type]


class DivergencePruner:
    """Prune on NaN/inf, approx KL above kl_max for kl_patience updates, or value
    loss above value_loss_max."""

    name = "divergence"

    def __init__(self, config: DivergenceConfig) -> None:
        self._config = config

    def judge(self, summary: CurveSummary) -> PruneVerdict:
        c = self._config
        rid = summary.run_id
        if summary.nan_seen:
            return _verdict(
                rid, self.name, PruneAction.PRUNE, "NaN/inf in telemetry", step=summary.step
            )
        if summary.kl_over_limit_streak >= c.kl_patience:
            return _verdict(
                rid,
                self.name,
                PruneAction.PRUNE,
                f"approx KL above {c.kl_max} for {summary.kl_over_limit_streak} updates",
                approx_kl=summary.approx_kl,
                step=summary.step,
            )
        if summary.value_loss is not None and summary.value_loss > c.value_loss_max:
            return _verdict(
                rid,
                self.name,
                PruneAction.PRUNE,
                f"value loss {summary.value_loss:.3g} above {c.value_loss_max:.3g}",
                value_loss=summary.value_loss,
                step=summary.step,
            )
        return _verdict(rid, self.name, PruneAction.KEEP, "no divergence")

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        return tuple(self.judge(o.summary) for o in cohort.observations if o.summary is not None)


class PlateauPruner:
    """Prune when the best rolling return gained < min_delta over the last
    patience_steps, once past min_steps, unless the goal is reached (that is a
    SOLVED verdict instead)."""

    name = "plateau"

    def __init__(self, config: PlateauConfig) -> None:
        self._config = config

    def judge(self, summary: CurveSummary, goal_threshold: float) -> PruneVerdict:
        c = self._config
        rid = summary.run_id
        best = summary.best_rolling_return
        if best is not None and best >= goal_threshold:
            return _verdict(
                rid,
                self.name,
                PruneAction.SOLVED,
                f"rolling return {best:.1f} reached goal {goal_threshold:g}",
                best_rolling_return=best,
                step=summary.best_at_step,
            )
        if summary.step < c.min_steps or best is None:
            return _verdict(rid, self.name, PruneAction.KEEP, "too early to judge a plateau")
        earlier = best_until(summary, summary.step - c.patience_steps)
        if earlier is None:
            return _verdict(rid, self.name, PruneAction.KEEP, "not enough history")
        gain = best - earlier
        if gain < c.min_delta:
            return _verdict(
                rid,
                self.name,
                PruneAction.PRUNE,
                f"plateau: best return gained {gain:.1f} (< {c.min_delta:g}) "
                f"over the last {c.patience_steps} steps",
                best_rolling_return=best,
                gain=gain,
                step=summary.step,
            )
        return _verdict(rid, self.name, PruneAction.KEEP, f"still improving (+{gain:.1f})")

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        return tuple(
            self.judge(o.summary, cohort.goal_threshold)
            for o in cohort.observations
            if o.summary is not None
        )
