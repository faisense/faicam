import { useState } from "preact/hooks";
import { messageOf, post } from "../shared/api";
import { Button } from "../shared/components";
import type { Backup, Camera, DetectResult, SaveResult, SourceKind } from "../shared/types";
import { Drawer, ErrorNote, useFocus } from "./Drawer";

interface Props {
  camera: Camera | null; // null when adding
  cameras: Camera[];
  brands: { key: string; label: string }[];
  onClose: () => void;
  onSaved: (result: SaveResult, message: string) => void;
  onOpenYaml: () => void;
}

interface Fields {
  name: string;
  kind: SourceKind;
  ip: string;
  brand: string;
  path: string;
  username: string;
  password: string;
  url: string;
  urlUsername: string;
  urlPassword: string;
  source: string;
  fps: string;
  size: string;
  transport: "auto" | "tcp" | "udp";
  port: string;
  streamPort: string;
  openTimeout: string;
  readTimeout: string;
}

interface BackupRow {
  id: number;
  choice: string; // "camera:<name>" or "address"
  value: string;
}

type TestResult = { ok: true; found: DetectResult } | { ok: false; message: string };

const DEFAULT_PORT = 554;
const DEFAULT_TIMEOUT = 5;
const KINDS: [SourceKind, string][] = [
  ["camera", "IP camera"],
  ["rtsp", "RTSP address"],
  ["source", "File or other"],
];
const SEARCHING: Record<SourceKind, string> = {
  camera: "Looking for the camera…",
  rtsp: "Connecting…",
  source: "Opening…",
};

let nextRow = 0;

export function CameraDrawer({ camera, cameras, brands, onClose, onSaved, onOpenYaml }: Props) {
  const [fields, setFields] = useState<Fields>(() => initialFields(camera));
  const [rows, setRows] = useState<BackupRow[]>(() => initialRows(camera));
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [moreOpen] = useState(() => hasMoreSettings(fields));
  const nameRef = useFocus<HTMLInputElement>();

  const s = camera?.settings;
  const set = <K extends keyof Fields>(key: K, value: Fields[K]) => setFields((f) => ({ ...f, [key]: value }));
  const others = cameras.filter((c) => c.name !== camera?.name).map((c) => c.name);
  const { kind } = fields;

  const payload = () => {
    const rtsp = kind === "rtsp";
    return {
      name: fields.name.trim(),
      original: camera?.name ?? null,
      stream_port: fields.streamPort.trim(),
      kind,
      ip: fields.ip.trim(),
      port: fields.port || DEFAULT_PORT,
      brand: fields.brand,
      path: fields.path.trim(),
      username: (rtsp ? fields.urlUsername : fields.username).trim(),
      password: rtsp ? fields.urlPassword : fields.password,
      source: (rtsp ? fields.url : fields.source).trim(),
      backups: readRows(rows),
      fps: fields.fps,
      size: fields.size.trim(),
      rtsp_transport: fields.transport,
      open_timeout: fields.openTimeout,
      read_timeout: fields.readTimeout,
    };
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    setError(null);
    try {
      const found = await post<DetectResult>("/api/detect", payload());
      setTest({ ok: true, found });
      if (kind === "camera") {
        if (found.brand) set("brand", found.brand);
        else setFields((f) => ({ ...f, brand: "other", path: found.path ?? "" }));
      }
    } catch (err) {
      setTest({ ok: false, message: messageOf(err) });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    const data = payload();
    if (!data.name) {
      setError("Give the camera a name.");
      nameRef.current?.focus();
      return;
    }
    if (!data.stream_port) {
      setError("Give the camera a stream port, like 7301. Each camera needs its own.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      onSaved(await post<SaveResult>("/api/cameras", data), `${camera ? "Saved" : "Added"} ${data.name}`);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setSaving(false);
    }
  };

  const keepPassword = (has: boolean | undefined, fallback = "") => (has ? "Leave empty to keep it" : fallback);

  return (
    <Drawer
      id="camera"
      title={camera ? `Edit ${camera.name}` : "Add camera"}
      onClose={onClose}
      onSubmit={save}
      footer={
        <>
          <Button class="btn primary" type="submit" busy={saving ? (kind === "camera" && fields.brand === "auto" ? "Finding the camera…" : "Saving…") : null}>
            Save
          </Button>
          <button class="btn ghost" type="button" onClick={onClose}>
            Cancel
          </button>
        </>
      }
    >
      <div class="fields">
        <label>
          Name
          <input ref={nameRef} value={fields.name} onInput={(e) => set("name", e.currentTarget.value)} placeholder="front-door" maxLength={32} />
          <small>Programs use this name to watch the camera. Lowercase letters, digits, - and _, starting with a letter.</small>
        </label>
        <label>
          Stream port
          <input type="number" min={1} max={65435} value={fields.streamPort} onInput={(e) => set("streamPort", e.currentTarget.value)} placeholder="7301" />
          <small>Other machines watch the camera on this port, programs on this computer on this port + 100. Each camera needs its own.</small>
        </label>

        <div class="seg" role="group" aria-label="Type">
          {KINDS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={kind === value}
              onClick={() => {
                set("kind", value);
                setTest(null);
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {kind === "camera" && (
          <div class="group">
            <label>
              IP address
              <input value={fields.ip} onInput={(e) => set("ip", e.currentTarget.value)} placeholder="192.168.1.21" inputMode="decimal" />
            </label>
            <label>
              Brand
              <select value={fields.brand} onChange={(e) => set("brand", e.currentTarget.value)}>
                <option value="auto">Find automatically</option>
                {brands.map((b) => (
                  <option key={b.key} value={b.key}>
                    {b.label}
                  </option>
                ))}
                <option value="other">Other (enter the stream path)</option>
              </select>
            </label>
            {fields.brand === "other" && (
              <label>
                Stream path
                <input value={fields.path} onInput={(e) => set("path", e.currentTarget.value)} placeholder="/stream1" />
                <small>From the camera's manual, the part after the IP address.</small>
              </label>
            )}
            <div class="two">
              <label>
                Username
                <input value={fields.username} onInput={(e) => set("username", e.currentTarget.value)} placeholder="admin" />
              </label>
              <label>
                Password
                <input
                  type="password"
                  autoComplete="new-password"
                  value={fields.password}
                  onInput={(e) => set("password", e.currentTarget.value)}
                  placeholder={keepPassword(s?.kind === "camera" && s.has_password)}
                />
              </label>
            </div>
          </div>
        )}

        {kind === "rtsp" && (
          <div class="group">
            <label>
              Address
              <input value={fields.url} onInput={(e) => set("url", e.currentTarget.value)} placeholder="rtsp://192.168.1.21:554/live" inputMode="url" />
              <small>HLS (.m3u8), RTMP and HTTP stream addresses work too.</small>
            </label>
            <div class="two">
              <label>
                Username
                <input value={fields.urlUsername} onInput={(e) => set("urlUsername", e.currentTarget.value)} placeholder="optional" />
              </label>
              <label>
                Password
                <input
                  type="password"
                  autoComplete="new-password"
                  value={fields.urlPassword}
                  onInput={(e) => set("urlPassword", e.currentTarget.value)}
                  placeholder={keepPassword(s?.kind === "rtsp" && s.has_password, "optional")}
                />
              </label>
            </div>
          </div>
        )}

        {kind === "source" && (
          <div class="group">
            <label>
              Source
              <input value={fields.source} onInput={(e) => set("source", e.currentTarget.value)} placeholder="~/Movies/lobby.mp4, 0 or synthetic://640x480@15" />
              <small>A video file, a USB camera number, a GStreamer pipeline, or a test pattern.</small>
            </label>
          </div>
        )}

        <div>
          <Button class="btn secondary" busy={testing ? SEARCHING[kind] : null} onClick={runTest}>
            Test connection
          </Button>
        </div>
        {test && <TestOutcome test={test} kind={kind} />}

        <div class="field">
          <span class="label">Backups</span>
          <div class="group">
            {rows.map((row) => (
              <BackupEditor
                key={row.id}
                row={row}
                cameras={others}
                onChange={(next) => setRows((all) => all.map((r) => (r.id === row.id ? next : r)))}
                onRemove={() => setRows((all) => all.filter((r) => r.id !== row.id))}
              />
            ))}
          </div>
          <div>
            <button class="btn link" type="button" onClick={() => setRows((all) => [...all, newRow(others)])}>
              + Add a backup
            </button>
          </div>
          <small>Used in this order while this camera is offline. faicam switches back when it returns.</small>
        </div>

        <details open={moreOpen}>
          <summary>More settings</summary>
          <div class="group">
            <div class="two">
              <label>
                Frame rate limit
                <input type="number" min={1} step={1} value={fields.fps} onInput={(e) => set("fps", e.currentTarget.value)} placeholder="Camera's rate" />
              </label>
              <label>
                Resize to
                <input value={fields.size} onInput={(e) => set("size", e.currentTarget.value)} placeholder="e.g. 1280x720" />
              </label>
            </div>
            <div class="two">
              <label>
                Connect over
                <select value={fields.transport} onChange={(e) => set("transport", e.currentTarget.value as Fields["transport"])}>
                  <option value="auto">Automatic</option>
                  <option value="tcp">TCP</option>
                  <option value="udp">UDP</option>
                </select>
              </label>
              {kind === "camera" && (
                <label>
                  RTSP port
                  <input type="number" min={1} max={65535} value={fields.port} onInput={(e) => set("port", e.currentTarget.value)} placeholder="554" />
                </label>
              )}
            </div>
            <div class="two">
              <label>
                Connect timeout
                <input type="number" min={0.5} step={0.5} value={fields.openTimeout} onInput={(e) => set("openTimeout", e.currentTarget.value)} placeholder="5" />
                <small>Seconds to wait when connecting.</small>
              </label>
              <label>
                No-picture timeout
                <input type="number" min={0.5} step={0.5} value={fields.readTimeout} onInput={(e) => set("readTimeout", e.currentTarget.value)} placeholder="5" />
                <small>Seconds without a frame before reconnecting.</small>
              </label>
            </div>
            <p class="hint-text">
              Need something that isn't here?{" "}
              <button class="btn link" type="button" onClick={onOpenYaml}>
                Edit all settings as YAML
              </button>
            </p>
          </div>
        </details>
        <ErrorNote message={error} />
      </div>
    </Drawer>
  );
}

function TestOutcome({ test, kind }: { test: TestResult; kind: SourceKind }) {
  if (!test.ok) return <div class="result fail">{test.message}</div>;
  const { found } = test;
  const title =
    kind === "source"
      ? "Opened the source"
      : kind === "rtsp"
        ? "Connected to the stream"
        : found.brand_label
          ? `Found a ${found.brand_label} camera`
          : "Found the video stream";
  return (
    <div class="result ok">
      <img src={found.preview} alt="What the camera sees" />
      <div>
        <strong>{title}</strong>
        <br />
        <span>
          {found.width}×{found.height}
        </span>
      </div>
    </div>
  );
}

interface BackupEditorProps {
  row: BackupRow;
  cameras: string[];
  onChange: (row: BackupRow) => void;
  onRemove: () => void;
}

function BackupEditor({ row, cameras, onChange, onRemove }: BackupEditorProps) {
  const address = row.choice === "address";
  const missing = row.choice.startsWith("camera:") && !cameras.includes(row.choice.slice(7));
  return (
    <div class={address ? "backup-row" : "backup-row camera-only"}>
      <select aria-label="Backup" value={row.choice} onChange={(e) => onChange({ ...row, choice: e.currentTarget.value })}>
        {cameras.map((name) => (
          <option key={name} value={`camera:${name}`}>
            {name}
          </option>
        ))}
        {missing && <option value={row.choice}>{row.choice.slice(7)} (not found)</option>}
        <option value="address">Address or file</option>
      </select>
      {address && (
        <input
          aria-label="Backup address or file"
          placeholder="rtsp://… or ~/Movies/backup.mp4"
          value={row.value}
          onInput={(e) => onChange({ ...row, value: e.currentTarget.value })}
        />
      )}
      <button class="icon" type="button" aria-label="Remove this backup" onClick={onRemove}>
        ×
      </button>
    </div>
  );
}

function initialFields(camera: Camera | null): Fields {
  const s = camera?.settings;
  const kind = s?.kind ?? "camera";
  const differs = (value: number | undefined, normal: number) => (value && value !== normal ? String(value) : "");
  return {
    name: camera?.name ?? "",
    kind,
    ip: s?.ip ?? "",
    brand: s?.brand || "auto",
    path: s?.path ?? "",
    username: kind === "camera" ? (s?.username ?? "") : "",
    password: "",
    url: kind === "rtsp" ? (s?.source ?? "") : "",
    urlUsername: kind === "rtsp" ? (s?.username ?? "") : "",
    urlPassword: "",
    source: kind === "source" ? (s?.source ?? "") : "",
    fps: s?.fps ? String(s.fps) : "",
    size: s?.size ?? "",
    transport: s?.rtsp_transport ?? "auto",
    port: differs(s?.port, DEFAULT_PORT),
    streamPort: s?.stream_port ? String(s.stream_port) : "",
    openTimeout: differs(s?.open_timeout, DEFAULT_TIMEOUT),
    readTimeout: differs(s?.read_timeout, DEFAULT_TIMEOUT),
  };
}

const hasMoreSettings = (f: Fields) =>
  Boolean(f.fps || f.size || f.transport !== "auto" || f.port || f.openTimeout || f.readTimeout);

function initialRows(camera: Camera | null): BackupRow[] {
  return (camera?.settings.backups ?? []).map((b: Backup) => ({
    id: nextRow++,
    choice: b.type === "camera" ? `camera:${b.value}` : "address",
    value: b.type === "address" ? b.value : "",
  }));
}

function newRow(cameras: string[]): BackupRow {
  const first = cameras[0];
  return { id: nextRow++, choice: first ? `camera:${first}` : "address", value: "" };
}

function readRows(rows: BackupRow[]): Backup[] {
  return rows
    .map((row): Backup =>
      row.choice === "address" ? { type: "address", value: row.value.trim() } : { type: "camera", value: row.choice.slice(7) },
    )
    .filter((b) => b.value);
}
