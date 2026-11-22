// Dependency-free SVG line chart: several series, linear axes, optional
// horizontal reference line (the goal threshold). Enough for return and loss
// curves without vendoring a third-party charting library.

import { s } from "../dom.js";

const PALETTE = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#39c5cf", "#ff7b72", "#a5d6ff"];

/**
 * @param {{series: {label:string, points:[number, number][]}[], width?:number,
 *   height?:number, yLabel?:string, reference?:{y:number, label:string}}} spec
 * @returns {SVGSVGElement}
 */
export function lineChart({ series, width = 720, height = 260, yLabel = "", reference }) {
  const pad = { left: 48, right: 12, top: 12, bottom: 28 };
  const all = series.flatMap((ser) => ser.points).filter(([, y]) => Number.isFinite(y));
  const svg = s("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": yLabel });
  if (all.length === 0) {
    svg.append(s("text", { x: width / 2, y: height / 2, "text-anchor": "middle" }, "no data yet"));
    return svg;
  }
  const xs = all.map(([x]) => x);
  const ys = all.map(([, y]) => y).concat(reference ? [reference.y] : []);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs) || 1];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const sx = (x) => pad.left + ((x - x0) / (x1 - x0 || 1)) * (width - pad.left - pad.right);
  const sy = (y) => height - pad.bottom - ((y - y0) / (y1 - y0 || 1)) * (height - pad.top - pad.bottom);

  svg.append(
    s("line", { class: "axis", x1: pad.left, y1: height - pad.bottom, x2: width - pad.right, y2: height - pad.bottom }),
    s("line", { class: "axis", x1: pad.left, y1: pad.top, x2: pad.left, y2: height - pad.bottom }),
    s("text", { x: 4, y: pad.top + 4 }, y1.toFixed(0)),
    s("text", { x: 4, y: height - pad.bottom }, y0.toFixed(0)),
    s("text", { x: width - pad.right, y: height - 8, "text-anchor": "end" }, `step ${Math.round(x1).toLocaleString()}`),
  );
  if (reference) {
    svg.append(
      s("line", { x1: pad.left, x2: width - pad.right, y1: sy(reference.y), y2: sy(reference.y),
        stroke: "#8b93a7", "stroke-dasharray": "4 4" }),
      s("text", { x: width - pad.right, y: sy(reference.y) - 4, "text-anchor": "end" }, reference.label),
    );
  }
  series.forEach((ser, i) => {
    const pts = ser.points.filter(([, y]) => Number.isFinite(y));
    if (pts.length === 0) return;
    const d = pts.map(([x, y], j) => `${j ? "L" : "M"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join("");
    const color = PALETTE[i % PALETTE.length];
    svg.append(
      s("path", { d, fill: "none", stroke: color, "stroke-width": 1.6 }),
      s("text", { x: pad.left + 6 + i * 70, y: pad.top + 10, fill: color }, ser.label),
    );
  });
  return svg;
}
