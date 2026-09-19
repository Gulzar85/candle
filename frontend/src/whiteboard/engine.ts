/**
 * The whiteboard engine. Orchestrates board state, viewport, renderer, and
 * input, implementing the input Host interface and exposing the command surface
 * the toolbar / keyboard / status bar drive.
 *
 * Lifecycle: construct with the stage+canvas, then `attach()`. Call `destroy()`
 * to tear down resize observation — the page may be swapped in / out by HTMX,
 * so the engine must clean up after itself.
 *
 * Phase 4 adds an optional persistence seam: the engine accepts an
 * `onServerOperation` callback that fires whenever a local operation is
 * committed. The caller (index.ts) wires this to the SyncManager which handles
 * queuing, submission, and retry. The engine itself never makes HTTP calls —
 * it remains DOM+state only.
 */

import {
  boundsOf,
  computeResizeTransform,
  handlePositions,
  nearestHandle,
  scalePointsFromAnchor,
  translatePoints,
  unionBounds,
  visibleHandles,
} from "./geometry";
import type { HandleId } from "./geometry";
import { clipToEraser, finalizePenStroke, hitsStroke } from "./tools";
import type { Host } from "./input";
import { InputController } from "./input";
import { Renderer } from "./renderer";
import * as boardState from "./state";
import { commit, createBoard } from "./state";
import type { Board } from "./state";
import type { BBox, Op, Point, Size, Stroke, Tool, Viewport } from "./types";
import { fitToShow, makeViewport, panBy, zoomAt, zoomPercent } from "./viewport";
import { normalizeOpacity, normalizeWidth, resolveColor } from "./color";
import type { PaletteColor } from "./color";
import type { OperationEnvelope } from "./repository";

export interface EngineOptions {
  readonly creatorId?: string;
  readonly initialColor?: PaletteColor;
  readonly initialWidth?: number;
  readonly initialOpacity?: number;
  /** Callback fired for each server-bound operation after local commit. */
  readonly onServerOperation?: (op: OperationEnvelope) => void;
  /**
   * Gate for the destructive `clear` command. Called before a non-empty board
   * is cleared, from both the toolbar button and the Ctrl/Cmd+Shift+X shortcut,
   * so the two can never drift out of consistency. Return false to abort.
   */
  readonly onConfirmClear?: () => boolean;
  /** Fired when the user presses `?` to request the keyboard-shortcut help. */
  readonly onHelp?: () => void;
}

export interface StatusInfo {
  readonly zoom: number;
  readonly strokeCount: number;
  readonly tool: Tool;
  readonly message?: string;
}

const ERASER_RADIUS = 12;
const SELECT_TOLERANCE = 12; // hit-test radius in world units for clicking a stroke
const HANDLE_TOLERANCE = 14; // hit-test radius in world units for grabbing a resize handle
const TRANSFORM_EPSILON = 1e-6; // below this, a move/resize gesture is a no-op (skip commit)

export class Engine implements Host {
  private readonly renderer: Renderer;
  private readonly input: InputController;
  private board: Board = createBoard();
  private _viewport: Viewport = makeViewport();
  private _size: Size = { w: 0, h: 0 };
  private activeTool: Tool = "pen";
  private color: PaletteColor;
  private width: number;
  private opacity: number;
  private spaceHeld = false;
  private nextId = 1;
  private panStart: Point | null = null;
  private liveStroke: { stroke: Stroke } | null = null;
  private liveEraserPath: Point[] = [];
  private resizeObserver: ResizeObserver | null = null;
  private onStatus: (info: StatusInfo) => void = () => {};
  private _serverVersion = 0;
  private selectedId: string | null = null;
  /** Live (not-yet-committed) move/resize preview: the selected object's id
   * plus its would-be new points, drawn via Renderer.renderWithOverride. */
  private liveTransform: { objectId: string; points: readonly Point[] } | null = null;
  private dragOriginalStroke: Stroke | null = null;
  private dragStartWorld: Point | null = null;
  private resizeOriginalStroke: Stroke | null = null;
  private resizeBBox: BBox | null = null;
  private resizeHandle: HandleId | null = null;

  constructor(
    private readonly stage: HTMLElement,
    canvas: HTMLCanvasElement,
    private readonly opts: EngineOptions = {},
  ) {
    this.renderer = new Renderer(canvas);
    this.input = new InputController(this);
    this.color = resolveColor(opts.initialColor ?? "#2563eb");
    this.width = normalizeWidth(opts.initialWidth ?? 3);
    this.opacity = normalizeOpacity(opts.initialOpacity ?? 1);
  }

  attach(): void {
    this.input.attach(this.stage);
    this.boundKeydown = (e) => this.handleGlobalKeydown(e);
    this.boundKeyup = (e) => {
      if (e.code === "Space") this.spaceHeld = false;
    };
    window.addEventListener("keydown", this.boundKeydown);
    window.addEventListener("keyup", this.boundKeyup);
    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(this.stage);
    this.handleResize();
  }

  destroy(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    if (this.boundKeydown) window.removeEventListener("keydown", this.boundKeydown);
    if (this.boundKeyup) window.removeEventListener("keyup", this.boundKeyup);
    this.input.detach();
  }

  private boundKeydown: ((e: KeyboardEvent) => void) | null = null;
  private boundKeyup: ((e: KeyboardEvent) => void) | null = null;

  setStatusHandler(fn: (info: StatusInfo) => void): void {
    this.onStatus = fn;
  }

  /** Update the server version baseline (called after server ack). */
  setServerVersion(version: number): void {
    this._serverVersion = version;
  }

  get serverVersion(): number {
    return this._serverVersion;
  }

  /**
   * Load a whiteboard state from the server (reconstructed from operations).
   * Replaces the local board entirely and triggers a re-render.
   */
  loadServerState(strokes: readonly Stroke[]): void {
    const past: Op[] = strokes.length > 0 ? [{ type: "add", strokes }] : [];
    this.board = {
      strokes: [...strokes],
      past,
      future: [],
      version: this.board.version + 1,
    };
    this.render();
    this.emitStatus();
  }

  /**
   * Apply a *remote* operation (committed by a partner, or a replayed `sync.ops`
   * entry) to the local board.
   *
   * Unlike a local commit, this never re-emits the operation to the server (it
   * did not originate here). It is idempotent for well-formed operations: ops
   * whose subject no longer exists (e.g. a delete for an already-removed stroke)
   * are no-ops. Returns whether the board changed.
   */
  applyRemoteOperation(env: OperationEnvelope): boolean {
    const op = remoteEnvToOp(env, this.board);
    if (!op) return false;
    this.board = commit(this.board, op);
    this.dropStaleSelection();
    this.render();
    this.emitStatus();
    return true;
  }

  /** Clear the selection if the selected stroke no longer exists. */
  private dropStaleSelection(): void {
    if (this.selectedId && !this.board.strokes.some((s) => s.id === this.selectedId)) {
      this.selectedId = null;
    }
  }

  private emitStatus(): void {
    this.onStatus({
      zoom: zoomPercent(this._viewport),
      strokeCount: this.board.strokes.length,
      tool: this.activeTool,
    });
  }

  private uid(prefix: string): string {
    return `${prefix}-${this.nextId++}-${Date.now().toString(36)}`;
  }

  // ------------------------------------------------------------------ input
  panActive(): boolean {
    return this.spaceHeld;
  }

  onBeginPoint(world: Point): void {
    if (this.activeTool === "pen") {
      this.liveStroke = { stroke: this.strokeForDraw(world) };
    } else if (this.activeTool === "eraser") {
      this.liveEraserPath = [world];
      this.previewErase();
    }
  }

  onMovePoint(world: Point): void {
    if (this.activeTool === "pen") {
      if (this.liveStroke) {
        const st = this.liveStroke.stroke;
        this.liveStroke = { stroke: { ...st, points: [...st.points, world] } };
        this.render();
      }
    } else if (this.activeTool === "eraser") {
      this.liveEraserPath = [...this.liveEraserPath, world];
      this.previewErase();
    }
  }

  onEndPoint(world: Point): void {
    if (this.activeTool === "pen") {
      const raw = this.liveStroke ? [...this.liveStroke.stroke.points, world] : [world];
      this.liveStroke = null;
      const done = finalizePenStroke(this.uid("s"), raw, this.currentStyle());
      if (done) this.commit({ type: "add", strokes: [done.stroke] });
    } else if (this.activeTool === "eraser") {
      this.finishErase(this.liveEraserPath);
      this.liveEraserPath = [];
    }
    this.render();
    this.emitStatus();
  }

  onCancelPoint(): void {
    this.liveStroke = null;
    this.liveEraserPath = [];
    this.render();
    this.emitStatus();
  }

  onBeginPan(screen: Point): void {
    this.panStart = screen;
  }

  onMovePan(screen: Point): void {
    if (!this.panStart) return;
    const dx = screen.x - this.panStart.x;
    const dy = screen.y - this.panStart.y;
    this.panStart = screen;
    this._viewport = panBy(this._viewport, dx, dy);
    this.render();
    this.emitStatus();
  }

  onEndPan(): void {
    this.panStart = null;
  }

  /** A click with the select tool: hit-test the stroke under the pointer. */
  onSelect(world: Point): void {
    if (this.activeTool !== "select") return;
    // Hit-test in reverse draw order so the stroke drawn on top wins.
    const stroke = this.hitTest(world);
    this.selectedId = stroke ? stroke.id : null;
    this.render();
    this.emitStatus();
  }

  private hitTest(world: Point): Stroke | null {
    const strokes = this.board.strokes;
    for (let i = strokes.length - 1; i >= 0; i--) {
      if (hitsStroke(world, strokes[i], SELECT_TOLERANCE)) {
        // The eraser clip uses the same radius; fine to reuse for selection.
        return strokes[i];
      }
    }
    return null;
  }

  private selectedStroke(): Stroke | null {
    if (!this.selectedId) return null;
    return this.board.strokes.find((s) => s.id === this.selectedId) ?? null;
  }

  // -------------------------------------------------------- move / resize
  hitTestHandle(world: Point): HandleId | null {
    const stroke = this.selectedStroke();
    if (!stroke) return null;
    const box = boundsOf(stroke.points);
    if (!box) return null;
    const visible = visibleHandles(box);
    if (visible.length === 0) return null;
    return nearestHandle(world, handlePositions(box), visible, HANDLE_TOLERANCE);
  }

  hitTestSelectedBody(world: Point): boolean {
    const stroke = this.selectedStroke();
    return stroke ? hitsStroke(world, stroke, SELECT_TOLERANCE) : false;
  }

  onBeginResize(handle: HandleId, _world: Point): void {
    const stroke = this.selectedStroke();
    const box = stroke ? boundsOf(stroke.points) : null;
    if (!stroke || !box) return;
    this.resizeOriginalStroke = stroke;
    this.resizeBBox = box;
    this.resizeHandle = handle;
  }

  onResizeMove(world: Point): void {
    if (!this.resizeOriginalStroke || !this.resizeBBox || !this.resizeHandle) return;
    const { anchor, scaleX, scaleY } = computeResizeTransform(
      this.resizeBBox,
      this.resizeHandle,
      world,
    );
    this.liveTransform = {
      objectId: this.resizeOriginalStroke.id,
      points: scalePointsFromAnchor(this.resizeOriginalStroke.points, anchor, scaleX, scaleY),
    };
    this.render();
  }

  onEndResize(world: Point): void {
    const from = this.resizeOriginalStroke;
    const box = this.resizeBBox;
    const handle = this.resizeHandle;
    this.resizeOriginalStroke = null;
    this.resizeBBox = null;
    this.resizeHandle = null;
    this.liveTransform = null;
    if (!from || !box || !handle) {
      this.render();
      return;
    }
    const { anchor, scaleX, scaleY } = computeResizeTransform(box, handle, world);
    if (Math.abs(scaleX - 1) < TRANSFORM_EPSILON && Math.abs(scaleY - 1) < TRANSFORM_EPSILON) {
      // A press+release directly on a handle with no real drag -- no-op.
      this.render();
      this.emitStatus();
      return;
    }
    const to: Stroke = { ...from, points: scalePointsFromAnchor(from.points, anchor, scaleX, scaleY) };
    this.commit({ type: "resize", from, to, anchor, scaleX, scaleY });
    this.render();
    this.emitStatus();
  }

  onCancelResize(): void {
    this.resizeOriginalStroke = null;
    this.resizeBBox = null;
    this.resizeHandle = null;
    this.liveTransform = null;
    this.render();
  }

  onBeginObjectDrag(world: Point): void {
    const stroke = this.selectedStroke();
    if (!stroke) return;
    this.dragOriginalStroke = stroke;
    this.dragStartWorld = world;
  }

  onObjectDragMove(world: Point): void {
    if (!this.dragOriginalStroke || !this.dragStartWorld) return;
    const dx = world.x - this.dragStartWorld.x;
    const dy = world.y - this.dragStartWorld.y;
    this.liveTransform = {
      objectId: this.dragOriginalStroke.id,
      points: translatePoints(this.dragOriginalStroke.points, dx, dy),
    };
    this.render();
  }

  onEndObjectDrag(world: Point): void {
    const from = this.dragOriginalStroke;
    const start = this.dragStartWorld;
    this.dragOriginalStroke = null;
    this.dragStartWorld = null;
    this.liveTransform = null;
    if (!from || !start) {
      this.render();
      return;
    }
    const dx = world.x - start.x;
    const dy = world.y - start.y;
    if (Math.abs(dx) < TRANSFORM_EPSILON && Math.abs(dy) < TRANSFORM_EPSILON) {
      // A press+release on the selected object's body with no real drag.
      this.render();
      this.emitStatus();
      return;
    }
    const to: Stroke = { ...from, points: translatePoints(from.points, dx, dy) };
    this.commit({ type: "move", from, to });
    this.render();
    this.emitStatus();
  }

  onCancelObjectDrag(): void {
    this.dragOriginalStroke = null;
    this.dragStartWorld = null;
    this.liveTransform = null;
    this.render();
  }

  /** Delete the currently selected stroke as a `delete_object` operation. */
  deleteSelection(): boolean {
    if (!this.selectedId) return false;
    const target = this.selectedId;
    const victims = this.board.strokes.filter((s) => s.id === target);
    if (victims.length === 0) {
      this.selectedId = null;
      this.emitStatus();
      return false;
    }
    this.selectedId = null;
    this.commit({ type: "remove", strokes: victims });
    this.render();
    this.emitStatus();
    return true;
  }

  onZoom(factor: number, anchor: Point): void {
    this._viewport = zoomAt(this._viewport, this._size, anchor, factor);
    this.render();
    this.emitStatus();
  }

  onPanBy(dx: number, dy: number): void {
    this._viewport = panBy(this._viewport, dx, dy);
    this.render();
    this.emitStatus();
  }

  // ------------------------------------------------------------- gestures
  private strokeForDraw(point: Point): Stroke {
    return { id: this.uid("live"), points: [point], style: this.currentStyle(), creatorId: this.opts.creatorId };
  }

  private currentStyle(): Stroke["style"] {
    return { color: this.color, width: this.width, opacity: this.opacity };
  }

  private previewErase(): void {
    const path = this.liveEraserPath;
    this.renderer.render(this.board, this._viewport, this._size, this.previewStrokes(path));
  }

  private previewStrokes(path: Point[]): Stroke[] {
    // Renders the live eraser by clipping committed strokes in-memory (pure).
    const live: Stroke[] = [];
    for (const stroke of this.board.strokes) {
      if (hitsStroke(path[path.length - 1], stroke, ERASER_RADIUS)) {
        const segments = clipToEraser(stroke.points, path, ERASER_RADIUS);
        for (const seg of segments) {
          live.push({ ...stroke, id: `${stroke.id}#${live.length}`, points: seg });
        }
      } else {
        live.push(stroke);
      }
    }
    return live;
  }

  private finishErase(path: Point[]): void {
    if (path.length === 0) return;
    const removed: Stroke[] = [];
    const added: Stroke[] = [];
    for (const stroke of this.board.strokes) {
      if (!hitsStroke(path[path.length - 1], stroke, ERASER_RADIUS) && !sweepsStroke(stroke, path)) {
        continue;
      }
      const segments = clipToEraser(stroke.points, path, ERASER_RADIUS);
      removed.push(stroke);
      for (const seg of segments) {
        const kept = Array.from({ length: seg.length }, (_, i) => seg[i]);
        added.push({ ...stroke, id: `${stroke.id}#${this.nextId++}`, points: kept });
      }
    }
    if (removed.length > 0) {
      this.commit({ type: "remove", strokes: removed });
      if (added.length > 0) this.commit({ type: "add", strokes: added });
    }
  }

  // --------------------------------------------------------------- commands
  setTool(tool: Tool): void {
    this.activeTool = tool;
    this.onCancelPoint();
    this.emitStatus();
  }

  setColor(color: string): void {
    this.color = resolveColor(color);
  }

  setWidth(width: number): void {
    this.width = normalizeWidth(width);
  }

  setOpacity(opacity: number): void {
    this.opacity = normalizeOpacity(opacity);
  }

  undo(): void {
    const next = boardState.undo(this.board);
    if (next.strokes === this.board.strokes) return;
    this.board = next;
    this.render();
    this.emitStatus();
  }

  redo(): void {
    const next = boardState.redo(this.board);
    if (next.strokes === this.board.strokes) return;
    this.board = next;
    this.render();
    this.emitStatus();
  }

  clear(): void {
    if (this.board.strokes.length === 0) return;
    if (this.opts.onConfirmClear && !this.opts.onConfirmClear()) return;
    this.commit({ type: "clear", strokes: this.board.strokes });
    this.render();
    this.emitStatus();
  }

  zoomBy(factor: number): void {
    this._viewport = zoomAt(this._viewport, this._size, { x: this._size.w / 2, y: this._size.h / 2 }, factor);
    this.render();
    this.emitStatus();
  }

  resetZoom(): void {
    this._viewport = makeViewport(this._viewport.cx, this._viewport.cy, 1);
    this.render();
    this.emitStatus();
  }

  fit(): void {
    const boxes: BBox[] = [];
    for (const s of this.board.strokes) {
      const b = boundsOf(s.points);
      if (b) boxes.push(b);
    }
    const bbox = unionBounds(boxes);
    const fit = fitToShow(bbox ?? { minX: -120, minY: -90, maxX: 120, maxY: 90 }, this._size, 48);
    if (fit) {
      this._viewport = fit;
      this.render();
      this.emitStatus();
    }
  }

  /** Host accessors: current viewport / canvas size / tool. */
  get viewport(): Viewport {
    return this._viewport;
  }

  get size(): Size {
    return this._size;
  }

  get tool(): Tool {
    return this.activeTool;
  }

  currentTool(): Tool {
    return this.activeTool;
  }

  get selected(): string | null {
    return this.selectedId;
  }

  // ------------------------------------------------------------------- ops
  private commit(op: Op): void {
    this.board = commit(this.board, op);
    this._emitServerOps(op);
    this.emitStatus();
  }

  /** Map a local Op to server operation envelopes and emit them. */
  private _emitServerOps(op: Op): void {
    const cb = this.opts.onServerOperation;
    if (!cb) return;

    // One local gesture can expand into several server operations (an eraser
    // stroke -> a `remove` plus N `add`s; a clear; a multi-stroke add). Each
    // must be submitted against the resulting version of the previous one, so
    // we emit a version *chain*: base = current, then advance for the next op.
    const envelopes: OperationEnvelope[] = [];

    switch (op.type) {
      case "add":
        for (const stroke of op.strokes) {
          envelopes.push({
            operation_id: crypto.randomUUID(),
            operation_type: "create_stroke",
            base_version: this._serverVersion + envelopes.length,
            payload: {
              object_id: stroke.id,
              points: stroke.points.map((p) => ({ x: p.x, y: p.y })),
              color: stroke.style.color,
              width: stroke.style.width,
              opacity: stroke.style.opacity,
              creator_id: stroke.creatorId,
            },
          });
        }
        break;
      case "remove":
        for (const stroke of op.strokes) {
          envelopes.push({
            operation_id: crypto.randomUUID(),
            operation_type: "delete_object",
            base_version: this._serverVersion + envelopes.length,
            payload: { object_id: stroke.id },
          });
        }
        break;
      case "clear":
        envelopes.push({
          operation_id: crypto.randomUUID(),
          operation_type: "clear_canvas",
          base_version: this._serverVersion,
          payload: {},
        });
        break;
      case "move":
        envelopes.push({
          operation_id: crypto.randomUUID(),
          operation_type: "move_object",
          base_version: this._serverVersion,
          payload: {
            object_id: op.to.id,
            // Translation is uniform across every point, so the delta
            // between corresponding points anywhere on the stroke is exact.
            dx: op.to.points[0].x - op.from.points[0].x,
            dy: op.to.points[0].y - op.from.points[0].y,
          },
        });
        break;
      case "resize":
        envelopes.push({
          operation_id: crypto.randomUUID(),
          operation_type: "resize_object",
          base_version: this._serverVersion,
          payload: {
            object_id: op.to.id,
            anchor: { x: op.anchor.x, y: op.anchor.y },
            scale_x: op.scaleX,
            scale_y: op.scaleY,
          },
        });
        break;
    }

    for (const envelope of envelopes) {
      cb(envelope);
    }
    // Advance the local high-water mark so the next gesture chains correctly.
    this._serverVersion += envelopes.length;
  }

  private render(): void {
    if (this.liveTransform) {
      this.renderer.renderWithOverride(
        this.board,
        this._viewport,
        this._size,
        this.liveTransform.objectId,
        this.liveTransform.points,
        this.selectedId,
      );
      return;
    }
    const live = this.liveStroke ? [this.liveStroke.stroke] : [];
    this.renderer.render(this.board, this._viewport, this._size, live, this.selectedId);
  }

  private handleResize(): void {
    const rect = this.stage.getBoundingClientRect();
    this._size = { w: rect.width, h: rect.height };
    this.renderer.resize(this._size);
    this.render();
  }

  private handleGlobalKeydown(e: KeyboardEvent): void {
    if (e.code === "Space" && !(e.target instanceof HTMLInputElement)) {
      this.spaceHeld = true;
      e.preventDefault();
      return;
    }
    if (this.isEditableTarget(e.target)) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "z") {
      e.preventDefault();
      e.shiftKey ? this.redo() : this.undo();
    } else if (mod && e.key.toLowerCase() === "y") {
      e.preventDefault();
      this.redo();
    } else if (mod && e.shiftKey && e.key.toLowerCase() === "x") {
      e.preventDefault();
      this.clear();
    } else if (!mod && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      this.deleteSelection();
    } else if (!mod && e.key === "Escape") {
      if (this.selectedId) {
        this.selectedId = null;
        this.render();
        this.emitStatus();
      }
    } else if (!mod && e.key.toLowerCase() === "p") {
      this.setTool("pen");
    } else if (!mod && e.key.toLowerCase() === "e") {
      this.setTool("eraser");
    } else if (!mod && e.key.toLowerCase() === "v") {
      this.setTool("select");
    } else if (!mod && e.key === "?") {
      e.preventDefault();
      this.opts.onHelp?.();
    }
  }

  private isEditableTarget(target: EventTarget | null): boolean {
    if (target instanceof HTMLElement) {
      const tag = target.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
    }
    return false;
  }
}

/** Whether any part of an eraser path comes near any segment of the stroke. */
function sweepsStroke(stroke: Stroke, path: Point[]): boolean {
  return path.some((p) => hitsStroke(p, stroke, ERASER_RADIUS));
}

/**
 * Map a server operation envelope onto a local reversible Op, or null if it
 * cannot be mapped onto the current board (idempotent no-op).
 *
 * - create_stroke -> add the stroke
 * - delete_object -> remove the stroke(s) whose id equals `object_id`
 * - clear_canvas  -> clear whatever is currently on the board
 */
function remoteEnvToOp(env: OperationEnvelope, board: Board): Op | null {
  const payload = env.payload ?? {};
  switch (env.operation_type) {
    case "create_stroke": {
      const id = payload.object_id;
      const points = payload.points;
      if (typeof id !== "string" || !Array.isArray(points) || points.length === 0) return null;
      const coords = points
        .map((p) => ({ x: Number((p as { x?: unknown }).x), y: Number((p as { y?: unknown }).y) }))
        .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
      if (coords.length === 0) return null;
      return {
        type: "add",
        strokes: [
          {
            id,
            points: coords,
            style: {
              color: normalizeColor(payload.color),
              width: normalizeWidth(Number(payload.width)),
              opacity: normalizeOpacity(Number(payload.opacity)),
            },
            creatorId: typeof payload.creator_id === "string" ? payload.creator_id : undefined,
          },
        ],
      };
    }
    case "delete_object": {
      const id = payload.object_id;
      if (typeof id !== "string") return null;
      const victims = board.strokes.filter((s) => s.id === id);
      if (victims.length === 0) return null;
      return { type: "remove", strokes: victims };
    }
    case "clear_canvas": {
      if (board.strokes.length === 0) return null;
      return { type: "clear", strokes: [...board.strokes] };
    }
    case "move_object": {
      const id = payload.object_id;
      const dx = Number(payload.dx);
      const dy = Number(payload.dy);
      if (typeof id !== "string" || !Number.isFinite(dx) || !Number.isFinite(dy)) return null;
      const target = board.strokes.find((s) => s.id === id);
      if (!target) return null; // already gone locally -- matches delete_object's no-op convention
      return {
        type: "move",
        from: target,
        to: { ...target, points: target.points.map((p) => ({ x: p.x + dx, y: p.y + dy })) },
      };
    }
    case "resize_object": {
      const id = payload.object_id;
      const anchorRaw = payload.anchor as { x?: unknown; y?: unknown } | undefined;
      const ax = Number(anchorRaw?.x);
      const ay = Number(anchorRaw?.y);
      const sx = Number(payload.scale_x);
      const sy = Number(payload.scale_y);
      if (
        typeof id !== "string" ||
        !Number.isFinite(ax) ||
        !Number.isFinite(ay) ||
        !Number.isFinite(sx) ||
        !Number.isFinite(sy)
      ) {
        return null;
      }
      const target = board.strokes.find((s) => s.id === id);
      if (!target) return null;
      return {
        type: "resize",
        from: target,
        to: {
          ...target,
          points: target.points.map((p) => ({ x: ax + (p.x - ax) * sx, y: ay + (p.y - ay) * sy })),
        },
        anchor: { x: ax, y: ay },
        scaleX: sx,
        scaleY: sy,
      };
    }
    // "restore_version" is deliberately not handled here — every caller must
    // recognize it before reaching applyRemoteOperation and trigger a full
    // state refetch instead (the payload it carries is intentionally
    // incomplete over the wire; see docs/architecture/whiteboard-history.md).
    default:
      return null;
  }
}

function normalizeColor(value: unknown): string {
  if (typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value)) return value;
  return "#2563eb";
}
