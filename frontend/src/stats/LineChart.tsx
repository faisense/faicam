import { useState } from "preact/hooks";
import { clock } from "../shared/format";
import { Swatch, useWidth } from "./colours";

export interface Series {
  name: string;
  colour: string;
  dashed: boolean;
  values: (number | null)[]; // one per sample time; null where the camera didn't exist
}

interface Props {
  title: string;
  unit: string;
  series: Series[];
  times: number[]; // sample times in seconds, oldest first
  t0: number;
  t1: number;
  gap: number; // samples further apart than this aren't joined
  format: (value: number) => string;
  axis: (value: number) => string;
  integer?: boolean;
  steps?: boolean; // counts change in steps, not gradually
  emptyTop: number; // the y range when every value is zero
}

const HEIGHT = 210;
const TOP = 8;
const BOTTOM = 22;

/** A line per camera over time, with a readout that follows the pointer and a table view. */
export function LineChart(props: Props) {
  const { title, unit, series, times, t0, t1, gap, format, axis, integer = false, steps = false } = props;
  const [plotRef, measured] = useWidth<HTMLDivElement>();
  const [hoverX, setHoverX] = useState<number | null>(null);
  const width = measured || 600;

  const track = (event: PointerEvent) =>
    setHoverX(event.clientX - (event.currentTarget as HTMLElement).getBoundingClientRect().left);

  return (
    <div class="chart">
      <h3>
        {title}
        <span>{unit}</span>
      </h3>
      {series.length > 1 && (
        <ul class="legend">
          {series.map((s) => (
            <li key={s.name}>
              <Swatch colour={s.colour} />
              {s.name}
            </li>
          ))}
        </ul>
      )}
      <div class="plot" ref={plotRef} onPointerMove={track} onPointerDown={track} onPointerLeave={() => setHoverX(null)}>
        {series.length && times.length ? (
          plot(width, hoverX)
        ) : (
          <svg viewBox={`0 0 ${width} ${HEIGHT}`} role="img" aria-label={`${title}: no data yet`}>
            <text class="nodata" x={width / 2} y={HEIGHT / 2} text-anchor="middle">
              {series.length ? "Collecting data…" : "No cameras yet."}
            </text>
          </svg>
        )}
      </div>
      <details>
        <summary>Show as table</summary>
        <div class="scroll">
          <SummaryTable series={series} format={format} integer={integer} />
        </div>
      </details>
    </div>
  );

  function plot(w: number, hx: number | null) {
    // y axis from zero to a round number
    let max = 0;
    for (const s of series) for (const v of s.values) if (v != null && v > max) max = v;
    let step = niceStep((max > 0 ? max : props.emptyTop) / 4);
    if (integer) step = Math.max(1, Math.ceil(step));
    const top = Math.max(step, Math.ceil(max / step - 1e-9) * step);
    const ticks: number[] = [];
    for (let v = 0; v <= top + step / 1e6; v += step) ticks.push(Number(v.toFixed(6)));
    const left = Math.max(...ticks.map((v) => axis(v).length)) * 6.5 + 10;

    // name each line at its end when there are few enough and they don't overlap
    const ends = series.flatMap((s) => {
      let i = s.values.length - 1;
      while (i >= 0 && s.values[i] == null) i--;
      return i >= 0 && i >= s.values.length - 3 ? [{ s, v: s.values[i]!, t: times[i]! }] : []; // still running
    });
    let labels = series.length >= 2 && series.length <= 4 && w >= 560;
    const right = labels ? Math.min(140, Math.max(4, ...ends.map((e) => e.s.name.length)) * 7 + 18) : 10;
    const x = (t: number) => left + ((t - t0) / Math.max(t1 - t0, 1)) * (w - left - right);
    const y = (v: number) => TOP + (1 - v / top) * (HEIGHT - TOP - BOTTOM);
    if (labels) {
      const ys = ends.map((e) => y(e.v)).sort((a, b) => a - b);
      if (ys.some((v, i) => i > 0 && v - ys[i - 1]! < 14)) labels = false;
    }

    const span = t1 - t0;
    const tStep = [10, 15, 30, 60, 120, 300, 600, 900, 1800].find((s) => span / s <= Math.max(2, Math.floor((w - left - right) / 90))) ?? 3600;
    const timeTicks: number[] = [];
    for (let t = Math.ceil(t0 / tStep) * tStep; t <= t1; t += tStep) timeTicks.push(t);

    // the sample nearest the pointer
    let hover: number | null = null;
    if (hx != null && hx >= left - 10 && hx <= w - right + 10) {
      hover = 0;
      for (let i = 1; i < times.length; i++) {
        if (Math.abs(x(times[i]!) - hx) < Math.abs(x(times[hover]!) - hx)) hover = i;
      }
    }
    const hoverRows =
      hover == null
        ? []
        : series
            .map((s) => ({ s, v: s.values[hover!] }))
            .filter((r): r is { s: Series; v: number } => r.v != null)
            .sort((a, b) => b.v - a.v);
    const hx0 = hover == null ? 0 : x(times[hover]!);

    return (
      <>
        <svg viewBox={`0 0 ${w} ${HEIGHT}`} role="img" aria-label={`${title} over time for ${series.map((s) => s.name).join(", ")}. The table below has the numbers.`}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={left} x2={w - right} y1={y(v)} y2={y(v)} stroke="var(--grid)" stroke-width={1} />
              <text x={left - 8} y={y(v) + 4} text-anchor="end">
                {axis(v)}
              </text>
            </g>
          ))}
          {timeTicks.map((t) => (
            <text key={t} x={x(t)} y={HEIGHT - 4} text-anchor="middle">
              {clock(t, tStep < 60)}
            </text>
          ))}
          {series.map((s) => (
            <path
              key={s.name}
              d={pathOf(s.values, times, x, y, gap, steps)}
              fill="none"
              stroke={s.colour}
              stroke-width={2}
              stroke-linejoin="round"
              stroke-linecap="round"
              stroke-dasharray={s.dashed ? "6 4" : undefined}
            />
          ))}
          {ends.map((e) => (
            <circle key={e.s.name} cx={x(e.t)} cy={y(e.v)} r={3} fill={e.s.colour} />
          ))}
          {labels &&
            ends.map((e) => (
              <text key={e.s.name} class="end" x={x(e.t) + 8} y={y(e.v) + 4}>
                {e.s.name}
              </text>
            ))}
          {hover != null && (
            <g>
              <line x1={hx0} x2={hx0} y1={TOP} y2={HEIGHT - BOTTOM} stroke="var(--muted)" stroke-width={1} />
              {hoverRows.map((r) => (
                <circle key={r.s.name} cx={hx0} cy={y(r.v)} r={4} fill={r.s.colour} stroke="var(--surface)" stroke-width={2} />
              ))}
            </g>
          )}
        </svg>
        {hover != null && (
          <div class="tip" style={{ left: `${hx0 + 14}px`, transform: hx0 > w * 0.6 ? "translateX(calc(-100% - 28px))" : undefined }}>
            <b>{clock(times[hover]!, true)}</b>
            {hoverRows.length ? (
              hoverRows.map((r) => (
                <div key={r.s.name}>
                  <Swatch colour={r.s.colour} />
                  <span>{r.s.name}</span>
                  <span>{format(r.v)}</span>
                </div>
              ))
            ) : (
              <div>No data</div>
            )}
          </div>
        )}
      </>
    );
  }
}

function SummaryTable({ series, format, integer }: { series: Series[]; format: (v: number) => string; integer: boolean }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Camera</th>
          <th class="num">Now</th>
          <th class="num">Average</th>
          <th class="num">Lowest</th>
          <th class="num">Highest</th>
        </tr>
      </thead>
      <tbody>
        {series.map((s) => {
          const values = s.values.filter((v): v is number => v != null);
          const name = (
            <td class="name">
              <Swatch colour={s.colour} />
              {s.name}
            </td>
          );
          if (!values.length) {
            return (
              <tr key={s.name}>
                {name}
                <td colSpan={4} class="muted">
                  No data
                </td>
              </tr>
            );
          }
          const average = values.reduce((a, b) => a + b, 0) / values.length;
          const cells = [values[values.length - 1]!, integer ? Number(average.toFixed(1)) : average, Math.min(...values), Math.max(...values)];
          return (
            <tr key={s.name}>
              {name}
              {cells.map((v, i) => (
                <td key={i} class="num">
                  {format(v)}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const f = raw / power;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * power;
}

function pathOf(
  values: (number | null)[],
  times: number[],
  x: (t: number) => number,
  y: (v: number) => number,
  gap: number,
  steps: boolean,
): string {
  let d = "";
  let last: [number, number] | null = null;
  values.forEach((v, i) => {
    const t = times[i]!;
    if (v == null || (i > 0 && t - times[i - 1]! > gap)) last = null; // break the line
    if (v == null) return;
    const point: [number, number] = [x(t), y(v)];
    if (last && steps) d += `L${point[0].toFixed(1)},${last[1].toFixed(1)}`;
    d += `${last ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}`;
    last = point;
  });
  return d;
}

/** A small line of recent values, for the tiles. */
export function Sparkline({ values, t0, t1 }: { values: [number, number | null][]; t0: number; t1: number }) {
  const W = 200;
  const H = 34;
  let max = 0;
  for (const [, v] of values) if (v != null && v > max) max = v;
  const x = (t: number) => ((t - t0) / Math.max(t1 - t0, 1)) * W;
  const y = (v: number) => H - 1 - (max > 0 ? v / max : 0) * (H - 4);
  const d = pathOf(
    values.map(([, v]) => v),
    values.map(([t]) => t),
    x,
    y,
    Infinity,
    false,
  );
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <line x1={0} x2={W} y1={H - 0.5} y2={H - 0.5} stroke="var(--grid)" stroke-width={1} vector-effect="non-scaling-stroke" />
      {d && <path d={d} fill="none" stroke="var(--muted)" stroke-width={1.5} stroke-linejoin="round" vector-effect="non-scaling-stroke" />}
    </svg>
  );
}
