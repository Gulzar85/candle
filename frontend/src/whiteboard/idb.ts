/**
 * Thin IndexedDB abstraction — a minimal promise-based wrapper over the
 * raw IDB API. This module owns all direct `indexedDB` interaction so
 * every other module in the project depends on this single seam.
 *
 * Design constraints:
 *  - No external dependencies (zero npm packages for IDB).
 *  - Promises, not callbacks.
 *  - Transaction helpers that abort on error (automatic rollback).
 *  - Version upgrade callbacks for schema migration.
 */

const DB_NAME = "candle-whiteboard";
const DB_VERSION = 1;

// ---------------------------------------------------------------------------
// Open / upgrade
// ---------------------------------------------------------------------------

export interface StoreDefinition {
  readonly name: string;
  readonly keyPath?: string | readonly string[];
  readonly indexes?: ReadonlyArray<{
    readonly name: string;
    readonly keyPath: string | readonly string[];
    readonly options?: IDBIndexParameters;
  }>;
}

function upgradeSchema(
  db: IDBDatabase,
  _oldVersion: number,
  _newVersion: number,
  stores: readonly StoreDefinition[],
): void {
  for (const def of stores) {
    if (!db.objectStoreNames.contains(def.name)) {
      const store = db.createObjectStore(def.name, {
        keyPath: def.keyPath as string | string[] | undefined,
      });
      for (const idx of def.indexes ?? []) {
        store.createIndex(idx.name, idx.keyPath as string | string[], idx.options);
      }
    }
  }
}

let dbPromise: Promise<IDBDatabase> | null = null;

/**
 * Open (or reuse) the IndexedDB connection. The first call triggers
 * `onupgradeneeded` which creates the object stores.
 */
export function openDB(stores: readonly StoreDefinition[]): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;

  dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = (event: IDBVersionChangeEvent) => {
      const db = request.result;
      upgradeSchema(db, event.oldVersion ?? 0, event.newVersion ?? 0, stores);
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => {
      dbPromise = null;
      reject(request.error);
    };
  });

  return dbPromise;
}

/**
 * Close the database connection and reset the cached promise.
 * Useful for testing or forcing a re-open with a new schema version.
 */
export function closeDB(): void {
  if (dbPromise) {
    dbPromise.then((db) => db.close()).catch(() => {});
    dbPromise = null;
  }
}

// ---------------------------------------------------------------------------
// Transaction helpers
// ---------------------------------------------------------------------------

/** Mode shorthand. */
export type IDBMode = "readonly" | "readwrite";

/**
 * Run a callback inside a single-store transaction. If the callback throws
 * or returns a rejected promise the transaction is automatically aborted.
 */
export function withStore<T>(
  db: IDBDatabase,
  storeName: string,
  mode: IDBMode,
  callback: (store: IDBObjectStore) => T | Promise<T>,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    const store = tx.objectStore(storeName);
    let result: T;

    try {
      const r = callback(store);
      if (r && typeof (r as Promise<T>).then === "function") {
        (r as Promise<T>).then(
          (val) => {
            result = val;
          },
          (err) => {
            tx.abort();
            reject(err);
          },
        );
      } else {
        result = r as T;
      }
    } catch (err) {
      tx.abort();
      reject(err);
      return;
    }

    tx.oncomplete = () => resolve(result!);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error("Transaction aborted"));
  });
}

// ---------------------------------------------------------------------------
// CRUD helpers
// ---------------------------------------------------------------------------

export function idbGet<T>(db: IDBDatabase, storeName: string, key: IDBValidKey): Promise<T | undefined> {
  return withStore(db, storeName, "readonly", (store) => {
    return new Promise<T | undefined>((resolve, reject) => {
      const req = store.get(key);
      req.onsuccess = () => resolve(req.result as T | undefined);
      req.onerror = () => reject(req.error);
    });
  });
}

export function idbGetAll<T>(db: IDBDatabase, storeName: string): Promise<T[]> {
  return withStore(db, storeName, "readonly", (store) => {
    return new Promise<T[]>((resolve, reject) => {
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result as T[]);
      req.onerror = () => reject(req.error);
    });
  });
}

export function idbGetAllFromIndex<T>(
  db: IDBDatabase,
  storeName: string,
  indexName: string,
  key: IDBValidKey | IDBKeyRange,
): Promise<T[]> {
  return withStore(db, storeName, "readonly", (store) => {
    const index = store.index(indexName);
    return new Promise<T[]>((resolve, reject) => {
      const req = index.getAll(key);
      req.onsuccess = () => resolve(req.result as T[]);
      req.onerror = () => reject(req.error);
    });
  });
}

export function idbPut<T>(db: IDBDatabase, storeName: string, value: T): Promise<IDBValidKey> {
  return withStore(db, storeName, "readwrite", (store) => {
    return new Promise<IDBValidKey>((resolve, reject) => {
      const req = store.put(value);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  });
}

export function idbDelete(db: IDBDatabase, storeName: string, key: IDBValidKey): Promise<void> {
  return withStore(db, storeName, "readwrite", (store) => {
    return new Promise<void>((resolve, reject) => {
      const req = store.delete(key);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  });
}

export function idbClear(db: IDBDatabase, storeName: string): Promise<void> {
  return withStore(db, storeName, "readwrite", (store) => {
    return new Promise<void>((resolve, reject) => {
      const req = store.clear();
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  });
}

export function idbCount(db: IDBDatabase, storeName: string): Promise<number> {
  return withStore(db, storeName, "readonly", (store) => {
    return new Promise<number>((resolve, reject) => {
      const req = store.count();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  });
}
