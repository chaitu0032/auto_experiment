"""LLM decision engine (seam 1).

The verification protocol (stop on a verified winner, verify candidates on
fresh seeds) is deterministic and runs first. Only the exploration step goes to
the LLM: the prompt holds the search space, the run table with curve
summaries, prune history, budgets and the goal, and asks for JSON
`{proposals[{config, parent_run_id, rationale}], rationale, code_changes[{summary,
diff}]}`. The reply goes through `decision/validate.py`; any error (timeout,
bad JSON, invalid proposal, LLM budget spent) falls back to the deterministic
engine and records why. Code changes never execute here: the master turns them
into approvals.
"""

from __future__ import annotations

import json
from dataclasses import replace

from autoexp.core.models import Decision
from autoexp.decision.base import DecisionContext, DecisionEngine
from autoexp.decision.deterministic import EXPLORATION_SEED, protocol_decision
from autoexp.decision.validate import InvalidDecision, parse_exploration
from autoexp.llm.client import LLMClient, LLMError, extract_json

PROMPT = """You are the decision agent of an automated RL experiment loop.
Goal: rolling mean return over the last {window} episodes >= {threshold}
(CartPole-v1 PPO). You choose the next {slots} hyperparameter configurations to
try. Prefer changes that the evidence supports; explain briefly.

Search space (every config must set every parameter, inside these bounds):
{space}

Runs so far (best_rolling_return is the best {window}-episode mean reached):
{runs}

Prune history:
{prunes}

Budgets: {budget}

Reply with JSON only:
{{"rationale": "why these configs, 1-3 sentences",
  "proposals": [{{"config": {{...}}, "parent_run_id": "r0001 or null",
                  "rationale": "one sentence"}}],
  "code_changes": []}}
Only propose code_changes (summary + unified diff of the trainer) if
hyperparameters cannot fix an observed problem; they need human approval."""

NARRATE = """Write the final finding of an automated RL experiment in 4-8 plain
sentences: what was tried, what worked, and why it likely worked. Be concrete
about hyperparameters. Stop reason: {reason}.

Runs:
{runs}"""


def _space(context: DecisionContext) -> str:
    return json.dumps(
        {
            n: {
                "kind": s.kind.value,
                "low": s.low,
                "high": s.high,
                "values": list(s.values) or None,
            }
            for n, s in context.search_space.items()
        }
    )


def _runs(context: DecisionContext) -> str:
    rows = []
    for record in context.runs:
        summary = context.summaries.get(record.spec.run_id)
        rows.append(
            {
                "run_id": record.spec.run_id,
                "round": record.spec.round_index,
                "purpose": record.spec.purpose.value,
                "seed": record.spec.seed,
                "config": dict(record.spec.config),
                "status": record.status.value,
                "stop_reason": record.stop_reason,
                "steps": summary.step if summary else 0,
                "best_rolling_return": round(summary.best_rolling_return, 1)
                if summary and summary.best_rolling_return is not None
                else None,
                "best_at_step": summary.best_at_step if summary else None,
                "final_rolling_return": round(summary.rolling_return, 1)
                if summary and summary.rolling_return is not None
                else None,
            }
        )
    return json.dumps(rows, indent=1)


def build_prompt(context: DecisionContext) -> str:
    return PROMPT.format(
        window=context.goal.window,
        threshold=context.goal.threshold,
        slots=context.free_slots,
        space=_space(context),
        runs=_runs(context),
        prunes=json.dumps(
            [{"run_id": p.run_id, "pruner": p.pruner, "reason": p.reason} for p in context.prunes]
        ),
        budget=json.dumps(
            {line.name: [round(line.used, 2), line.limit] for line in context.budget.lines}
        ),
    )


class LLMDecisionEngine:
    name = "llm"

    def __init__(self, client: LLMClient, fallback: DecisionEngine) -> None:
        self._client = client
        self._fallback = fallback

    def decide(self, context: DecisionContext) -> Decision:
        protocol = protocol_decision(context, self.name)
        if protocol is not None:
            return protocol
        if context.free_slots <= 0:
            return self._fallback.decide(context)
        try:
            reply = self._client.complete(build_prompt(context), purpose="decision")
            raw = extract_json(reply.text)
            return parse_exploration(raw, context, self.name, [EXPLORATION_SEED])
        except (LLMError, InvalidDecision) as err:
            fallback = self._fallback.decide(context)
            return replace(fallback, fallback_reason=f"{type(err).__name__}: {err}"[:300])

    def narrate(self, context: DecisionContext, stop_reason: str) -> str | None:
        try:
            reply = self._client.complete(
                NARRATE.format(reason=stop_reason, runs=_runs(context)), purpose="narrative"
            )
        except LLMError:
            return None
        text = reply.text.strip()
        return text[:4000] or None
