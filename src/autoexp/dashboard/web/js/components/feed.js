// Prune and decision feed with the master's reasoning, newest first.

import { h, mount } from "../dom.js";
import { time } from "../format.js";

const SHOWN = new Set(["prune", "decision", "approval_requested", "round_started", "stopped", "recovered", "steer_held"]);

/** Extra evidence beyond the one-line summary (which already has the reason). */
export function detail(event) {
  const d = event.data ?? {};
  if (event.kind === "prune") {
    const e = d.evidence ?? {};
    return Object.entries(e).map(([k, v]) => `${k}=${typeof v === "number" ? +v.toFixed(3) : v}`).join(" ");
  }
  if (event.kind === "decision") {
    const proposals = (d.proposals ?? []).map((p) => `${p.purpose} seed ${p.seed}`);
    return proposals.length ? `proposals: ${proposals.join(", ")}` : "";
  }
  return "";
}

export function renderFeed(root, state) {
  const items = state.events.filter((e) => SHOWN.has(e.kind)).slice().reverse();
  mount(
    root,
    h("h2", {}, "Decisions & prunes"),
    items.length
      ? h("ul", { class: "feed" }, items.map((e) =>
          h("li", {},
            h("div", {}, h("span", { class: "muted" }, `${time(e.at)} · ${e.kind} `), e.summary),
            detail(e) && h("div", { class: "muted" }, detail(e)),
          )))
      : h("p", { class: "muted" }, "Nothing decided yet."),
  );
}
