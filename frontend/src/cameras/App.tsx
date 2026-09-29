import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { api } from "../shared/api";
import { OfflineBanner, Spinner, useToast, Wordmark } from "../shared/components";
import type { SaveResult, State } from "../shared/types";
import { CameraCard } from "./CameraCard";
import { CameraDrawer } from "./CameraDrawer";
import { LiveView } from "./LiveView";
import { SettingsDrawer } from "./SettingsDrawer";
import { YamlDrawer } from "./YamlDrawer";

type Panel =
  | { kind: "camera"; name: string | null }
  | { kind: "settings"; shareLinks?: boolean }
  | { kind: "yaml" };

const POLL_MS = 1500;
const RESTART_POLL_MS = 1000;

export function App() {
  const toast = useToast();
  const [state, setState] = useState<State | null>(null);
  const [offline, setOffline] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [live, setLive] = useState<string | null>(null);
  const [restartText, setRestartText] = useState<string | null>(null);
  const restarting = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    window.clearTimeout(timer.current);
    try {
      const next = await api<State>("/api/state");
      if (restarting.current) {
        location.reload(); // faicam is back with the new settings
        return;
      }
      setOffline(false);
      setState(next);
    } catch {
      setOffline(!restarting.current);
    }
    timer.current = window.setTimeout(refresh, restarting.current ? RESTART_POLL_MS : POLL_MS);
  }, []);

  useEffect(() => {
    void refresh();
    return () => window.clearTimeout(timer.current);
  }, [refresh]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (live) setLive(null);
      else setPanel(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [live]);

  const beginRestart = (url: string | null | undefined) => {
    setPanel(null);
    setLive(null);
    restarting.current = true;
    window.clearTimeout(timer.current); // the server answers for a moment before it restarts
    const port = url ? new URL(url).port : location.port;
    if (port && port !== location.port) {
      const next = `${location.protocol}//${location.hostname}:${port}/`;
      setRestartText(`The page is moving to ${next}. Taking you there…`);
      window.setTimeout(() => location.assign(next), 4000);
      return;
    }
    setRestartText("Applying the new settings. This page reconnects by itself.");
    timer.current = window.setTimeout(refresh, 2000);
  };

  const applied = (result: SaveResult, message: string) => {
    if (result.restart) {
      beginRestart(result.url);
      return;
    }
    setPanel(null);
    toast(result.note ?? message);
    void refresh();
  };

  const cameras = state?.cameras ?? [];
  const editable = state?.editable ?? false;
  const online = cameras.filter((c) => c.state === "streaming").length;
  const paused = cameras.filter((c) => c.state === "paused").length;
  const summary = !state
    ? "Loading…"
    : cameras.length
      ? `${cameras.length} camera${cameras.length === 1 ? "" : "s"} · ${online} online${paused ? ` · ${paused} paused` : ""}`
      : "No cameras";
  const addCamera = () => setPanel({ kind: "camera", name: null });

  return (
    <>
      <div class="wrap">
        <div class="top">
          <div class="brand">
            <Wordmark />
            <span class="summary">{summary}</span>
          </div>
          <div class="top-actions">
            <a class="btn ghost" href="/stats">
              Stats
            </a>
            {editable && (
              <button class="btn ghost" type="button" onClick={() => setPanel({ kind: "settings" })}>
                Settings
              </button>
            )}
            {editable && cameras.length > 0 && (
              <button class="btn primary" type="button" onClick={addCamera}>
                + Add camera
              </button>
            )}
          </div>
        </div>

        <OfflineBanner show={offline} />

        {state && editable && cameras.length === 0 && (
          <div class="empty">
            <h2>No cameras yet</h2>
            <p>Add a camera with its IP address, username and password. faicam finds the video stream for you.</p>
            <button class="btn primary" type="button" onClick={addCamera}>
              + Add your first camera
            </button>
          </div>
        )}

        <div class="grid">
          {state &&
            cameras.map((camera) => (
              <CameraCard
                key={camera.name}
                camera={camera}
                editable={editable}
                linkBase={state.links.base}
                onEdit={(name) => setPanel({ kind: "camera", name })}
                onWatch={setLive}
                onChanged={refresh}
              />
            ))}
        </div>

        {state && (
          <footer class="page">
            {editable ? (
              <>
                Settings are saved in <code>{state.config_path}</code>
              </>
            ) : (
              <>
                Running a single source. To add and edit cameras, start faicam with a config file: <code>faicam</code>
              </>
            )}
          </footer>
        )}
      </div>

      {state && panel?.kind === "camera" && (
        <CameraDrawer
          camera={cameras.find((c) => c.name === panel.name) ?? null}
          cameras={cameras}
          brands={state.brands}
          onClose={() => setPanel(null)}
          onSaved={applied}
          onOpenYaml={() => setPanel({ kind: "yaml" })}
        />
      )}
      {state && panel?.kind === "settings" && (
        <SettingsDrawer
          settings={state.service}
          shareLinks={panel.shareLinks}
          onClose={() => setPanel(null)}
          onSaved={applied}
          onOpenYaml={() => setPanel({ kind: "yaml" })}
        />
      )}
      {state && panel?.kind === "yaml" && (
        <YamlDrawer path={state.config_path} onClose={() => setPanel(null)} onSaved={applied} />
      )}
      {state && live && (
        <LiveView
          name={live}
          links={state.links}
          editable={editable}
          onClose={() => setLive(null)}
          onShareLinks={() => {
            setLive(null);
            setPanel({ kind: "settings", shareLinks: true });
          }}
        />
      )}
      {restartText && (
        <div class="modal" role="alertdialog" aria-live="assertive">
          <div class="restarting">
            <Spinner big />
            <h2>Restarting faicam</h2>
            <p>{restartText}</p>
          </div>
        </div>
      )}
    </>
  );
}
