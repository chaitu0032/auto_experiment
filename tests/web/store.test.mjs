// Pure-reducer tests for the dashboard store. Run: node --test tests/web/
import assert from "node:assert/strict";
import { test } from "node:test";

import { createStore, initialState, toggleRun, withCurve, withEvents } from "../../src/autoexp/dashboard/web/js/store.js";

test("reducers return new objects and leave the input unchanged", () => {
  const next = toggleRun(initialState, "r0001");
  assert.deepEqual(next.selectedRunIds, ["r0001"]);
  assert.deepEqual(initialState.selectedRunIds, []);
  assert.deepEqual(toggleRun(next, "r0001").selectedRunIds, []);
});

test("withCurve appends incremental points per run", () => {
  const a = withCurve(initialState, { run_id: "r0001", returns: [{ step: 1, rolling_return: 10 }], losses: [], next_step: 1 });
  const b = withCurve(a, { run_id: "r0001", returns: [{ step: 2, rolling_return: 20 }], losses: [], next_step: 2 });
  assert.equal(b.curves.r0001.returns.length, 2);
  assert.equal(b.curves.r0001.next_step, 2);
  assert.equal(a.curves.r0001.returns.length, 1);
});

test("withEvents advances the cursor", () => {
  const s = withEvents(initialState, { events: [{ seq: 1, kind: "prune" }], next_seq: 1 });
  assert.equal(s.nextSeq, 1);
  assert.equal(s.events.length, 1);
});

test("store notifies subscribers with frozen state", () => {
  const store = createStore();
  let seen = null;
  store.subscribe((s) => { seen = s; });
  store.update((s) => toggleRun(s, "r0002"));
  assert.ok(Object.isFrozen(seen));
  assert.deepEqual(seen.selectedRunIds, ["r0002"]);
});
