// Display formatting only; no state.

export const num = (v, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(digits);

export const int = (v) => (v === null || v === undefined ? "—" : Math.round(v).toLocaleString());

export const pct = (fraction) => `${Math.round((fraction ?? 0) * 100)}%`;

export const time = (epochSeconds) =>
  epochSeconds ? new Date(epochSeconds * 1000).toLocaleTimeString() : "—";

export const duration = (seconds) => {
  if (seconds === null || seconds === undefined) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m ? `${m}m ${s}s` : `${s}s`;
};

export const configText = (config) =>
  Object.entries(config ?? {})
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
