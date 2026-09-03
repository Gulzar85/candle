/**
 * Color utilities and the default palette. Pure — no DOM.
 *
 * Colors drive both strokes and the per-user presence identity. Keeping them
 * as plain data (hex strings) means style decisions stay in the template layer
 * and the engine just receives resolved values.
 */

/** Default illustration palette for strokes. */
export const DEFAULT_PALETTE = [
  "#2563eb",
  "#dc2626",
  "#16a34a",
  "#d97706",
  "#7c3aed",
  "#171717",
] as const;

export type PaletteColor = (typeof DEFAULT_PALETTE)[number];

/** True when `value` is a normalized 6-digit hex color like `#2563eb`. */
export function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}

/** Clamp a value into [min, max]. */
export function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value;
}

/** Clamp and round a stroke width into the allowed range. */
export function normalizeWidth(width: number): number {
  return Math.round(clamp(width, 1, 64));
}

/** Clamp an opacity into [0, 1] keeping a little precision. */
export function normalizeOpacity(opacity: number): number {
  return Math.round(clamp(opacity, 0, 1) * 1000) / 1000;
}

/**
 * Resolve a color to a canonical lowercase 6-digit hex, defaulting invalid
 * input to the palette's first color. This keeps stroke data canonical and
 * comparable (important for future sync/dedup).
 */
export function resolveColor(value: string): PaletteColor {
  if (isHexColor(value)) return value.toLowerCase() as PaletteColor;
  return DEFAULT_PALETTE[0];
}
