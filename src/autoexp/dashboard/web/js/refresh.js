// Refresh strategy: server-sent events from /api/stream announce a new
// overview `version`; on each change we refetch what is on screen. If the
// stream fails, fall back to polling the overview every POLL_MS and retry the
// stream later. `?refresh=poll` in the page URL forces polling (useful behind
// proxies that buffer event streams, and for headless screenshots).

import { STREAM_URL } from "./api.js";

const POLL_MS = 3000;
const STREAM_RETRY_MS = 15000;

/**
 * @param {{onVersion: (version:number) => void, onMode: (mode:string) => void,
 *   pollOnly?: boolean}} handlers
 * @returns {() => void} stop function
 */
export function startRefresh({ onVersion, onMode, pollOnly = false }) {
  let source = null;
  let pollTimer = null;
  let retryTimer = null;
  let stopped = false;

  const poll = () => {
    onMode("polling");
    clearInterval(pollTimer);
    pollTimer = setInterval(() => onVersion(-1), POLL_MS); // -1: unknown, always refetch
    if (!pollOnly) retryTimer = setTimeout(openStream, STREAM_RETRY_MS);
  };

  function openStream() {
    if (stopped || pollOnly || typeof EventSource === "undefined") return poll();
    source = new EventSource(STREAM_URL);
    source.addEventListener("open", () => {
      clearInterval(pollTimer);
      onMode("live");
    });
    source.addEventListener("version", (event) => {
      try {
        onVersion(JSON.parse(event.data).version);
      } catch {
        onVersion(-1);
      }
    });
    source.addEventListener("error", () => {
      source.close();
      if (!stopped) poll();
    });
  }

  openStream();
  onVersion(-1); // initial load
  return () => {
    stopped = true;
    source?.close();
    clearInterval(pollTimer);
    clearTimeout(retryTimer);
  };
}
