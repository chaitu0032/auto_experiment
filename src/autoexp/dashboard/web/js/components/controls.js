// Top-bar controls: master state and the stop button.

import { h, mount } from "../dom.js";

export function renderControls(root, state, { onStop }) {
  const o = state.overview;
  const stopping = o?.stop_requested;
  mount(
    root,
    h("span", { class: "muted" }, o ? `master ${o.master} · round ${o.round_index} ` : ""),
    h("button", { class: "danger", disabled: !o || stopping || o.master !== "running", onclick: onStop },
      stopping ? "Stop requested" : "Stop experiment"),
  );
}
