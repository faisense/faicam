/** How a camera's state reads on its card. */

import type { Tone } from "../shared/components";
import type { Camera } from "../shared/types";

export interface Status {
  label: string;
  tone: Tone;
  note?: string;
  noteTone?: "warn" | "bad";
  detail?: string | null;
}

export function statusOf(camera: Camera): Status {
  const first = camera.settings.backups[0];
  const backup = first?.type === "camera" ? first.value : "the backup";
  switch (camera.state) {
    case "streaming":
      return camera.on_backup
        ? {
            label: "On backup",
            tone: "warn",
            note: `The camera is offline. Showing ${backup} instead.`,
            noteTone: "warn",
          }
        : { label: "Online", tone: "good" };
    case "paused":
      return {
        label: "Paused",
        tone: "idle",
        note: "Not streaming. Programs and links get no pictures until you resume it.",
      };
    case "connecting":
      return { label: "Connecting", tone: "idle", note: "Connecting to the camera…" };
    case "reconnecting":
      return {
        label: "Offline",
        tone: "bad",
        note: "Can't reach the camera. faicam keeps trying.",
        noteTone: "bad",
        detail: camera.error,
      };
    default:
      return { label: "Stopped", tone: "idle" };
  }
}

export function watching(n: number): string {
  if (n === 0) return "no programs watching";
  return n === 1 ? "1 program watching" : `${n} programs watching`;
}

/** A camera's live link, as other devices reach it when they may. */
export const liveUrl = (base: string, name: string): string =>
  `${base}/live/${encodeURIComponent(name)}.mjpg`;
