// Pending approvals (training-code changes, budget extensions) with approve/reject.

import { h, mount } from "../dom.js";
import { time } from "../format.js";

export function renderApprovals(root, state, { onAnswer }) {
  const pending = (state.overview?.approvals ?? []).filter((a) => a.status === "pending");
  mount(
    root,
    h("h2", {}, `Pending approvals (${pending.length})`),
    pending.length
      ? pending.map((a) =>
          h("div", { class: "approval" },
            h("div", {}, h("strong", {}, a.kind), " ", a.summary),
            h("div", { class: "muted" }, `${a.approval_id} · ${a.requested_by} · ${time(a.at)}`),
            a.payload?.diff && h("pre", { class: "pane" }, String(a.payload.diff)),
            h("button", { onclick: () => onAnswer(a.approval_id, "approve") }, "Approve"), " ",
            h("button", { class: "danger", onclick: () => onAnswer(a.approval_id, "reject") }, "Reject"),
          ))
      : h("p", { class: "muted" }, "Nothing waiting for you."),
  );
}
