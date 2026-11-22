// Return and loss curves for the selected runs (all live runs if none selected).

import { lineChart } from "../charts/lineChart.js";
import { h, mount } from "../dom.js";

export function curveRunIds(state) {
  const runs = state.overview?.runs ?? [];
  return state.selectedRunIds.length
    ? state.selectedRunIds
    : runs.filter((r) => r.status === "running").map((r) => r.run_id);
}

export function renderCurves(root, state) {
  const ids = curveRunIds(state);
  const goal = state.overview?.goal;
  const reference = goal ? { y: goal.threshold, label: `goal ${goal.threshold}` } : undefined;
  const curves = ids.map((id) => state.curves[id]).filter(Boolean);
  const returns = curves.map((c) => ({ label: c.run_id, points: c.returns.map((p) => [p.step, p.rolling_return]) }));
  const kl = curves.map((c) => ({ label: c.run_id, points: c.losses.map((p) => [p.step, p.approx_kl]) }));
  const valueLoss = curves.map((c) => ({ label: c.run_id, points: c.losses.map((p) => [p.step, p.value_loss]) }));
  mount(
    root,
    h("h2", {}, "Curves ", h("span", { class: "muted" }, ids.length ? ids.join(", ") : "select runs")),
    lineChart({ series: returns, yLabel: "rolling return", reference }),
    h("div", { class: "grid", style: "padding:0" },
      h("div", { class: "span-2" }, lineChart({ series: valueLoss, yLabel: "value loss", height: 160 })),
      h("div", {}, lineChart({ series: kl, yLabel: "approx KL", height: 160, width: 360 })),
    ),
  );
}
