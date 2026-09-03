/**
 * Pointer input. DOM dependent (verified by build + smoke test).
 *
 * A thin adapter between raw Pointer / Wheel events and the engine. It owns the
 * gesture state machine (what the currently pressed pointer is doing) and hands
 * the engine high-level "commands" via the `Host` interface. Keeping the gesture
 * rules here keeps the engine focused on board/viewport semantics.
 *
 * Coordinate note: all screen positions passed to the host are measured in CSS
 * pixels relative to the top-left of the stage, matching the canvas `size`.
 */

import type { Point, Size, Tool, Viewport } from "./types";
import { panBy, screenToWorld, zoomAt } from "./viewport";

export interface Host {
  readonly tool: Tool;
  readonly viewport: Viewport;
  readonly size: Size;

  /** The current active pointer state for panning (space/ctrl held). */
  panActive(): boolean;

  onBeginPoint(world: Point): void;
  onMovePoint(world: Point): void;
  onEndPoint(world: Point): void;
  onCancelPoint(): void;
  onBeginPan(screen: Point): void;
  onMovePan(screen: Point): void;
  onEndPan(): void;
  onZoom(factor: number, anchor: Point): void;
  onPanBy(dx: number, dy: number): void;
  /** A click with the select tool (no drag) — used for hit-test selection. */
  onSelect(world: Point): void;
}

const MIDDLE_BUTTON = 1;
// Pointer travel (CSS px) before a select-tool press becomes a pan/drag. Below
// this threshold a press+release counts as a click (select).
const SELECT_CLICK_TOLERANCE = 6;

export class InputController {
  private activePointer = 0;
  private mode: "none" | "draw" | "pan" | "click" | "pinch" = "none";
  private lastScreen: Point | null = null;
  /** Live touch pointers, keyed by pointerId — tracked to detect a second
   * finger for pinch-zoom / two-finger pan, independent of `activePointer`. */
  private readonly touches = new Map<number, Point>();
  private pinchPrevDist = 0;
  private pinchPrevCenter: Point | null = null;

  constructor(private readonly host: Host) {}

  attach(stage: HTMLElement): void {
    stage.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    stage.addEventListener("pointermove", (e) => this.onPointerMove(e));
    stage.addEventListener("pointerup", (e) => this.onPointerUp(e));
    stage.addEventListener("pointercancel", (e) => this.onPointerCancel(e));
    stage.addEventListener("pointerleave", (e) => this.onPointerLeave(e));
    stage.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    stage.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  private screenOf(e: { clientX: number; clientY: number; currentTarget: EventTarget | null }): Point {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  private worldOf(screen: Point): Point {
    return screenToWorld(screen, this.host.size, this.host.viewport);
  }

  private wantsPan(e: PointerEvent): boolean {
    if (e.button === MIDDLE_BUTTON) return true;
    if (this.host.panActive()) return true;
    // Ctrl+drag pans from any tool; the bare select tool pans only on drag.
    if (e.ctrlKey || e.metaKey) return true;
    return false;
  }

  private onPointerDown(e: PointerEvent): void {
    if (e.pointerType === "touch") {
      this.touches.set(e.pointerId, this.screenOf(e));
      if (this.touches.size === 2) {
        this.beginPinch(e);
        return;
      }
      if (this.touches.size > 2) return; // a third finger is ignored
    }
    if (this.mode === "pinch") return; // mid-pinch; ignore stray pointer events
    if (this.activePointer !== 0 && e.pointerId !== this.activePointer) return;
    const screen = this.screenOf(e);
    const world = this.worldOf(screen);

    if (this.wantsPan(e)) {
      this.mode = "pan";
      this.host.onBeginPan(screen);
    } else if (this.host.tool === "select") {
      // Wait for movement before declaring a pan; a small press+release selects.
      this.mode = "click";
      this.lastScreen = screen;
    } else if (this.host.tool === "pen" || this.host.tool === "eraser") {
      this.mode = "draw";
      this.host.onBeginPoint(world);
    } else {
      return;
    }
    this.activePointer = e.pointerId;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  /** Enter two-finger pinch-zoom / pan, cancelling whatever single-pointer
   * gesture (draw/pan) was in progress under the first finger. */
  private beginPinch(e: PointerEvent): void {
    if (this.mode === "draw") this.host.onCancelPoint();
    else if (this.mode === "pan") this.host.onEndPan();
    this.activePointer = 0;
    this.lastScreen = null;
    this.mode = "pinch";
    const pts = Array.from(this.touches.values());
    this.pinchPrevDist = distance(pts[0], pts[1]);
    this.pinchPrevCenter = midpoint(pts[0], pts[1]);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  private onPointerMove(e: PointerEvent): void {
    if (e.pointerType === "touch" && this.touches.has(e.pointerId)) {
      this.touches.set(e.pointerId, this.screenOf(e));
    }
    if (this.mode === "pinch") {
      if (this.touches.size < 2) return;
      const pts = Array.from(this.touches.values());
      const dist = distance(pts[0], pts[1]);
      const center = midpoint(pts[0], pts[1]);
      if (this.pinchPrevDist > 0 && dist > 0) {
        const factor = dist / this.pinchPrevDist;
        if (Number.isFinite(factor) && factor > 0) this.host.onZoom(factor, center);
      }
      if (this.pinchPrevCenter) {
        const dx = center.x - this.pinchPrevCenter.x;
        const dy = center.y - this.pinchPrevCenter.y;
        if (dx !== 0 || dy !== 0) this.host.onPanBy(dx, dy);
      }
      this.pinchPrevDist = dist;
      this.pinchPrevCenter = center;
      return;
    }
    if (e.pointerId !== this.activePointer) return;
    const screen = this.screenOf(e);
    if (this.mode === "pan") {
      this.host.onMovePan(screen);
    } else if (this.mode === "draw") {
      this.host.onMovePoint(this.worldOf(screen));
    } else if (this.mode === "click" && this.lastScreen) {
      const dx = screen.x - this.lastScreen.x;
      const dy = screen.y - this.lastScreen.y;
      if (dx * dx + dy * dy > SELECT_CLICK_TOLERANCE * SELECT_CLICK_TOLERANCE) {
        // It became a drag -> pan.
        this.mode = "pan";
        this.host.onBeginPan(this.lastScreen);
        this.host.onMovePan(screen);
      }
    }
  }

  private onPointerUp(e: PointerEvent): void {
    if (e.pointerType === "touch") this.touches.delete(e.pointerId);
    if (this.mode === "pinch") {
      if (this.touches.size < 2) this.reset();
      return;
    }
    if (e.pointerId !== this.activePointer) return;
    const screen = this.screenOf(e);
    if (this.mode === "pan") {
      this.host.onEndPan();
    } else if (this.mode === "draw") {
      this.host.onEndPoint(this.worldOf(screen));
    } else if (this.mode === "click") {
      this.host.onSelect(this.worldOf(screen));
    }
    this.reset();
  }

  private onPointerCancel(e: PointerEvent): void {
    if (e.pointerType === "touch") this.touches.delete(e.pointerId);
    if (this.mode === "pinch") {
      if (this.touches.size < 2) this.reset();
      return;
    }
    if (e.pointerId !== this.activePointer) return;
    if (this.mode === "draw") this.host.onCancelPoint();
    if (this.mode === "pan") this.host.onEndPan();
    this.reset();
  }

  private onPointerLeave(e: PointerEvent): void {
    if (e.pointerId !== this.activePointer) return;
    // Pointer capture persists through leave, so drawing continues; only clear a
    // pan so we don't keep the live cursor pinned after the pointer exits.
    if (this.mode === "pan" || this.mode === "click") {
      this.host.onEndPan();
      this.mode = "none";
    }
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    const screen = this.screenOf(e);
    if (e.ctrlKey || e.metaKey) {
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      this.host.onZoom(factor, screen);
    } else {
      this.host.onPanBy(e.deltaX, e.deltaY);
    }
  }

  private reset(): void {
    this.activePointer = 0;
    this.mode = "none";
    this.lastScreen = null;
    this.pinchPrevDist = 0;
    this.pinchPrevCenter = null;
  }
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}
