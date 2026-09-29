import { useEffect, useRef, useState } from "preact/hooks";
import { api, messageOf, post } from "../shared/api";
import { copyText } from "../shared/clipboard";
import { Button, Pill, useToast } from "../shared/components";
import type { Camera, StreamState } from "../shared/types";
import { liveUrl, statusOf, watching } from "./status";

interface Props {
  camera: Camera;
  editable: boolean;
  linkBase: string;
  onEdit: (name: string) => void;
  onWatch: (name: string) => void;
  onChanged: () => void;
}

export function CameraCard({ camera, editable, linkBase, onEdit, onWatch, onChanged }: Props) {
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [pausing, setPausing] = useState<string | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const picture = useThumbnail(camera.name, camera.state);
  const status = statusOf(camera);
  const { name, state } = camera;
  const paused = state === "paused";
  const streaming = state === "streaming";

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  const watch = () => {
    if (paused) toast(`${name} is paused. Resume it to watch.`);
    else if (!streaming) toast(`${name} has no picture right now.`);
    else onWatch(name);
  };

  const togglePause = async () => {
    setPausing(paused ? "Resuming…" : "Pausing…");
    try {
      await post(`/api/cameras/${encodeURIComponent(name)}/${paused ? "resume" : "pause"}`, {});
      toast(paused ? `Resumed ${name}` : `Paused ${name}`);
      onChanged();
    } catch (error) {
      toast(messageOf(error));
    } finally {
      setPausing(null);
    }
  };

  const remove = async () => {
    try {
      await api(`/api/cameras/${encodeURIComponent(name)}`, { method: "DELETE" });
      toast(`Removed ${name}`);
      onChanged();
    } catch (error) {
      toast(messageOf(error));
      setConfirming(false);
    }
  };

  const copy = async () => {
    const link = liveUrl(linkBase, name);
    toast((await copyText(link)) ? "Link copied" : link);
  };

  const placeholder = paused
    ? "Paused"
    : streaming
      ? "Loading picture…"
      : state === "reconnecting"
        ? "No picture"
        : "Connecting…";

  return (
    <article class={`card${picture ? " has-pic" : ""}${paused ? " paused" : ""}`}>
      <button class="thumb" type="button" aria-label={`Watch ${name} live`} onClick={watch}>
        <img src={picture ?? undefined} alt="" />
        <span class="nopic">{placeholder}</span>
        <span class="hint">Watch live</span>
      </button>
      <div class="card-body">
        <div class="row">
          <h3 title={camera.source}>{name}</h3>
          <Pill tone={status.tone}>{status.label}</Pill>
        </div>
        <p class="meta">
          {streaming
            ? `${camera.width}×${camera.height} · ${Math.round(camera.fps)} fps · ${watching(camera.watching)}`
            : camera.source}
        </p>
        {status.note && (
          <p class={`note ${status.noteTone ?? ""}`}>
            {status.note}
            {status.detail && <small>{status.detail}</small>}
          </p>
        )}
        {confirming ? (
          <div class="confirm">
            <span>
              Remove <b>{name}</b>?
            </span>
            <button ref={confirmRef} class="btn danger" type="button" onClick={remove}>
              Remove
            </button>
            <button class="btn ghost" type="button" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <div class="actions">
            {editable && (
              <button class="btn ghost" type="button" onClick={() => onEdit(name)}>
                Edit
              </button>
            )}
            {editable && (
              <Button class="btn ghost" busy={pausing} onClick={togglePause}>
                {paused ? "Resume" : "Pause"}
              </Button>
            )}
            <button class="btn ghost" type="button" onClick={copy}>
              Copy link
            </button>
            {editable && (
              <button class="btn ghost" type="button" onClick={() => setConfirming(true)}>
                Remove
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

/**
 * The camera's latest picture, refreshed every second while it streams. Each new
 * picture loads off-screen first, so the card never flashes empty. A paused camera
 * keeps its last picture (dimmed); an offline one shows none.
 */
function useThumbnail(name: string, state: StreamState): string | null {
  const [src, setSrc] = useState<string | null>(null);
  const streaming = state === "streaming";
  const keep = streaming || state === "paused";

  useEffect(() => {
    if (!keep) setSrc(null);
  }, [keep]);

  useEffect(() => {
    if (!streaming) return;
    let alive = true;
    let timer = 0;
    const load = () => {
      if (!alive) return;
      if (document.hidden) {
        timer = window.setTimeout(load, 1000);
        return;
      }
      const img = new Image();
      img.onload = () => {
        if (!alive) return;
        setSrc(img.src);
        timer = window.setTimeout(load, 1000);
      };
      img.onerror = () => {
        if (alive) timer = window.setTimeout(load, 3000);
      };
      img.src = `/snapshot/${encodeURIComponent(name)}.jpg?width=480&quality=75&t=${Date.now()}`;
    };
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [name, streaming]);

  return src;
}
