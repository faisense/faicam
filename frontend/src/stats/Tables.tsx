import { Pill, type Tone } from "../shared/components";
import { bytes, count, duration, plural } from "../shared/format";
import type { StatsCamera, StatsNow, Watcher } from "../shared/types";
import { Swatch } from "./colours";

type ColourFor = (name: string) => string;

function status(camera: StatsCamera): [string, Tone] {
  switch (camera.state) {
    case "streaming":
      return camera.on_backup ? ["On backup", "warn"] : ["Online", "good"];
    case "paused":
      return ["Paused", "idle"];
    case "connecting":
      return ["Connecting", "idle"];
    case "reconnecting":
      return ["Offline", "bad"];
    default:
      return ["Stopped", "idle"];
  }
}

function CameraName({ name, colourFor }: { name: string; colourFor: ColourFor }) {
  return (
    <td class="name">
      <Swatch colour={colourFor(name)} />
      {name}
    </td>
  );
}

export function HealthTable({ cameras, colourFor }: { cameras: StatsCamera[]; colourFor: ColourFor }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Camera</th>
          <th>Status</th>
          <th class="num">Frames per second</th>
          <th class="num">Size</th>
          <th class="num">Online for</th>
          <th class="num">Reconnects</th>
          <th class="num">Time on backup</th>
          <th class="num">Frames shared</th>
          <th class="num">Over fps limit</th>
          <th class="num">Not decoded</th>
          <th class="num">Time per frame</th>
          <th class="num">CPU</th>
        </tr>
      </thead>
      <tbody>
        {cameras.length === 0 && (
          <tr>
            <td colSpan={12} class="empty">
              No cameras yet. Add one on the cameras page.
            </td>
          </tr>
        )}
        {cameras.map((c) => {
          const [label, tone] = status(c);
          const streaming = c.state === "streaming";
          return (
            <tr key={c.name}>
              <CameraName name={c.name} colourFor={colourFor} />
              <td>
                <Pill tone={tone}>{label}</Pill>
                {!streaming && c.last_frame_age != null && <small>last picture {duration(c.last_frame_age)} ago</small>}
                {!streaming && c.error && <small class="problem">{c.error}</small>}
              </td>
              <td class="num">{streaming ? c.fps.toFixed(1) : "—"}</td>
              <td class="num">{c.width ? `${c.width}×${c.height}` : "—"}</td>
              <td class="num">{streaming ? duration(c.online_seconds) : "—"}</td>
              <td class="num">{count(c.reconnects)}</td>
              <td class="num">{c.backup_seconds ? duration(c.backup_seconds) : "—"}</td>
              <td class="num">{count(c.frames_shared)}</td>
              <td class="num">{count(c.frames_capped)}</td>
              <td class="num">{count(c.frames_unwatched)}</td>
              <td class="num">{c.work_ms ? `${c.work_ms.toFixed(1)} ms` : "—"}</td>
              <td class="num">{Math.round(c.cpu)}%</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const KIND_ORDER: Record<Watcher["kind"], number> = { programs: 0, machine: 1, link: 2, panel: 3 };
const who = (address: string) => (address === "127.0.0.1" || address === "::1" ? "This machine" : address);

export function WatchersTable({ now, colourFor }: { now: StatsNow; colourFor: ColourFor }) {
  const order = new Map(now.cameras.map((c, i) => [c.name, i]));
  const since = (w: Watcher) => ("since" in w ? w.since : 0);
  const watchers = [...now.watchers].sort(
    (a, b) => (order.get(a.camera) ?? 0) - (order.get(b.camera) ?? 0) || KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || since(a) - since(b),
  );
  return (
    <table>
      <thead>
        <tr>
          <th>Camera</th>
          <th>Who</th>
          <th>Watching with</th>
          <th class="num">Watching for</th>
          <th class="num">Received</th>
        </tr>
      </thead>
      <tbody>
        {watchers.length === 0 && (
          <tr>
            <td colSpan={5} class="empty">
              Nobody is watching right now.
            </td>
          </tr>
        )}
        {watchers.map((w, i) => (
          <tr key={i}>
            <CameraName name={w.camera} colourFor={colourFor} />
            {w.kind === "programs" ? (
              <>
                <td>This machine</td>
                <td>
                  {plural(w.count, "program")}
                  <small>through faicam.subscribe</small>
                </td>
                <td class="num">—</td>
                <td class="num">—</td>
              </>
            ) : (
              <>
                <td>{who(w.address)}</td>
                <td>
                  {w.kind === "machine" ? "faicam client" : w.app}
                  <small>{w.kind === "machine" ? "over the network" : w.kind === "panel" ? "live view on the cameras page" : "web link"}</small>
                </td>
                <td class="num">{duration(now.time - w.since)}</td>
                <td class="num">{w.kind === "machine" ? "—" : bytes(w.bytes)}</td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
