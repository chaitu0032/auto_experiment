// Budget lines with progress bars.

import { h, mount } from "../dom.js";
import { num, pct } from "../format.js";

export function renderBudgets(root, state) {
  const lines = state.overview?.budget?.lines ?? [];
  mount(
    root,
    h("h2", {}, "Budgets"),
    lines.length
      ? lines.map((line) =>
          h("div", { class: "budget-line" },
            h("div", {}, line.name, " ", h("span", { class: "muted" }, `${num(line.used, 1)} / ${num(line.limit, 1)} (${pct(line.fraction)})`)),
            h("div", { class: line.fraction >= 1 ? "bar over" : "bar" },
              h("span", { style: { width: `${Math.min(100, Math.round(line.fraction * 100))}%` } })),
          ))
      : h("p", { class: "muted" }, "No budget data yet."),
  );
}
