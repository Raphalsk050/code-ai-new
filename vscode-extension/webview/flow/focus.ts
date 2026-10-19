// The run, followed on the canvas: the node at work opens up so what it writes
// can be read, the nodes around it step aside, and once it is done it folds back
// before the next one opens. Pure geometry and timing, so it is tested without
// a DOM; canvas.tsx drives it once per animation frame.

import { Bounds, isAnnotation, NodeLayout } from "./model";
import type { NodeRunStatus } from "./store";

/** The width an opened node reads at, and the room its output panel takes. */
export const OPEN_W = 460;
export const PANEL_H = 260;
/** Extra air between an opened node and the ones it pushed. */
const GAP = 28;

export interface Spring {
  x: number;
  v: number;
}

export interface SpringTune {
  k: number;
  c: number;
}

/** Bouncy enough to read as a spring, damped enough to stop within half a second. */
export const OPEN_SPRING: SpringTune = { k: 210, c: 17 };
/** For a backlog: the same motion, twice as fast. */
export const QUICK_SPRING: SpringTune = { k: 520, c: 34 };
/** The camera never overshoots: a view that swings past the node is seasickness. */
export const CAMERA_SPRING: SpringTune = { k: 120, c: 21 };

/** One step of a damped spring towards `target`, in substeps so a slow frame stays stable. */
export function stepSpring(spring: Spring, target: number, dt: number, tune: SpringTune): Spring {
  let { x, v } = spring;
  const steps = Math.max(1, Math.ceil(dt / (1 / 240)));
  const h = Math.min(dt, 0.1) / steps;
  for (let index = 0; index < steps; index += 1) {
    v += (-tune.k * (x - target) - tune.c * v) * h;
    x += v * h;
  }
  return { x, v };
}

export function settled(spring: Spring, target: number): boolean {
  return Math.abs(spring.x - target) < 0.004 && Math.abs(spring.v) < 0.03;
}

/** Where a node's own body ends: its output panel opens below it. */
export function panelTop(layout: NodeLayout): number {
  return layout.body;
}

/** How much a node grows when it opens, in each direction. */
export function growthOf(layout: NodeLayout): { w: number; h: number } {
  return { w: Math.max(0, OPEN_W - layout.width), h: PANEL_H };
}

function box(layout: NodeLayout): Bounds {
  const [x, y] = layout.node.pos;
  return { minX: x, minY: y, maxX: x + layout.width, maxY: y + layout.height };
}

function moved(layout: NodeLayout, dx: number, dy: number, dw = 0, dh = 0): NodeLayout {
  if (dx === 0 && dy === 0 && dw === 0 && dh === 0) return layout;
  const width = layout.width + dw;
  return {
    ...layout,
    node: { ...layout.node, pos: [layout.node.pos[0] + dx, layout.node.pos[1] + dy] },
    width,
    height: layout.height + dh,
    outputs: dw ? layout.outputs.map((item) => ({ ...item, x: width })) : layout.outputs,
    // What feeds it from below stays on its bottom edge, under the panel.
    inputs: dh
      ? layout.inputs.map((item) => (item.side === "bottom" ? { ...item, y: item.y + dh } : item))
      : layout.inputs,
  };
}

/**
 * The graph as drawn with `focusId` opened by `p` (0 shut, 1 open; the spring
 * overshoots a little either side of that).
 *
 * The node grows about its own centre. A node beside it moves sideways only, one
 * above or below it moves up or down only, and one off a corner moves both
 * ways: every node keeps clear of the others, because two nodes only ever move
 * apart or together. A comment holding the opened node stretches with it; any
 * other comment moves as a node would.
 */
export function presented(
  layouts: Map<string, NodeLayout>,
  focusId: string | null,
  p: number
): Map<string, NodeLayout> {
  const focus = focusId ? layouts.get(focusId) : undefined;
  const amount = Math.max(0, p);
  if (!focus || amount <= 0) return layouts;
  const growth = growthOf(focus);
  const gw = growth.w * amount;
  const gh = growth.h * amount;
  const pushX = gw / 2 + GAP * Math.min(1, amount);
  const pushY = gh / 2 + GAP * Math.min(1, amount);
  const f = box(focus);
  const cx = (f.minX + f.maxX) / 2;
  const cy = (f.minY + f.maxY) / 2;

  const out = new Map<string, NodeLayout>();
  for (const [id, layout] of layouts) {
    if (id === focusId) {
      out.set(id, moved(layout, -gw / 2, -gh / 2, gw, gh));
      continue;
    }
    const b = box(layout);
    if (isAnnotation(layout.spec) && b.minX <= f.minX && b.maxX >= f.maxX && b.minY <= f.minY && b.maxY >= f.maxY) {
      out.set(id, moved(layout, -pushX, -pushY, pushX * 2, pushY * 2));
      continue;
    }
    const beside = b.minY < f.maxY && b.maxY > f.minY;
    const stacked = b.minX < f.maxX && b.maxX > f.minX;
    const dx = beside || !stacked ? ((b.minX + b.maxX) / 2 >= cx ? pushX : -pushX) : 0;
    const dy = stacked || !beside ? ((b.minY + b.maxY) / 2 >= cy ? pushY : -pushY) : 0;
    out.set(id, moved(layout, dx, dy));
  }
  return out;
}

export interface View {
  pan: [number, number];
  zoom: number;
}

/**
 * The view that puts an opened node in the middle at a zoom it reads at: the
 * current one when that is close enough, never so close the node overflows.
 */
export function focusView(layout: NodeLayout, viewport: { width: number; height: number }, current: number): View {
  const growth = growthOf(layout);
  const width = layout.width + growth.w;
  const height = layout.height + growth.h;
  const fit = Math.min((viewport.width * 0.86) / width, (viewport.height * 0.86) / height);
  const zoom = Math.max(0.4, Math.min(Math.max(current, 0.9), fit, 1.25));
  const cx = layout.node.pos[0] + layout.width / 2;
  const cy = layout.node.pos[1] + layout.height / 2;
  return { zoom, pan: [viewport.width / 2 - cx * zoom, viewport.height / 2 - cy * zoom] };
}

export function blendView(from: View, to: View, t: number): View {
  return {
    zoom: from.zoom + (to.zoom - from.zoom) * t,
    pan: [from.pan[0] + (to.pan[0] - from.pan[0]) * t, from.pan[1] + (to.pan[1] - from.pan[1]) * t],
  };
}

// -- the reel: which node is open, and when the next one may open ------------

export interface ReelInput {
  now: number;
  /** The setting: follow the run on the canvas. */
  enabled: boolean;
  running: boolean;
  /** Node ids in the order they started; `orderBase` is the absolute index of the first. */
  order: string[];
  orderBase: number;
  status(id: string): NodeRunStatus;
  /** Whether the node has something worth stopping to read. */
  readable(id: string): boolean;
  /** A node the user opened by hand, if any. */
  pinned: string | null;
}

export interface ReelStep {
  id: string;
  phase: "opening" | "open" | "closing";
  manual: boolean;
  openedAt: number;
  doneAt: number;
}

/** Nodes waiting their turn past this many are skipped, so the view keeps up with the run. */
export const MAX_BACKLOG = 6;
const DWELL_READABLE_MS = 1400;
const DWELL_PLAIN_MS = 550;
const DWELL_BUSY_MS = 250;

/**
 * Shows one node at a time, in the order the run reached them.
 *
 * A node opens, stays open while it works and a moment after, then folds back;
 * only once it has fully folded, and the nodes it pushed are back in place,
 * does the next one open. A run faster than that builds a queue, which plays
 * faster the longer it gets and drops the oldest steps past `MAX_BACKLOG`.
 */
export class FocusReel {
  step: ReelStep | null = null;
  /** Absolute index of the next node in the run's order to show. */
  next = 0;
  spring: Spring = { x: 0, v: 0 };

  /** A new run: its nodes are shown from the start, the one still open folds first. */
  restart(base: number): void {
    this.next = base;
    if (this.step) this.step.phase = "closing";
  }

  pending(input: ReelInput): number {
    return Math.max(0, input.orderBase + input.order.length - this.next);
  }

  /** Advance by `dt` seconds. Returns whether anything is still moving or waiting. */
  tick(input: ReelInput, dt: number): boolean {
    if (!this.step) this.pick(input);
    const step = this.step;
    if (!step) return false;
    const busy = this.pending(input) >= 2;
    const target = step.phase === "closing" ? 0 : 1;
    this.spring = stepSpring(this.spring, target, dt, busy ? QUICK_SPRING : OPEN_SPRING);
    // Folding back ends at rest, not overshooting into a node smaller than it is.
    if (step.phase === "closing" && this.spring.x < 0) this.spring = { x: 0, v: 0 };

    if (step.phase === "opening" && settled(this.spring, 1)) {
      step.phase = "open";
      step.openedAt = input.now;
    }
    if (step.phase === "open") {
      const over = step.manual
        ? input.pinned !== step.id
        : !input.enabled || !input.running || isFinished(input.status(step.id));
      // Latched: a loop running the node again later is the queue's next step,
      // not a reason to keep this one open.
      if (over || step.doneAt) {
        if (!step.doneAt) step.doneAt = input.now;
        const dwell =
          step.manual || !input.enabled
            ? 0
            : busy
              ? DWELL_BUSY_MS
              : input.readable(step.id)
                ? DWELL_READABLE_MS
                : DWELL_PLAIN_MS;
        if (input.now - Math.max(step.openedAt, step.doneAt) >= dwell) step.phase = "closing";
      }
    }
    if (step.phase === "closing" && settled(this.spring, 0)) {
      this.spring = { x: 0, v: 0 };
      this.step = null;
      this.pick(input);
    }
    return this.step !== null || this.pending(input) > 0;
  }

  private pick(input: ReelInput): void {
    const end = input.orderBase + input.order.length;
    if (input.enabled && this.next < end) {
      if (end - this.next > MAX_BACKLOG) this.next = end - MAX_BACKLOG;
      this.next = Math.max(this.next, input.orderBase);
      const id = input.order[this.next - input.orderBase];
      this.next += 1;
      this.step = { id, phase: "opening", manual: false, openedAt: 0, doneAt: 0 };
      return;
    }
    if (input.pinned) {
      this.step = { id: input.pinned, phase: "opening", manual: true, openedAt: 0, doneAt: 0 };
    }
  }
}

function isFinished(status: NodeRunStatus): boolean {
  return status === "done" || status === "failed";
}
