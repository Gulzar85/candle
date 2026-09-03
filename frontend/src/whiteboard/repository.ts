/**
 * WhiteboardRepository — persistence adapter for the whiteboard engine.
 *
 * Abstracts HTTP calls to the Django API so the engine and renderer stay
 * decoupled from the transport layer. This adapter is the seam where Phase 5
 * will attach WebSocket transport without changing the engine.
 *
 * Responsibilities:
 *  - Load the whiteboard state (reconstructed from server operations)
 *  - Submit operations (single or batch)
 *  - Fetch incremental operations (for future sync)
 *  - Read the CSRF token from the page meta tag
 *
 * The repository does NOT manage queues, retries, or save status — that is
 * the responsibility of the SyncManager (sync.ts).
 */

import type { Stroke } from "./types";

/** Server-reconstructed object state (from state_reconstruction.py). */
export interface ServerObject {
  readonly object_type: string;
  readonly object_id: string;
  readonly points: readonly { readonly x: number; readonly y: number }[];
  readonly color: string;
  readonly width: number;
  readonly opacity: number;
  readonly creator_id?: string;
}

/** Response from GET /api/whiteboards/<id>/ */
export interface LoadStateResponse {
  readonly version: number;
  readonly objects: readonly ServerObject[];
  readonly count: number;
}

/** A single operation envelope sent to the server. */
export interface OperationEnvelope {
  readonly operation_id: string;
  readonly operation_type: string;
  readonly base_version: number;
  readonly payload?: Record<string, unknown>;
}

/** Server acknowledgement for one accepted operation. */
export interface OperationAck {
  readonly operation_id: string;
  readonly sequence: number;
  readonly version: number;
  readonly duplicate: boolean;
}

/** Response from POST /api/whiteboards/<id>/operations/ */
export interface SubmitResponse {
  readonly acks: readonly OperationAck[];
  readonly version: number;
  readonly applied: number;
}

/** Response from GET /api/whiteboards/<id>/operations/list/ */
export interface OperationsListResponse {
  readonly operations: readonly ServerOperation[];
  readonly version: number;
  readonly count: number;
}

/** A server-stored operation (for incremental sync). */
export interface ServerOperation {
  readonly operation_id: string;
  readonly sequence: number;
  readonly operation_type: string;
  readonly base_version: number;
  readonly resulting_version: number;
  readonly created_at: string;
  readonly actor_id: number;
  readonly payload: Record<string, unknown>;
}

/** Structured error from the API. */
export interface ApiError {
  readonly error: string;
  readonly message: string;
  readonly current_version?: number;
  readonly client_version?: number;
  readonly retry_after?: number;
}

function getCsrfToken(): string {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta instanceof HTMLMetaElement ? meta.content : "";
}

function jsonHeaders(): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-CSRFToken": getCsrfToken(),
  };
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let body: ApiError;
    try {
      body = (await response.json()) as ApiError;
    } catch {
      body = { error: "NETWORK_ERROR", message: `HTTP ${response.status}` };
    }
    const err = new Error(body.message || "Request failed") as Error & {
      apiError: ApiError;
      status: number;
    };
    err.apiError = body;
    err.status = response.status;
    throw err;
  }
  return response.json() as Promise<T>;
}

export class WhiteboardRepository {
  private readonly apiBase: string;

  constructor(apiBase: string) {
    this.apiBase = apiBase.replace(/\/+$/, "");
  }

  /** Load the current reconstructed whiteboard state. */
  async loadState(): Promise<LoadStateResponse> {
    const response = await fetch(`${this.apiBase}/`, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    return handleResponse<LoadStateResponse>(response);
  }

  /** Submit a single operation to the server. */
  async submitOperation(op: OperationEnvelope): Promise<SubmitResponse> {
    const response = await fetch(`${this.apiBase}/operations/`, {
      method: "POST",
      credentials: "same-origin",
      headers: jsonHeaders(),
      body: JSON.stringify(op),
    });
    return handleResponse<SubmitResponse>(response);
  }

  /** Submit a batch of operations in one request. */
  async submitBatch(ops: readonly OperationEnvelope[]): Promise<SubmitResponse> {
    const response = await fetch(`${this.apiBase}/operations/`, {
      method: "POST",
      credentials: "same-origin",
      headers: jsonHeaders(),
      body: JSON.stringify({ operations: [...ops] }),
    });
    return handleResponse<SubmitResponse>(response);
  }

  /** Fetch operations after a given sequence (for incremental sync). */
  async loadOperations(
    afterSequence: number = 0,
    limit: number = 100,
  ): Promise<OperationsListResponse> {
    const params = new URLSearchParams({
      after_sequence: String(afterSequence),
      limit: String(limit),
    });
    const response = await fetch(`${this.apiBase}/operations/list/?${params}`, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    return handleResponse<OperationsListResponse>(response);
  }

  /** Convert a ServerObject (from loadState) into a local Stroke. */
  static serverObjectToStroke(obj: ServerObject): Stroke {
    return {
      id: obj.object_id,
      points: obj.points.map((p) => ({ x: p.x, y: p.y })),
      style: { color: obj.color, width: obj.width, opacity: obj.opacity },
      creatorId: obj.creator_id,
    };
  }
}
