// API client for the Python backend (src/autoexp/dashboard/routes.py).
// Every response is an envelope {ok, data, error}; this module unwraps it and
// throws ApiError on failure. Shapes mirror dashboard/schemas.py.

/**
 * @typedef {{run_id:string, round_index:number, purpose:string, parent_run_id:?string,
 *   status:string, worker_id:?string, device_id:?string, seed:number,
 *   config:Object<string, number|string|boolean>, step:number, total_steps:number,
 *   rolling_return:?number, best_rolling_return:?number, sps:?number,
 *   stop_reason:?string, started_at:?number, ended_at:?number}} RunRow
 * @typedef {{experiment_id:string, goal:{metric:string, window:number, threshold:number,
 *   seeds_required:number}, version:number, round_index:number,
 *   master:"running"|"stopped"|"absent", stop_requested:boolean, runs:RunRow[],
 *   workers:Worker[], budget:?Budget, approvals:Approval[], finding:?Finding}} Overview
 * @typedef {{worker_id:string, pane_id:?string, runtime:string, state:string,
 *   takeover:boolean, run_ids:string[]}} Worker
 * @typedef {{at:number, lines:{name:string, used:number, limit:number, fraction:number}[]}} Budget
 * @typedef {{approval_id:string, kind:string, summary:string, payload:Object,
 *   requested_by:string, at:number, status:string}} Approval
 * @typedef {{stop_rule:string, winner_config:?Object, winner_run_ids:string[],
 *   tried:number, text:string}} Finding
 * @typedef {{seq:number, at:number, kind:string, summary:string, data:Object}} MasterEvent
 * @typedef {{run_id:string, returns:{step:number, rolling_return:?number, episode_return:?number}[],
 *   losses:{step:number, policy_loss:?number, value_loss:?number, entropy:?number, approx_kl:?number}[],
 *   next_step:number}} Curve
 * @typedef {{worker_id:string, pane_id:?string, text:string, read_at:?number}} Pane
 */

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(method, path, body) {
  const init = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw new ApiError(0, "network", `dashboard server unreachable: ${err.message}`);
  }
  let envelope;
  try {
    envelope = await response.json();
  } catch {
    throw new ApiError(response.status, "bad_response", `non-JSON response from ${path}`);
  }
  if (!response.ok || !envelope.ok) {
    const error = envelope.error ?? { code: "unknown", message: response.statusText };
    throw new ApiError(response.status, error.code, error.message);
  }
  return envelope.data;
}

const q = (params) => {
  const search = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => [k, String(v)]),
  );
  return search.size ? `?${search}` : "";
};

/** @returns {Promise<Overview>} */
export const getOverview = () => request("GET", "/api/overview");
export const getRun = (runId) => request("GET", `/api/runs/${encodeURIComponent(runId)}`);
/** @returns {Promise<Curve>} */
export const getCurve = (runId, sinceStep, maxPoints = 500) =>
  request("GET", `/api/runs/${encodeURIComponent(runId)}/curve${q({ since_step: sinceStep, max_points: maxPoints })}`);
/** @returns {Promise<{events:MasterEvent[], next_seq:number}>} */
export const getEvents = (after, kinds) =>
  request("GET", `/api/events${q({ after, kinds: kinds?.join(",") })}`);
/** @returns {Promise<Pane>} */
export const getPane = (workerId, lines = 60) =>
  request("GET", `/api/workers/${encodeURIComponent(workerId)}/pane${q({ lines })}`);

export const answerApproval = (approvalId, decision, note = "") =>
  request("POST", `/api/approvals/${encodeURIComponent(approvalId)}`, { decision, note });
export const setTakeover = (workerId, on) =>
  request("POST", `/api/workers/${encodeURIComponent(workerId)}/takeover`, { on });
export const requestStop = () => request("POST", "/api/stop", {});

export const STREAM_URL = "/api/stream";
