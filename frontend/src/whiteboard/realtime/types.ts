/**
 * Realtime collaboration protocol message types.
 *
 * Mirrors documentation/docs/architecture/websocket-protocol.md. These are plain
 * data types shared by the transport (parsing/serialization) and the controller
 * (dispatch/handling) so the wire contract lives in one place.
 *
 * Message types:
 *   client -> server: `sync.request`, `operation.submit`, `presence.cursor`,
 *                     `ping`
 *   server -> client: `connection.ready`, `connection.error`,
 *                     `operation.committed`, `operation.rejected`, `sync.ops`,
 *                     `sync.required`, `presence.joined`, `presence.left`,
 *                     `presence.update`, `pong`
 */

export type TransportState = "connecting" | "open" | "reconnecting" | "closed";

export type RealTimeConnectionState =
  | "connecting"
  | "connected"
  | "syncing"
  | "ready"
  | "reconnecting"
  | "closed";

// Error codes emitted by the server (see realtime.py on the backend).
export const Err = {
  AUTH_REQUIRED: "AUTH_REQUIRED",
  FORBIDDEN: "FORBIDDEN",
  WHITEBOARD_NOT_FOUND: "WHITEBOARD_NOT_FOUND",
  WHITEBOARD_ARCHIVED: "WHITEBOARD_ARCHIVED",
  INVALID_MESSAGE: "INVALID_MESSAGE",
  INVALID_OPERATION: "INVALID_OPERATION",
  STALE_VERSION: "STALE_VERSION",
  RATE_LIMITED: "RATE_LIMITED",
  PAYLOAD_TOO_LARGE: "PAYLOAD_TOO_LARGE",
  SYNC_REQUIRED: "SYNC_REQUIRED",
  SERVER_ERROR: "SERVER_ERROR",
} as const;

export type ErrCode = (typeof Err)[keyof typeof Err];

export interface ServerErrorBody {
  readonly type: "connection.error";
  readonly code: ErrCode;
  readonly message: string;
}

export interface OperationRejectedBody {
  readonly type: "operation.rejected";
  readonly reason: ErrCode;
  readonly message: string;
  readonly current_version?: number;
  readonly client_version?: number;
}

export interface ConnectionReady {
  readonly type: "connection.ready";
  readonly protocol_version: number;
  readonly connection_id: string;
  readonly version: number;
  readonly user: UserPublic;
  readonly partner: UserPublic | null;
}

export interface UserPublic {
  readonly public_id: string;
  readonly display_name: string;
}

export interface OperationCommitted {
  readonly type: "operation.committed";
  readonly operation_id: string;
  readonly sequence: number;
  readonly version: number;
  readonly duplicate: boolean;
  readonly actor: UserPublic;
  readonly operation: {
    readonly operation_type: string;
    readonly payload: Record<string, unknown>;
  };
  readonly client_id?: string;
}

export interface SyncOps {
  readonly type: "sync.ops";
  readonly version: number;
  readonly operations: readonly ServerOperation[];
}

export interface SyncRequired {
  readonly type: "sync.required";
}

export interface ServerOperation {
  readonly operation_id: string;
  readonly sequence: number;
  readonly operation_type: string;
  readonly base_version: number;
  readonly resulting_version: number;
  readonly payload: Record<string, unknown>;
}

export interface PresenceJoined {
  readonly type: "presence.joined";
  readonly user: UserPublic;
}

export interface PresenceLeft {
  readonly type: "presence.left";
  readonly user: UserPublic;
}

export interface PresenceUpdate {
  readonly type: "presence.update";
  readonly user: UserPublic;
  readonly cursor: { readonly x: number; readonly y: number };
}

export interface Pong {
  readonly type: "pong";
}

export type ServerMessage =
  | ServerErrorBody
  | OperationRejectedBody
  | ConnectionReady
  | OperationCommitted
  | SyncOps
  | SyncRequired
  | PresenceJoined
  | PresenceLeft
  | PresenceUpdate
  | Pong;

// Client -> server frames ---------------------------------------------------

export interface SyncRequest {
  readonly type: "sync.request";
  readonly version: number;
}

export interface OperationSubmit {
  readonly type: "operation.submit";
  readonly operation_id: string;
  readonly base_version: number;
  readonly operation: {
    readonly operation_type: string;
    readonly payload: Record<string, unknown>;
  };
  readonly client_id?: string;
}

export interface PresenceCursor {
  readonly type: "presence.cursor";
  readonly x: number;
  readonly y: number;
}

export interface Ping {
  readonly type: "ping";
}

export type ClientMessage = SyncRequest | OperationSubmit | PresenceCursor | Ping | ServerMessage;

/** An operation carried over the wire (submit + remote-apply + replay). */
export interface OperationEnvelopeForWire {
  readonly operation_id: string;
  readonly operation_type: string;
  readonly base_version: number;
  readonly payload?: Record<string, unknown>;
}