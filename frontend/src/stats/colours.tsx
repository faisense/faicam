/** A colour per camera, in the order cameras were first seen, never reassigned. */

import { useEffect, useRef, useState } from "preact/hooks";

// Categorical colours, in this order, checked for colour-blind separation.
const PALETTE = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};

const slots = new Map<string, number>();

export interface Colour {
  colour: string;
  dashed: boolean; // past the eighth camera, colours repeat with dashed lines
}

export function colourOf(name: string, dark: boolean): Colour {
  if (!slots.has(name)) slots.set(name, slots.size);
  const palette = PALETTE[dark ? "dark" : "light"];
  const slot = slots.get(name)!;
  return { colour: palette[slot % palette.length]!, dashed: slot >= palette.length };
}

const scheme = matchMedia("(prefers-color-scheme: dark)");

export function useDarkMode(): boolean {
  const [dark, setDark] = useState(scheme.matches);
  useEffect(() => {
    const onChange = () => setDark(scheme.matches);
    scheme.addEventListener("change", onChange);
    return () => scheme.removeEventListener("change", onChange);
  }, []);
  return dark;
}

/** An element's width, kept current as the page resizes. */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export function Swatch({ colour }: { colour: string }) {
  return <span class="swatch" style={{ background: colour }} />;
}
