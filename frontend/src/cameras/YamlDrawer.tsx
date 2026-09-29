import { useEffect, useState } from "preact/hooks";
import { api, messageOf, post } from "../shared/api";
import { Button, useToast } from "../shared/components";
import type { SaveResult } from "../shared/types";
import { Drawer, ErrorNote, useFocus } from "./Drawer";

interface Props {
  path: string | null;
  onClose: () => void;
  onSaved: (result: SaveResult, message: string) => void;
}

/** Every setting, as the YAML file itself. Passwords arrive masked. */
export function YamlDrawer({ path, onClose, onSaved }: Props) {
  const toast = useToast();
  const [text, setText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editor = useFocus<HTMLTextAreaElement>();

  const load = async () => {
    try {
      setText((await api<{ yaml: string }>("/api/config")).yaml);
      setError(null);
    } catch (err) {
      toast(messageOf(err));
      onClose();
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (text !== null) editor.current?.focus();
  }, [text === null]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      onSaved(await post<SaveResult>("/api/config", { yaml: text ?? "" }), "Settings saved");
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setSaving(false);
    }
  };

  const indent = (event: KeyboardEvent) => {
    if (event.key !== "Tab") return; // indent with two spaces instead of leaving the editor
    event.preventDefault();
    const box = event.currentTarget as HTMLTextAreaElement;
    box.setRangeText("  ", box.selectionStart, box.selectionEnd, "end");
    setText(box.value);
  };

  return (
    <Drawer
      id="yaml"
      title="All settings (YAML)"
      wide
      onClose={onClose}
      onSubmit={save}
      footer={
        <>
          <Button class="btn primary" type="submit" busy={saving ? "Checking…" : null} disabled={text === null}>
            Save
          </Button>
          <button class="btn ghost" type="button" onClick={onClose}>
            Cancel
          </button>
          <span class="spacer" />
          <button class="btn ghost" type="button" onClick={load}>
            Undo my changes
          </button>
        </>
      }
    >
      <div class="fields column">
        <p class="info">
          The whole <code>{path ?? "cameras.yaml"}</code> file. Passwords show as <code>********</code>: leave them to keep them, or type a
          new one. Every option is described in the project's <code>configs/example.yaml</code>.
        </p>
        <textarea
          ref={editor}
          class="yaml"
          spellcheck={false}
          aria-label="Settings in YAML"
          value={text ?? "Loading…"}
          disabled={text === null}
          onInput={(e) => setText(e.currentTarget.value)}
          onKeyDown={indent}
        />
        <ErrorNote message={error} />
      </div>
    </Drawer>
  );
}
