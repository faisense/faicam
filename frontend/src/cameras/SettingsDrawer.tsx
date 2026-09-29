import { useEffect, useRef, useState } from "preact/hooks";
import { messageOf, post } from "../shared/api";
import { Button } from "../shared/components";
import type { SaveResult, ServiceSettings, Sharing } from "../shared/types";
import { Drawer, ErrorNote } from "./Drawer";

interface Props {
  settings: ServiceSettings;
  /** Opened from a camera's "share the camera links": start with links shared. */
  shareLinks?: boolean;
  onClose: () => void;
  onSaved: (result: SaveResult, message: string) => void;
  onOpenYaml: () => void;
}

export function SettingsDrawer({ settings, shareLinks = false, onClose, onSaved, onOpenYaml }: Props) {
  const { network, ui } = settings;
  const [net, setNet] = useState(network.enabled);
  const [codec, setCodec] = useState(network.codec);
  const [quality, setQuality] = useState(String(network.quality));
  const [level, setLevel] = useState(String(network.level));
  const [bind, setBind] = useState(network.bind);
  const [links, setLinks] = useState<Sharing>(shareLinks ? "network" : ui.links);
  const [linksProtected, setLinksProtected] = useState(ui.has_links_password);
  const [linksPassword, setLinksPassword] = useState("");
  const [page, setPage] = useState<Sharing>(ui.page);
  const [password, setPassword] = useState("");
  const [uiPort, setUiPort] = useState(String(ui.port));
  const [port, setPort] = useState(String(settings.port));
  const [slots, setSlots] = useState(String(settings.slots));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);
  const linksChoice = useRef<HTMLInputElement>(null);
  const overridden = settings.overridden;

  useEffect(() => (shareLinks ? linksChoice : first).current?.focus(), [shareLinks]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await post<SaveResult>("/api/settings", {
        network: { enabled: net, codec, quality, level, bind: bind.trim() },
        ui: {
          page,
          password,
          links,
          links_protected: links === "network" && linksProtected,
          links_password: linksPassword,
          port: uiPort,
        },
        port,
        slots,
      });
      onSaved(result, "Settings saved");
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer
      id="settings"
      title="Settings"
      onClose={onClose}
      onSubmit={save}
      footer={
        <>
          <Button class="btn primary" type="submit" busy={saving ? "Saving…" : null}>
            Save and restart
          </Button>
          <button class="btn ghost" type="button" onClick={onClose}>
            Cancel
          </button>
        </>
      }
    >
      <div class="fields">
        <section class="block">
          <h3>Camera links</h3>
          <p>The live video and pictures behind each camera's Copy link. They only show video: nobody can change anything with them.</p>
          <label class="check">
            <input ref={linksChoice} type="radio" name="links" checked={links === "local"} onChange={() => setLinks("local")} />
            Only this computer can open them
          </label>
          <label class="check">
            <input type="radio" name="links" checked={links === "network"} onChange={() => setLinks("network")} />
            <span>
              Phones, TVs and other computers can open them too
              <small class="hint-text">Anyone on your network can watch, unless you set a password.</small>
            </span>
          </label>
          {links === "network" && (
            <>
              <label class="check">
                <input type="checkbox" checked={linksProtected} onChange={(e) => setLinksProtected(e.currentTarget.checked)} />
                Ask for a password
              </label>
              {linksProtected && (
                <label>
                  Password for camera links
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={linksPassword}
                    onInput={(e) => setLinksPassword(e.currentTarget.value)}
                    placeholder={ui.has_links_password ? "Leave empty to keep it" : ""}
                  />
                  <small>Viewers type it once in their browser or app. Any username works.</small>
                </label>
              )}
            </>
          )}
        </section>

        <section class="block">
          <h3>This page</h3>
          <p>Where cameras are added, changed and paused.</p>
          <label class="check">
            <input type="radio" name="page" checked={page === "local"} onChange={() => setPage("local")} />
            Only this computer can open it
          </label>
          <label class="check">
            <input type="radio" name="page" checked={page === "network"} onChange={() => setPage("network")} />
            Other machines can open it too
          </label>
          {page === "network" && (
            <label>
              Password
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onInput={(e) => setPassword(e.currentTarget.value)}
                placeholder={ui.has_password ? "Leave empty to keep it" : ""}
              />
              <small>Needed to open the page from other machines. It opens every camera link too.</small>
            </label>
          )}
          <label>
            Port
            <input type="number" min={1} max={65535} value={uiPort} onInput={(e) => setUiPort(e.currentTarget.value)} />
            <small>For this page and the camera links.</small>
          </label>
        </section>

        <section class="block">
          <h3>Programs on other machines</h3>
          <label class="check">
            <input
              ref={first}
              type="checkbox"
              checked={net}
              disabled={overridden.includes("network")}
              onChange={(e) => setNet(e.currentTarget.checked)}
            />
            Share the cameras with programs over the network
          </label>
          {net && (
            <div class="group">
              <label>
                Picture format
                <select value={codec} onChange={(e) => setCodec(e.currentTarget.value as ServiceSettings["network"]["codec"])}>
                  <option value="jpeg">JPEG: smallest, very slightly lossy</option>
                  <option value="zstd">zstd: exact pixels, larger</option>
                  <option value="raw">Raw: exact pixels, largest</option>
                </select>
              </label>
              {codec === "jpeg" && (
                <label>
                  JPEG quality
                  <input type="number" min={1} max={100} value={quality} onInput={(e) => setQuality(e.currentTarget.value)} />
                  <small>1 to 100. Higher looks better and uses more bandwidth.</small>
                </label>
              )}
              {codec === "zstd" && (
                <label>
                  zstd level
                  <input type="number" min={1} max={22} value={level} onInput={(e) => setLevel(e.currentTarget.value)} />
                  <small>3 is a good balance; higher levels cost much more CPU.</small>
                </label>
              )}
              <label>
                Listen on
                <input value={bind} onInput={(e) => setBind(e.currentTarget.value)} placeholder="0.0.0.0" />
                <small>0.0.0.0 means every network this computer is on.</small>
              </label>
            </div>
          )}
          {overridden.length > 0 && (
            <p class="info">
              Set on the command line ({overridden.map((k) => `--${k}`).join(", ")}): those win over this page until faicam is
              started without them.
            </p>
          )}
        </section>

        <section class="block">
          <h3>Advanced</h3>
          <div class="two">
            <label>
              Control port
              <input type="number" min={1} max={65535} value={port} disabled={overridden.includes("port")} onInput={(e) => setPort(e.currentTarget.value)} />
              <small>Programs find cameras through it.</small>
            </label>
            <label>
              Frames kept
              <input type="number" min={2} max={64} value={slots} onInput={(e) => setSlots(e.currentTarget.value)} />
              <small>Per camera, in shared memory.</small>
            </label>
          </div>
          <p class="hint-text">
            Everything else:{" "}
            <button class="btn link" type="button" onClick={onOpenYaml}>
              Edit all settings as YAML
            </button>
          </p>
        </section>
        <p class="info">Saving these restarts faicam. Cameras reconnect within a few seconds.</p>
        <ErrorNote message={error} />
      </div>
    </Drawer>
  );
}
