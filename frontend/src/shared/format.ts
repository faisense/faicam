/** Numbers, sizes and times the way people read them. */

export function bytes(n: number | null | undefined, short = false): string {
  if (n == null) return "—";
  for (const [unit, size] of [["GB", 1e9], ["MB", 1e6], ["KB", 1e3]] as const) {
    if (n >= size) {
      const v = n / size;
      return `${v < 10 && !short ? v.toFixed(1) : Math.round(v)} ${unit}`;
    }
  }
  return `${Math.round(n)} B`;
}

export const rate = (n: number | null | undefined, short = false): string => `${bytes(n, short)}/s`;

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} days ${h % 24} h`;
}

export const count = (n: number): string => n.toLocaleString();

export const plural = (n: number, one: string, many = `${one}s`): string =>
  `${count(n)} ${n === 1 ? one : many}`;

export function clock(t: number, withSeconds = false): string {
  return new Date(t * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
  });
}
