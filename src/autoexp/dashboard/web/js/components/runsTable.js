// Runs table, one row per run ID. Clicking a row selects it for the curves panel.

import { h, mount } from "../dom.js";
import { configText, int, num } from "../format.js";

const COLUMNS = ["run", "round", "purpose", "status", "worker", "device", "seed", "step", "return(100)", "best", "sps", "config", "reason"];

export function renderRunsTable(root, state, { onToggleRun }) {
  const runs = state.overview?.runs ?? [];
  const rows = runs.map((run) =>
    h(
      "tr",
      { class: state.selectedRunIds.includes(run.run_id) ? "selected" : "", onclick: () => onToggleRun(run.run_id) },
      h("td", {}, run.run_id),
      h("td", {}, run.round_index),
      h("td", {}, run.purpose, run.parent_run_id ? ` ← ${run.parent_run_id}` : ""),
      h("td", { class: `status-${run.status}` }, run.status),
      h("td", {}, run.worker_id ?? "—"),
      h("td", {}, run.device_id ?? "—"),
      h("td", {}, run.seed),
      h("td", {}, `${int(run.step)} / ${int(run.total_steps)}`),
      h("td", {}, num(run.rolling_return)),
      h("td", {}, num(run.best_rolling_return)),
      h("td", {}, int(run.sps)),
      h("td", { class: "muted" }, configText(run.config)),
      h("td", { class: "muted" }, run.stop_reason ?? ""),
    ),
  );
  mount(
    root,
    h("h2", {}, `Runs (${runs.length})`),
    runs.length
      ? h("table", {}, h("thead", {}, h("tr", {}, COLUMNS.map((c) => h("th", {}, c)))), h("tbody", {}, rows))
      : h("p", { class: "muted" }, "No runs allocated yet."),
  );
}
