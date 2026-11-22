"""CleanRL-style single-file PPO for Gymnasium CartPole-v1, CPU only.

Hyperparameters come from --config (learning_rate, num_envs, num_steps,
ent_coef, clip_coef, update_epochs, and optionally gamma, gae_lambda,
num_minibatches, vf_coef, max_grad_norm, anneal_lr, hidden). Emits episode,
update, eval and system telemetry through TelemetryWriter, saves checkpoints,
and honours SIGTERM (finish the current update, flush, exit 0).
Requires the `train` extra (torch, gymnasium, numpy).
"""

from __future__ import annotations

import argparse
import json
import resource
import signal
import sys
import time
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULTS = {
    "learning_rate": 2.5e-4,
    "num_envs": 4,
    "num_steps": 128,
    "gamma": 0.99,
    "gae_lambda": 0.95,
    "num_minibatches": 4,
    "update_epochs": 4,
    "clip_coef": 0.2,
    "ent_coef": 0.01,
    "vf_coef": 0.5,
    "max_grad_norm": 0.5,
    "anneal_lr": True,
    "hidden": 64,
    "eval_every_updates": 50,
    "eval_episodes": 5,
    "checkpoint_every_updates": 100,
}


@dataclass(frozen=True)
class Hyper:
    learning_rate: float
    num_envs: int
    num_steps: int
    gamma: float
    gae_lambda: float
    num_minibatches: int
    update_epochs: int
    clip_coef: float
    ent_coef: float
    vf_coef: float
    max_grad_norm: float
    anneal_lr: bool
    hidden: int
    eval_every_updates: int
    eval_episodes: int
    checkpoint_every_updates: int


def parse_hyper(raw: dict[str, object]) -> Hyper:
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown hyperparameters: {sorted(unknown)}")
    merged = {**DEFAULTS, **raw}
    values = {}
    for f in fields(Hyper):
        default = DEFAULTS[f.name]
        value = merged[f.name]
        values[f.name] = type(default)(value)  # type: ignore[call-arg]
    return Hyper(**values)


class _Stop:
    requested = False


def _on_term(signum: int, frame: object) -> None:
    _Stop.requested = True


def _rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--total-steps", type=int, required=True)
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps"])
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    hyper = parse_hyper(json.loads(args.config))
    return train(args, hyper)


def train(args: argparse.Namespace, hp: Hyper) -> int:  # noqa: PLR0915 - one PPO loop
    import gymnasium as gym
    import numpy as np
    import torch
    from torch import nn, optim
    from torch.distributions.categorical import Categorical

    from autoexp.core.ids import RunId
    from autoexp.telemetry.writer import TelemetryWriter

    torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = torch.device(args.device)
    run_dir = Path(args.run_dir)
    ckpt_dir = run_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    envs = gym.vector.SyncVectorEnv(
        [
            lambda: gym.wrappers.RecordEpisodeStatistics(gym.make("CartPole-v1"))
            for _ in range(hp.num_envs)
        ]
    )
    obs_dim = int(np.prod(envs.single_observation_space.shape))
    n_actions = int(envs.single_action_space.n)

    def layer(i: int, o: int, std: float = 2**0.5) -> nn.Linear:
        lin = nn.Linear(i, o)
        nn.init.orthogonal_(lin.weight, std)
        nn.init.constant_(lin.bias, 0.0)
        return lin

    class Agent(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            h = hp.hidden
            self.critic = nn.Sequential(
                layer(obs_dim, h), nn.Tanh(), layer(h, h), nn.Tanh(), layer(h, 1, 1.0)
            )
            self.actor = nn.Sequential(
                layer(obs_dim, h), nn.Tanh(), layer(h, h), nn.Tanh(), layer(h, n_actions, 0.01)
            )

        def value(self, x: torch.Tensor) -> torch.Tensor:
            return self.critic(x)

        def act(self, x: torch.Tensor, action: torch.Tensor | None = None):  # type: ignore[no-untyped-def]
            dist = Categorical(logits=self.actor(x))
            if action is None:
                action = dist.sample()
            return action, dist.log_prob(action), dist.entropy(), self.critic(x)

    agent = Agent().to(device)
    optimizer = optim.Adam(agent.parameters(), lr=hp.learning_rate, eps=1e-5)
    batch = hp.num_envs * hp.num_steps
    minibatch = max(1, batch // hp.num_minibatches)
    num_updates = max(1, args.total_steps // batch)

    obs_buf = torch.zeros((hp.num_steps, hp.num_envs, obs_dim), device=device)
    act_buf = torch.zeros((hp.num_steps, hp.num_envs), device=device)
    logp_buf = torch.zeros((hp.num_steps, hp.num_envs), device=device)
    rew_buf = torch.zeros((hp.num_steps, hp.num_envs), device=device)
    done_buf = torch.zeros((hp.num_steps, hp.num_envs), device=device)
    val_buf = torch.zeros((hp.num_steps, hp.num_envs), device=device)

    writer = TelemetryWriter(run_dir / "metrics.jsonl", RunId(args.run_id))
    step = 0
    started = time.time()
    cpu_start = time.process_time()
    next_obs_np, _ = envs.reset(seed=args.seed)
    next_obs = torch.as_tensor(next_obs_np, dtype=torch.float32, device=device)
    next_done = torch.zeros(hp.num_envs, device=device)
    print(
        f"[{args.run_id}] PPO CartPole-v1 seed={args.seed} updates={num_updates} batch={batch}",
        flush=True,
    )

    with writer:
        for update in range(1, num_updates + 1):
            if _Stop.requested:
                print(f"[{args.run_id}] stop requested; exiting after {step} steps", flush=True)
                break
            if hp.anneal_lr:
                optimizer.param_groups[0]["lr"] = hp.learning_rate * (
                    1.0 - (update - 1.0) / num_updates
                )
            for t in range(hp.num_steps):
                step += hp.num_envs
                obs_buf[t] = next_obs
                done_buf[t] = next_done
                with torch.no_grad():
                    action, logp, _, value = agent.act(next_obs)
                val_buf[t] = value.flatten()
                act_buf[t] = action
                logp_buf[t] = logp
                obs_np, reward, term, trunc, infos = envs.step(action.cpu().numpy())
                rew_buf[t] = torch.as_tensor(reward, dtype=torch.float32, device=device)
                next_obs = torch.as_tensor(obs_np, dtype=torch.float32, device=device)
                next_done = torch.as_tensor(
                    np.logical_or(term, trunc), dtype=torch.float32, device=device
                )
                if "episode" in infos:
                    mask = infos.get("_episode", np.ones(hp.num_envs, dtype=bool))
                    for i in np.nonzero(mask)[0]:
                        writer.episode(
                            step, float(infos["episode"]["r"][i]), int(infos["episode"]["l"][i])
                        )

            with torch.no_grad():
                next_value = agent.value(next_obs).reshape(1, -1)
                adv = torch.zeros_like(rew_buf)
                last = 0
                for t in reversed(range(hp.num_steps)):
                    nonterminal = 1.0 - (next_done if t == hp.num_steps - 1 else done_buf[t + 1])
                    nv = next_value if t == hp.num_steps - 1 else val_buf[t + 1]
                    delta = rew_buf[t] + hp.gamma * nv * nonterminal - val_buf[t]
                    last = delta + hp.gamma * hp.gae_lambda * nonterminal * last
                    adv[t] = last
                returns = adv + val_buf

            b_obs = obs_buf.reshape((-1, obs_dim))
            b_logp, b_act = logp_buf.reshape(-1), act_buf.reshape(-1)
            b_adv, b_ret, b_val = adv.reshape(-1), returns.reshape(-1), val_buf.reshape(-1)
            idx = np.arange(batch)
            clipfracs, grad_norm = [], 0.0
            for _ in range(hp.update_epochs):
                np.random.shuffle(idx)
                for start in range(0, batch, minibatch):
                    mb = idx[start : start + minibatch]
                    _, newlogp, entropy, newv = agent.act(b_obs[mb], b_act.long()[mb])
                    ratio = (newlogp - b_logp[mb]).exp()
                    with torch.no_grad():
                        approx_kl = ((ratio - 1) - (newlogp - b_logp[mb])).mean()
                        clipfracs.append(((ratio - 1.0).abs() > hp.clip_coef).float().mean().item())
                    mb_adv = b_adv[mb]
                    mb_adv = (mb_adv - mb_adv.mean()) / (mb_adv.std() + 1e-8)
                    pg_loss = torch.max(
                        -mb_adv * ratio,
                        -mb_adv * torch.clamp(ratio, 1 - hp.clip_coef, 1 + hp.clip_coef),
                    ).mean()
                    v_loss = 0.5 * ((newv.view(-1) - b_ret[mb]) ** 2).mean()
                    ent = entropy.mean()
                    loss = pg_loss - hp.ent_coef * ent + hp.vf_coef * v_loss
                    optimizer.zero_grad()
                    loss.backward()
                    grad_norm = float(
                        nn.utils.clip_grad_norm_(agent.parameters(), hp.max_grad_norm)
                    )
                    optimizer.step()

            var_y = float(b_ret.var())
            explained = float("nan") if var_y == 0 else 1 - float((b_ret - b_val).var()) / var_y
            elapsed = time.time() - started
            writer.update(
                step,
                policy_loss=pg_loss.item(),
                value_loss=v_loss.item(),
                entropy=ent.item(),
                approx_kl=approx_kl.item(),
                clipfrac=float(np.mean(clipfracs)),
                grad_norm=grad_norm,
                lr=float(optimizer.param_groups[0]["lr"]),
                explained_variance=explained if explained == explained else 0.0,
                sps=step / max(elapsed, 1e-6),
            )
            if update % 10 == 0:
                cpu = (time.process_time() - cpu_start) / max(elapsed, 1e-6) * 100
                writer.system(step, cpu_percent=cpu, rss_mb=_rss_mb(), threads=args.threads)
                print(
                    f"[{args.run_id}] step {step} sps {step / max(elapsed, 1e-6):.0f}", flush=True
                )
            if update % hp.eval_every_updates == 0 or update == num_updates:
                mean, std = _evaluate(agent, args.seed + update, hp.eval_episodes, device)
                writer.eval(step, mean, std, hp.eval_episodes, video_path=None)
            if update % hp.checkpoint_every_updates == 0 or update == num_updates:
                torch.save({"step": step, "model": agent.state_dict()}, ckpt_dir / "latest.pt")
        torch.save({"step": step, "model": agent.state_dict()}, ckpt_dir / "latest.pt")
    envs.close()
    print(f"[{args.run_id}] done at step {step}", flush=True)
    return 0


def _evaluate(agent, seed: int, episodes: int, device) -> tuple[float, float]:  # type: ignore[no-untyped-def]
    import gymnasium as gym
    import numpy as np
    import torch

    env = gym.make("CartPole-v1")
    returns = []
    for ep in range(episodes):
        obs, _ = env.reset(seed=seed + ep)
        done, total = False, 0.0
        while not done:
            with torch.no_grad():
                logits = agent.actor(torch.as_tensor(obs, dtype=torch.float32, device=device))
            obs, reward, term, trunc, _ = env.step(int(logits.argmax()))
            total += float(reward)
            done = term or trunc
        returns.append(total)
    env.close()
    return float(np.mean(returns)), float(np.std(returns))


if __name__ == "__main__":
    raise SystemExit(main())
