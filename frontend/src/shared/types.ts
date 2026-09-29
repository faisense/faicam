/** What faicam's API returns (see src/faicam/server.py and stats.py). */

export type StreamState = "connecting" | "streaming" | "reconnecting" | "paused" | "stopped";
export type Sharing = "local" | "network";

// ---- the cameras page: GET /api/state ----

export type SourceKind = "camera" | "rtsp" | "source";

export interface Backup {
  type: "camera" | "address";
  value: string;
}

/** A camera's form fields. Passwords are never sent, only whether one is set. */
export interface CameraSettings {
  kind: SourceKind;
  ip: string;
  port: number;
  username: string | null;
  has_password: boolean;
  brand: string;
  path: string;
  source: string;
  backups: Backup[];
  fps: number | null;
  size: string;
  rtsp_transport: "auto" | "tcp" | "udp";
  paused: boolean;
  open_timeout: number;
  read_timeout: number;
}

export interface Camera {
  name: string;
  state: StreamState;
  on_backup: boolean;
  fps: number;
  width: number;
  height: number;
  error: string | null;
  reconnects: number;
  watching: number; // programs, on this machine or others
  source: string;
  settings: CameraSettings;
}

export interface ServiceSettings {
  network: { enabled: boolean; codec: "jpeg" | "zstd" | "raw"; quality: number; level: number; bind: string };
  ui: {
    page: Sharing;
    links: Sharing;
    port: number;
    has_password: boolean;
    has_links_password: boolean;
  };
  port: number;
  slots: number;
  overridden: string[]; // settings given on the command line, which win over the file
  editable: boolean;
}

/** Where camera links point, and who can open them. */
export interface Links {
  base: string;
  shared: boolean; // other devices can open camera links
  page_shared: boolean; // other machines can open the page (with its password)
  password: boolean; // camera links ask other devices for a password
}

export interface State {
  cameras: Camera[];
  brands: { key: string; label: string }[];
  editable: boolean;
  config_path: string | null;
  service: ServiceSettings;
  links: Links;
}

/** The answer to a change: applied, or faicam restarts to apply it. */
export interface SaveResult {
  ok: boolean;
  restart?: boolean;
  url?: string | null;
  note?: string;
}

export interface DetectResult {
  brand: string | null;
  brand_label: string | null;
  path: string | null;
  width: number;
  height: number;
  preview: string; // a data: URL
}

// ---- the stats page: GET /api/stats ----

export interface StatsCamera {
  name: string;
  state: StreamState;
  on_backup: boolean;
  source: string;
  error: string | null;
  fps: number;
  width: number;
  height: number;
  online_seconds: number;
  backup_seconds: number;
  reconnects: number;
  last_frame_age: number | null;
  frames_shared: number;
  frames_capped: number;
  frames_unwatched: number;
  work_ms: number;
  cpu: number;
  viewers: number;
  sent_rate: number;
}

export type Watcher =
  | { camera: string; kind: "programs"; count: number }
  | { camera: string; kind: "machine"; address: string; since: number }
  | { camera: string; kind: "link" | "panel"; address: string; app: string; since: number; bytes: number };

export interface StatsNow {
  time: number;
  started: number | null;
  process: { cpu: number; memory: number | null; threads: number; cores: number | null; pid: number };
  totals: { viewers: number; sent_rate: number; online: number };
  cameras: StatsCamera[];
  watchers: Watcher[];
}

export interface Sample {
  t: number;
  cpu: number;
  memory: number | null;
  sent: number;
  viewers: number;
  cameras: Record<string, { fps: number; viewers: number; sent: number }>;
}

export interface StatsReport {
  interval: number;
  history: number;
  now: StatsNow | Record<string, never>; // empty until the first sample
  samples: Sample[];
}
