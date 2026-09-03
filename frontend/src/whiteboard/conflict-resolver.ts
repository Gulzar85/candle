/**
 * Operation conflict resolution.
 *
 * The offline sync engine needs deterministic rules for deciding whether a
 * locally-created operation can be safely replayed against a newer server
 * state. This module defines a per-operation-type policy and offers the
 * pure decision logic.
 *
 * The server remains the final authority. This resolver only decides how the
 * *client* should react — submit, rebase, or quarantine.
 *
 * Conflict rules (see docs/architecture/offline-conflict-matrix.md):
 *   create_stroke   — additive, safe to replay after newer strokes.
 *   delete_object   — verify the target still exists before deleting.
 *   clear_canvas    — destructive; requires explicit policy (quarantine for
 *                     manual review rather than silently wiping newer changes).
 */

export type ConflictDecision =
  | { readonly action: "submit" }
  | { readonly action: "rebase"; readonly newBaseVersion: number }
  | { readonly action: "reject" };

export type OperationType =
  | "create_stroke"
  | "delete_object"
  | "clear_canvas";

export interface ReconcileInput {
  readonly operation_type: OperationType;
  /** The base version the client believed when it created the op. */
  readonly base_version: number;
  /** The server's current version. */
  readonly server_version: number;
  /** Whether the client has a cached object snapshot. */
  readonly cached_object_exists?: boolean;
  /** For clear_canvas, whether ANY newer server ops exist beyond base. */
  readonly newer_server_ops_exist?: boolean;
}

/**
 * Decide how to reconcile a pending offline operation against the current
 * server version.
 */
export function decideReconciliation(input: ReconcileInput): ConflictDecision {
  const { operation_type, base_version, server_version } = input;

  // Not stale — safe to submit as-is.
  if (server_version === base_version) return { action: "submit" };

  switch (operation_type) {
    case "create_stroke":
      // Additive and commutative with concurrent strokes: safe to rebase.
      return { action: "rebase", newBaseVersion: server_version };

    case "delete_object": {
      // If the target is known to be gone already, rejecting is a no-op anyway;
      // the server will make it idempotent. But if it may still exist and we
      // don't know, be conservative: reject for manual review rather than
      // silently deleting something a partner may have modified.
      if (input.cached_object_exists === false) {
        return { action: "rebase", newBaseVersion: server_version };
      }
      return { action: "reject" };
    }

    case "clear_canvas":
      // Highly destructive. Never auto-replay onto a changed board. Quarantine
      // for explicit user confirmation.
      return { action: "reject" };

    default:
      return { action: "reject" };
  }
}

/**
 * Human-readable reason for a conflict (shown in the reconcile UI, not the
 * technical error string).
 */
export function describeConflict(
  operation_type: OperationType,
  base_version: number,
  server_version: number,
): string {
  const behind = server_version - base_version;
  switch (operation_type) {
    case "create_stroke":
      return `A stroke you drew offline can be added on top of ${behind} newer change(s).`;
    case "delete_object":
      return `A deletion you made offline needs review because the board changed ${behind} time(s) while you were away.`;
    case "clear_canvas":
      return `You cleared the board offline, but it has changed ${behind} time(s). Confirm before applying the clear.`;
    default:
      return "This offline change needs attention.";
  }
}
