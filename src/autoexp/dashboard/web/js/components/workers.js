// Worker panes: state, assigned runs, cached pane tail, takeover toggle.

import { h, mount } from "../dom.js";
import { time } from "../format.js";

export function renderWorkers(root, state, { onTakeover }) {
  const workers = state.overview?.workers ?? [];
  mount(
    root,
    h("h2", {}, "Workers"),
    h("div", { class: "grid", style: "padding:0" },
      workers.map((w) => {
        const pane = state.panes[w.worker_id];
        return h("div", { class: "panel" },
          h("div", {},
            h("strong", {}, w.worker_id), " ",
            h("span", { class: "pill" }, w.takeover ? "human takeover" : w.state), " ",
            h("span", { class: "muted" }, `${w.runtime} · pane ${w.pane_id ?? "—"} · runs ${w.run_ids.join(", ") || "—"}`),
          ),
          h("pre", { class: "pane" }, pane?.text ?? "(no pane output yet)"),
          h("div", { class: "muted" }, `read ${time(pane?.read_at)}`),
          h("button", { onclick: () => onTakeover(w.worker_id, !w.takeover) },
            w.takeover ? "Release to master" : "Take over"),
        );
      }),
    ),
  );
}
