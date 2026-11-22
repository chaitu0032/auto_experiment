// Immutable UI store: every update produces a new state object; components
// re-render from the state they are given and never mutate it.

/**
 * @typedef {{overview: ?import("./api.js").Overview,
 *   events: import("./api.js").MasterEvent[], nextSeq: number,
 *   curves: Object<string, import("./api.js").Curve>,
 *   panes: Object<string, import("./api.js").Pane>,
 *   selectedRunIds: string[], connection: "connecting"|"live"|"polling"|"offline",
 *   error: ?string}} UiState
 */

/** @type {UiState} */
export const initialState = Object.freeze({
  overview: null,
  events: [],
  nextSeq: 0,
  curves: {},
  panes: {},
  selectedRunIds: [],
  connection: "connecting",
  error: null,
});

export function createStore(state = initialState) {
  let current = state;
  const listeners = new Set();
  return {
    get: () => current,
    /** @param {(s: UiState) => UiState} updater must return a new object */
    update(updater) {
      const next = updater(current);
      if (next === current) return;
      current = Object.freeze(next);
      for (const listener of listeners) listener(current);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

// Pure reducers -----------------------------------------------------------

export const withOverview = (s, overview) => ({ ...s, overview, error: null });

export const withEvents = (s, { events, next_seq }) => ({
  ...s,
  events: [...s.events, ...events].slice(-500),
  nextSeq: next_seq,
});

export const withCurve = (s, curve) => {
  const prev = s.curves[curve.run_id];
  const merged = prev
    ? { ...curve, returns: [...prev.returns, ...curve.returns], losses: [...prev.losses, ...curve.losses] }
    : curve;
  return { ...s, curves: { ...s.curves, [curve.run_id]: merged } };
};

export const withPane = (s, pane) => ({ ...s, panes: { ...s.panes, [pane.worker_id]: pane } });

export const toggleRun = (s, runId) => ({
  ...s,
  selectedRunIds: s.selectedRunIds.includes(runId)
    ? s.selectedRunIds.filter((id) => id !== runId)
    : [...s.selectedRunIds, runId],
});

export const withConnection = (s, connection) => ({ ...s, connection });
export const withError = (s, error) => ({ ...s, error });
