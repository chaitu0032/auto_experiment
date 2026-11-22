// Dashboard entry point: wires the API client, the immutable store, the
// refresh strategy and the components together.

import * as api from "./api.js";
import { renderApprovals } from "./components/approvals.js";
import { renderBudgets } from "./components/budgets.js";
import { renderControls } from "./components/controls.js";
import { curveRunIds, renderCurves } from "./components/curves.js";
import { renderFeed } from "./components/feed.js";
import { renderFinding } from "./components/finding.js";
import { renderRunsTable } from "./components/runsTable.js";
import { renderWorkers } from "./components/workers.js";
import { startRefresh } from "./refresh.js";
import {
  createStore, toggleRun, withConnection, withCurve, withError, withEvents, withOverview, withPane,
} from "./store.js";

const store = createStore();
const $ = (id) => document.getElementById(id);
let lastVersion = null;

async function refreshAll(version) {
  if (version !== -1 && version === lastVersion) return;
  lastVersion = version;
  try {
    const overview = await api.getOverview();
    store.update((s) => withOverview(s, overview));
    const events = await api.getEvents(store.get().nextSeq);
    store.update((s) => withEvents(s, events));
    await Promise.all(curveRunIds(store.get()).map(async (id) => {
      const curve = await api.getCurve(id, store.get().curves[id]?.next_step);
      store.update((s) => withCurve(s, curve));
    }));
    await Promise.all(overview.workers.map(async (w) => {
      const pane = await api.getPane(w.worker_id);
      store.update((s) => withPane(s, pane));
    }));
  } catch (err) {
    console.error("refresh failed", err);
    store.update((s) => withError(s, err.message));
  }
}

async function act(promise) {
  try {
    await promise;
    await refreshAll(-1);
  } catch (err) {
    console.error("action failed", err);
    store.update((s) => withError(s, err.message));
  }
}

const handlers = {
  onToggleRun: (runId) => {
    store.update((s) => toggleRun(s, runId));
    refreshAll(-1);
  },
  onAnswer: (approvalId, decision) => act(api.answerApproval(approvalId, decision)),
  onTakeover: (workerId, on) => act(api.setTakeover(workerId, on)),
  onStop: () => act(api.requestStop()),
};

function render(state) {
  $("experiment").textContent = state.overview?.experiment_id ?? "";
  $("connection").textContent = state.error ? `error: ${state.error}` : state.connection;
  renderControls($("controls"), state, handlers);
  renderFinding($("finding"), state);
  renderBudgets($("budgets"), state);
  renderApprovals($("approvals"), state, handlers);
  renderRunsTable($("runs"), state, handlers);
  renderCurves($("curves"), state);
  renderFeed($("feed"), state);
  renderWorkers($("workers"), state, handlers);
}

store.subscribe(render);
render(store.get());
startRefresh({
  onVersion: refreshAll,
  onMode: (mode) => store.update((s) => withConnection(s, mode)),
});
