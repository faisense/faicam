import { useEffect, useState } from "preact/hooks";
import { copyText } from "../shared/clipboard";
import { useToast } from "../shared/components";
import type { Links } from "../shared/types";
import { useFocus } from "./Drawer";
import { liveUrl } from "./status";

interface Props {
  name: string;
  links: Links;
  editable: boolean;
  onClose: () => void;
  onShareLinks: () => void;
}

/** A camera full size, with the link other apps and devices can open. */
export function LiveView({ name, links, editable, onClose, onShareLinks }: Props) {
  const toast = useToast();
  const closeRef = useFocus<HTMLButtonElement>();
  // Math.random rather than crypto.randomUUID, which plain http:// pages don't have.
  const [streamId] = useState(() => Math.random().toString(36).slice(2) + Date.now().toString(36));
  const link = liveUrl(links.base, name);

  // Ask the server to end the stream on close: Safari keeps closed MJPEG streams open.
  useEffect(
    () => () => {
      void fetch(`/api/stop-stream?id=${streamId}`, { method: "POST", keepalive: true }).catch(() => {});
    },
    [streamId],
  );

  const copy = async () => toast((await copyText(link)) ? "Link copied" : link);
  const share = editable && (
    <button class="btn link" type="button" onClick={onShareLinks}>
      share the camera links
    </button>
  );

  return (
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="live-title"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div class="modal-box">
        <header>
          <h2>
            <span id="live-title">{name}</span>
            <small class="badge">LIVE</small>
          </h2>
          <button ref={closeRef} class="icon" type="button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <img src={`/live/${encodeURIComponent(name)}.mjpg?id=${streamId}`} alt={`${name}, live`} />
        <div class="link-row">
          <input readOnly value={link} aria-label="Web address of this camera's live video" />
          <button class="btn secondary" type="button" onClick={copy}>
            Copy link
          </button>
        </div>
        <p class="link-hint">
          Opens in a browser, VLC or OBS, and in code with <code>cv2.VideoCapture(link)</code>. Add{" "}
          <code>?fps=30&amp;width=1920</code> for more.
        </p>
        <p class="link-hint">
          {links.shared ? (
            links.password ? (
              "Phones, TVs and other computers on your network can open it with the camera-links password."
            ) : (
              "Phones, TVs and other computers on your network can open it."
            )
          ) : links.page_shared ? (
            <>Other devices can open it with the page password. To let viewers watch without it, {share || "share the camera links in Settings"}.</>
          ) : (
            <>It only opens on this computer. To watch on a phone, a TV or another computer, {share || "share the camera links in Settings"}.</>
          )}
        </p>
      </div>
    </div>
  );
}
