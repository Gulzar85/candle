/**
 * JSON export (Phase 9). Pure -- no DOM except the Blob constructor.
 *
 * Wraps the server's reconstructed state (WhiteboardStateView's `objects`)
 * in a small, versioned envelope. Contains only what's needed to
 * reconstruct the board's visible content -- no auth tokens, actor ids,
 * timestamps, or other internal/private metadata. Matches the schema
 * `ImportService` (board_import.py) accepts, so export -> import is a
 * genuine round-trip through the same validated pipeline as any other
 * write, not a special case.
 */

import type { ServerObject } from "./repository";

export const SCHEMA_VERSION = 1;

export interface BoardExport {
  readonly schema_version: typeof SCHEMA_VERSION;
  readonly exported_at: string;
  readonly board_title: string;
  readonly objects: readonly ServerObject[];
}

export function buildExport(objects: readonly ServerObject[], boardTitle: string): BoardExport {
  return {
    schema_version: SCHEMA_VERSION,
    exported_at: new Date().toISOString(),
    board_title: boardTitle,
    objects: objects.map((o) => ({
      object_type: o.object_type,
      object_id: o.object_id,
      points: o.points,
      color: o.color,
      width: o.width,
      opacity: o.opacity,
      // creator_id deliberately omitted: a display-initials hint tied to a
      // specific account, not needed to reconstruct visible content, and
      // not worth carrying into a file that may be shared outside the app.
    })),
  };
}

export function exportToJsonBlob(objects: readonly ServerObject[], boardTitle: string): Blob {
  return new Blob([JSON.stringify(buildExport(objects, boardTitle), null, 2)], {
    type: "application/json",
  });
}
