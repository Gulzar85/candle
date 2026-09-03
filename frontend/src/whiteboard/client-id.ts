/**
 * Stable client identifier — persisted in IndexedDB so it survives browser
 * restarts. Used to correlate operations across sessions for sync diagnostics.
 *
 * NOT an authentication credential. NOT secret. Just a stable UUID per
 * browser installation.
 *
 * Storage: `client_id` record in the `meta` object store (created by the
 * WhiteboardLocalStore schema).
 *
 * IMPORTANT: callers must ensure the store is connected (schema created)
 * before calling getClientId, i.e. after `WhiteboardLocalStore.connect()`.
 */

let cachedClientId: string | null = null;

import { idbGet, idbPut } from "./idb";

/**
 * Get or create the persistent client ID in the given (connected) database.
 */
export async function getClientId(db: IDBDatabase): Promise<string> {
  if (cachedClientId) return cachedClientId;

  const existing = await idbGet<{ key: string; value: string }>(db, "meta", "client_id");
  if (existing?.value) {
    cachedClientId = existing.value;
    return cachedClientId;
  }

  const id =
    typeof globalThis.crypto !== "undefined" &&
    typeof globalThis.crypto.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

  await idbPut(db, "meta", { key: "client_id", value: id });
  cachedClientId = id;
  return id;
}

/** Reset the cached client ID (for testing only). */
export function resetClientIdCache(): void {
  cachedClientId = null;
}
