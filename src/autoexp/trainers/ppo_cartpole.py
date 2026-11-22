"""CleanRL-style single-file PPO for Gymnasium CartPole-v1, CPU only.

Hyperparameters come from --config (learning_rate, num_envs, num_steps,
ent_coef, clip_coef, update_epochs, ...). Emits episode, update, eval and
system telemetry through TelemetryWriter and honours SIGTERM.
Requires the `train` extra (torch, gymnasium, numpy).
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
