// Final finding, shown when the loop has ended. Markdown is shown as plain text.

import { h, mount } from "../dom.js";
import { configText } from "../format.js";

export function renderFinding(root, state) {
  const f = state.overview?.finding;
  root.hidden = !f;
  if (!f) return;
  mount(
    root,
    h("h2", {}, `Finding — stopped by ${f.stop_rule}`),
    f.winner_config && h("p", {}, "Winner: ", h("code", {}, configText(f.winner_config)),
      ` (runs ${f.winner_run_ids.join(", ")}) · ${f.tried} runs tried`),
    h("pre", { class: "pane", style: "max-height:none; white-space:pre-wrap" }, f.text),
  );
}
