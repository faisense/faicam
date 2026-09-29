/**
 * The full-screen viewer a camera link opens in a browser (phones included). Video
 * only: pausing and editing belong to the cameras page.
 *
 * The video starts after the page has loaded, since an endless MJPEG stream otherwise
 * keeps the browser's loading indicator spinning. The camera's state is checked every
 * few seconds, so a paused camera shows why, and plays again by itself once resumed.
 */
import "../shared/theme.css";
import "./viewer.css";

const CHECK_MS = 3000;

const video = document.getElementById("video") as HTMLImageElement;
const message = document.getElementById("message")!;
const label = document.getElementById("live")!;
const name = decodeURIComponent(location.pathname.split("/").pop() ?? "").replace(/\.mjpg$/, "");
const options = new URLSearchParams(location.search);
options.set("mjpeg", "1");
let playing = false;

document.title = `${name} · faicam`;
document.getElementById("name")!.textContent = name;
video.alt = `${name}, live`;
video.addEventListener("error", () => stop()); // the stream ended; the next check restarts it

function play(): void {
  options.set("t", String(Date.now())); // a fresh connection each time
  video.src = `/live/${encodeURIComponent(name)}.mjpg?${options}`;
  playing = true;
  show(null);
}

function stop(text: string | null = null): void {
  video.removeAttribute("src");
  playing = false;
  show(text);
}

function show(text: string | null): void {
  message.textContent = text ?? "";
  message.hidden = text === null;
  label.hidden = text !== null;
}

async function check(): Promise<void> {
  try {
    const res = await fetch(`/live/${encodeURIComponent(name)}.json`, { cache: "no-store" });
    const body = (await res.json().catch(() => ({}))) as { state?: string; error?: string };
    if (!res.ok) stop(body.error ?? "This camera isn't available.");
    else if (body.state === "streaming") {
      if (!playing) play();
    } else if (body.state === "paused") stop(`${name} is paused. It plays here by itself when it's resumed.`);
    else stop(`Waiting for ${name}…`);
  } catch {
    stop("Can't reach faicam. Trying again…");
  }
  window.setTimeout(check, CHECK_MS);
}

addEventListener("load", () => void check());
