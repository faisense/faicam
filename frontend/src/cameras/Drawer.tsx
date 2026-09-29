import type { ComponentChildren } from "preact";
import { useEffect, useRef } from "preact/hooks";

interface Props {
  id: string;
  title: string;
  wide?: boolean;
  onClose: () => void;
  onSubmit: () => void;
  footer: ComponentChildren;
  children: ComponentChildren;
}

/** A side panel with a form: a title, fields, and buttons at the bottom. */
export function Drawer({ id, title, wide = false, onClose, onSubmit, footer, children }: Props) {
  return (
    <>
      <div class="scrim" onClick={onClose} />
      <aside class={wide ? "drawer wide" : "drawer"} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
        <form
          noValidate
          autoComplete="off"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <header>
            <h2 id={`${id}-title`}>{title}</h2>
            <button class="icon" type="button" aria-label="Close" onClick={onClose}>
              ×
            </button>
          </header>
          {children}
          <div class="foot">{footer}</div>
        </form>
      </aside>
    </>
  );
}

/** A ref that focuses its element once it appears. */
export function useFocus<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => ref.current?.focus(), []);
  return ref;
}

export function ErrorNote({ message }: { message: string | null }) {
  return (
    <p class="error" role="alert" hidden={!message}>
      {message}
    </p>
  );
}
