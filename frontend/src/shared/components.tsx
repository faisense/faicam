/** Small pieces both pages use. */

import { createContext, type ComponentChildren, type ComponentProps } from "preact";
import { useCallback, useContext, useRef, useState } from "preact/hooks";

export function Wordmark() {
  return (
    <h1>
      f<span>ai</span>cam
    </h1>
  );
}

export type Tone = "good" | "warn" | "bad" | "idle";

export function Pill({ tone, children }: { tone: Tone; children: ComponentChildren }) {
  return <span class={`pill ${tone}`}>{children}</span>;
}

export function Spinner({ big = false }: { big?: boolean }) {
  return <span class={big ? "spin big" : "spin"} aria-hidden="true" />;
}

/** A button that shows a spinner and says what it's doing while ``busy`` is set. */
export function Button({ busy, children, disabled, ...props }: ComponentProps<"button"> & { busy?: string | null }) {
  return (
    <button type="button" {...props} disabled={Boolean(busy) || disabled}>
      {busy ? (
        <>
          <Spinner />
          {busy}
        </>
      ) : (
        children
      )}
    </button>
  );
}

export function OfflineBanner({ show }: { show: boolean }) {
  return (
    <div class="banner" hidden={!show}>
      faicam isn't answering. Check that <code>faicam</code> is still running; this page
      reconnects by itself.
    </div>
  );
}

// ---- short messages at the bottom of the page ----

const ToastContext = createContext<(message: string) => void>(() => {});

export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ComponentChildren }) {
  const [message, setMessage] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((text: string) => {
    setMessage(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(""), 2600);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div class="toast" role="status" hidden={!message}>
        {message}
      </div>
    </ToastContext.Provider>
  );
}
