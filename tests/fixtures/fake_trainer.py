"""A fast stand-in trainer that honours the trainer contract (for tests).

The return curve is a deterministic function of the config:
  return(step) = cap * (1 - exp(-step / scale)), cap = config["cap"] (default 200)
`config["crash_at"]` raises at that step; `config["nan_at"]` writes a NaN loss.
`config["delay"]` sleeps per episode so a test can observe a live run.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from pathlib import Path

stop = False


def _term(signum, frame):  # noqa: ANN001
    global stop
    stop = True


def main() -> int:
    p = argparse.ArgumentParser()
    for name in ("--run-dir", "--run-id", "--config", "--device"):
        p.add_argument(name, required=True)
    for name in ("--seed", "--total-steps", "--threads"):
        p.add_argument(name, type=int, required=True)
    a = p.parse_args()
    signal.signal(signal.SIGTERM, _term)
    cfg = json.loads(a.config)
    cap = float(cfg.get("cap", 200))
    scale = float(cfg.get("scale", a.total_steps / 4))
    delay = float(cfg.get("delay", 0.0))
    path = Path(a.run_dir) / "metrics.jsonl"
    step = 0
    with path.open("a") as out:
        while step < a.total_steps and not stop:
            step += 500
            if cfg.get("crash_at") and step >= cfg["crash_at"]:
                raise RuntimeError("fake trainer crash for the test")
            ret = cap * (1 - math.exp(-step / scale))
            out.write(
                json.dumps(
                    {
                        "run_id": a.run_id,
                        "t": time.time(),
                        "step": step,
                        "kind": "episode",
                        "values": {"return": ret, "length": 100},
                    }
                )
                + "\n"
            )
            loss = "nan" if cfg.get("nan_at") and step >= cfg["nan_at"] else 0.1
            out.write(
                json.dumps(
                    {
                        "run_id": a.run_id,
                        "t": time.time(),
                        "step": step,
                        "kind": "update",
                        "values": {"value_loss": loss, "approx_kl": 0.01, "entropy": 0.6},
                    }
                )
                + "\n"
            )
            out.flush()
            if delay:
                time.sleep(delay)
    print(f"fake trainer {a.run_id} done at {step}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
