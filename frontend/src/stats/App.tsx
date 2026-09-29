import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../shared/api";
import { OfflineBanner, Wordmark } from "../shared/components";
import { bytes, count, duration, plural, rate } from "../shared/format";
import type { Sample, StatsNow, StatsReport } from "../shared/types";
import { colourOf, useDarkMode } from "./colours";
import { LineChart, Sparkline, type Series } from "./LineChart";
import { HealthTable, WatchersTable } from "./Tables";

const POLL_MS = 2000;
const RANGES: [number, string][] = [
  [300, "5 min"],
  [900, "15 min"],
  [3600, "1 hour"],
];
const RANGE_KEY = "faicam.stats.range";

type Key = "fps" | "viewers" | "sent";
const CHARTS: { field: Key; title: string; unit: string; format: (v: number) => string; axis: (v: number) => string; integer?: boolean; steps?: boolean; emptyTop: number }[] = [
  { field: "fps", title: "Frames per second", unit: "per camera", format: (v) => v.toFixed(1), axis: (v) => String(Number(v.toFixed(2))), emptyTop: 4 },
  { field: "viewers", title: "Viewers", unit: "programs, machines and web links", format: String, axis: String, integer: true, steps: true, emptyTop: 4 },
  { field: "sent", title: "Sent over the network", unit: "per camera", format: (v) => rate(v), axis: (v) => rate(v, true), emptyTop: 4000 },
];

export function App() {
  const dark = useDarkMode();
  const [now, setNow] = useState<StatsNow | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [offline, setOffline] = useState(false);
  const [range, setRange] = useState(readRange);
  const since = useRef<{ started: number | null; last: number | null }>({ started: null, last: null });

  useEffect(() => {
    let timer = 0;
    let alive = true;
    const load = async (): Promise<void> => {
      const { last } = since.current;
      const report = await api<StatsReport>(last == null ? "/api/stats" : `/api/stats?since=${last}`);
      const next = "cameras" in report.now ? (report.now as StatsNow) : null;
      if (last != null && next && next.started !== since.current.started) {
        since.current = { started: null, last: null }; // faicam restarted: its history starts over
        setSamples([]);
        return load();
      }
      since.current.started = next?.started ?? null;
      const newest = report.samples[report.samples.length - 1];
      if (newest) since.current.last = newest.t;
      setSamples((old) => {
        const all = last == null ? report.samples : [...old, ...report.samples];
        const keepFrom = (newest?.t ?? 0) - report.history;
        return all.filter((s) => s.t > keepFrom);
      });
      setNow(next);
    };
    const tick = async () => {
      try {
        await load();
        setOffline(false);
      } catch {
        setOffline(true);
      }
      if (alive) timer = window.setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  const choose = (seconds: number) => {
    setRange(seconds);
    try {
      localStorage.setItem(RANGE_KEY, String(seconds));
    } catch {
      // private browsing: the choice just isn't remembered
    }
  };

  const colourFor = (name: string) => colourOf(name, dark).colour;
  if (now) for (const c of now.cameras) colourOf(c.name, dark); // colours follow the cameras' order

  // the samples in view; a new faicam fills the width instead of a sliver
  const t1 = samples[samples.length - 1]?.t ?? 0;
  let t0 = Math.max(t1 - range, samples[0]?.t ?? 0);
  if (t1 - t0 < 30) t0 = t1 - 30;
  const shown = samples.filter((s) => s.t >= t0);
  const times = shown.map((s) => s.t);
  const names = now?.cameras.map((c) => c.name) ?? [];

  return (
    <div class="wrap">
      <div class="top">
        <div class="brand">
          <Wordmark />
          <span class="summary">
            {now
              ? `${plural(now.cameras.length, "camera")} · ${count(now.totals.online)} online` +
                (now.started ? ` · running for ${duration(now.time - now.started)}` : "")
              : "Loading stats…"}
          </span>
        </div>
        <a class="btn ghost" href="/">
          ← Cameras
        </a>
      </div>

      <OfflineBanner show={offline} />

      {now && (
        <>
          <div class="tiles">
            <Tile label="CPU" value={`${Math.round(now.process.cpu)}%`} sub={`100% is one full core${now.process.cores ? ` · ${plural(now.process.cores, "core")} here` : ""}`} spark={shown.map((s) => [s.t, s.cpu])} t0={t0} t1={t1} />
            <Tile label="Memory" value={bytes(now.process.memory)} sub={now.process.memory == null ? "not available on this system" : "used by faicam"} spark={shown.map((s) => [s.t, s.memory])} t0={t0} t1={t1} />
            <Tile label="Sent over the network" value={rate(now.totals.sent_rate)} sub="to other machines and web links" spark={shown.map((s) => [s.t, s.sent])} t0={t0} t1={t1} />
            <Tile
              label="Viewers"
              value={count(now.totals.viewers)}
              sub={now.totals.viewers ? `watching ${plural(now.cameras.filter((c) => c.viewers > 0).length, "camera")}` : "nobody is watching"}
              spark={shown.map((s) => [s.t, s.viewers])}
              t0={t0}
              t1={t1}
            />
          </div>

          <section class="panel" aria-labelledby="health-title">
            <div class="panel-head">
              <h2 id="health-title">Camera health</h2>
            </div>
            <div class="scroll" id="health">
              <HealthTable cameras={now.cameras} colourFor={colourFor} />
            </div>
            <p class="explain">
              <b>Over fps limit</b>: frames dropped to stay under the camera's fps setting. <b>Not decoded</b>: frames skipped while nobody was
              watching, which saves CPU. <b>Time per frame</b>: decoding, resizing and sharing one frame. <b>CPU</b>: the camera's own reader;
              100% is one full core.
            </p>
          </section>

          <section class="panel" aria-labelledby="watchers-title">
            <div class="panel-head">
              <h2 id="watchers-title">Who is watching</h2>
              <p>{now.totals.viewers ? plural(now.totals.viewers, "viewer") : ""}</p>
            </div>
            <div class="scroll">
              <WatchersTable now={now} colourFor={colourFor} />
            </div>
          </section>

          <section class="panel" aria-labelledby="history-title">
            <div class="panel-head">
              <h2 id="history-title">Over time</h2>
              <div class="seg" role="group" aria-label="Time range">
                {RANGES.map(([seconds, label]) => (
                  <button key={seconds} type="button" aria-pressed={seconds === range} onClick={() => choose(seconds)}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div class="charts">
              {CHARTS.map(({ field, ...chart }) => (
                <LineChart
                  key={field}
                  {...chart}
                  series={names.map((name): Series => ({ name, ...colourOf(name, dark), values: shown.map((s) => s.cameras[name]?.[field] ?? null) }))}
                  times={times}
                  t0={t0}
                  t1={t1}
                  gap={(3 * POLL_MS) / 1000}
                />
              ))}
            </div>
          </section>

          <footer class="page">
            Process {now.process.pid} · {plural(now.process.threads, "thread")} · updated every {POLL_MS / 1000} s · the last hour is kept in memory
          </footer>
        </>
      )}
    </div>
  );
}

interface TileProps {
  label: string;
  value: string;
  sub: string;
  spark: [number, number | null][];
  t0: number;
  t1: number;
}

function Tile({ label, value, sub, spark, t0, t1 }: TileProps) {
  return (
    <div class="tile">
      <span class="label">{label}</span>
      <span class="value">{value}</span>
      <span class="sub">{sub}</span>
      <Sparkline values={spark} t0={t0} t1={t1} />
    </div>
  );
}

function readRange(): number {
  try {
    const saved = Number(localStorage.getItem(RANGE_KEY));
    if (RANGES.some(([seconds]) => seconds === saved)) return saved;
  } catch {
    // private browsing
  }
  return 900;
}
