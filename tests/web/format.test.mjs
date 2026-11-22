// Pure formatting helpers. Run: npm test
import assert from "node:assert/strict";
import { test } from "node:test";

import { configText, duration, int, num, pct } from "../../src/autoexp/dashboard/web/js/format.js";

test("numbers render with a dash for missing values", () => {
  assert.equal(num(null), "—");
  assert.equal(num(NaN), "—");
  assert.equal(num(1.234, 2), "1.23");
  assert.equal(int(undefined), "—");
  assert.equal(pct(0.256), "26%");
  assert.equal(pct(undefined), "0%");
});

test("durations and configs", () => {
  assert.equal(duration(65), "1m 5s");
  assert.equal(duration(9), "9s");
  assert.equal(duration(null), "—");
  assert.equal(configText({ lr: 0.001, envs: 4 }), "lr=0.001 envs=4");
  assert.equal(configText(null), "");
});

test("feed detail shows evidence, not the repeated reason", async () => {
  globalThis.document ??= undefined; // feed.js imports dom.js but detail() is pure
  const { detail } = await import("../../src/autoexp/dashboard/web/js/components/feed.js");
  assert.equal(
    detail({ kind: "prune", data: { reason: "x", evidence: { rank: 3, of: 4, value: 364.2123 } } }),
    "rank=3 of=4 value=364.212",
  );
  assert.equal(
    detail({ kind: "decision", data: { proposals: [{ purpose: "seed_check", seed: 2 }] } }),
    "proposals: seed_check seed 2",
  );
  assert.equal(detail({ kind: "stopped", data: {} }), "");
});

test("chart ticks keep small values readable", async () => {
  const { tick } = await import("../../src/autoexp/dashboard/web/js/charts/lineChart.js");
  assert.equal(tick(347.2), "347");
  assert.equal(tick(2.345), "2.3");
  assert.equal(tick(0.01234), "0.012");
  assert.equal(tick(0), "0");
});
