import * as React from "react";

import {
  blendView,
  CAMERA_SPRING,
  focusView,
  FocusReel,
  ReelInput,
  settled,
  Spring,
  stepSpring,
  View,
} from "./focus";
import type { NodeLayout } from "./model";
import { Action, readable, RunState, worthOpening } from "./store";

export interface FollowRunArgs {
  run: RunState;
  /** The setting; a node opened by hand opens whatever it says. */
  enabled: boolean;
  layouts: Map<string, NodeLayout>;
  size: { width: number; height: number };
  view: View;
  viewEdits: number;
  pinned: string | null;
  dispatch(action: Action): void;
}

export interface FollowFrame {
  focusId: string | null;
  /** How open the focused node is: 0 shut, 1 open, a little past either while it springs. */
  p: number;
}

export interface Follow extends FollowFrame {
  /** The user moved the camera during a run, so it no longer follows. */
  detached: boolean;
  /** Take the camera back to where the run is, and keep it there. */
  resume(): void;
}

const SHUT: FollowFrame = { focusId: null, p: 0 };

/** Where the run is: the newest node still working, or the last one it reached. */
export function whereRunIs(run: RunState): string | null {
  for (let index = run.order.length - 1; index >= 0; index -= 1) {
    if (run.nodes[run.order[index]] === "running") return run.order[index];
  }
  return run.order[run.order.length - 1] ?? null;
}

interface CameraMove {
  from: View;
  to: View;
  spring: Spring;
}

/**
 * Drives the reel once per animation frame while there is anything to show,
 * and moves the camera to each node as it opens.
 *
 * The camera backs off for the rest of a run as soon as the user pans or zooms
 * themselves: following a run must never fight the hand on the mouse.
 */
export function useFollowRun(args: FollowRunArgs): Follow {
  const reel = React.useRef(new FocusReel()).current;
  const latest = React.useRef(args);
  latest.current = args;
  const [frame, setFrame] = React.useState<FollowFrame>(SHUT);
  const [detached, setDetached] = React.useState(false);
  const loop = React.useRef<{ handle: number; last: number } | null>(null);
  const camera = React.useRef<{ move: CameraMove | null; edits: number; paused: boolean; shown: string | null }>({
    move: null,
    edits: args.viewEdits,
    paused: false,
    shown: null,
  });

  const tick = React.useCallback((now: number) => {
    const state = loop.current;
    if (!state) return;
    const dt = state.last ? (now - state.last) / 1000 : 1 / 60;
    state.last = now;
    const current = latest.current;
    const { run } = current;
    const input: ReelInput = {
      now,
      enabled: current.enabled,
      running: run.running,
      order: run.order,
      orderBase: run.orderBase,
      status: (id) => run.nodes[id] ?? "idle",
      readable: (id) => readable(run, id),
      // A node inside a function not on the canvas has nothing to open there.
      opens: (id) => current.layouts.has(id) && worthOpening(run, current.layouts.get(id)!.node.kind, id),
      pinned: current.pinned,
    };
    const busy = reel.tick(input, dt);
    const step = reel.step;
    const cam = camera.current;

    // The user moved the camera: leave it where they put it.
    if (current.viewEdits !== cam.edits) {
      cam.edits = current.viewEdits;
      cam.move = null;
      if (run.running) {
        cam.paused = true;
        setDetached(true);
      }
    }
    if (step && step.id !== cam.shown && step.phase === "opening") {
      cam.shown = step.id;
      const layout = current.layouts.get(step.id);
      if (layout && (step.manual || !cam.paused)) {
        cam.move = {
          from: current.view,
          to: focusView(layout, current.size, current.view.zoom),
          spring: { x: 0, v: 0 },
        };
      }
    }
    if (!step) cam.shown = null;
    if (cam.move) {
      cam.move.spring = stepSpring(cam.move.spring, 1, dt, CAMERA_SPRING);
      const done = settled(cam.move.spring, 1);
      const view = blendView(cam.move.from, cam.move.to, done ? 1 : cam.move.spring.x);
      current.dispatch({ type: "setView", pan: view.pan, zoom: view.zoom, follow: true });
      if (done) cam.move = null;
    }

    const next: FollowFrame = step ? { focusId: step.id, p: reel.spring.x } : SHUT;
    setFrame((previous) =>
      previous.focusId === next.focusId && Math.abs(previous.p - next.p) < 1e-4 ? previous : next
    );
    if (busy || cam.move) {
      state.handle = requestAnimationFrame(tick);
    } else {
      loop.current = null;
    }
  }, [reel]);

  const kick = React.useCallback(() => {
    if (loop.current) return;
    loop.current = { handle: requestAnimationFrame(tick), last: 0 };
  }, [tick]);

  // A new run starts its queue from its own first node, and gets the camera back.
  React.useEffect(() => {
    if (!args.run.startedAt) return;
    reel.restart(args.run.orderBase);
    camera.current.paused = false;
    camera.current.edits = latest.current.viewEdits;
    setDetached(false);
    kick();
  }, [args.run.startedAt, reel, kick]);

  // A run that ended has nowhere left to follow.
  React.useEffect(() => {
    if (!args.run.running) setDetached(false);
  }, [args.run.running]);

  const resume = React.useCallback(() => {
    const current = latest.current;
    const cam = camera.current;
    cam.paused = false;
    cam.edits = current.viewEdits;
    setDetached(false);
    const id = reel.step?.id ?? whereRunIs(current.run);
    const layout = id ? current.layouts.get(id) : undefined;
    if (id && layout) {
      cam.shown = id;
      cam.move = { from: current.view, to: focusView(layout, current.size, current.view.zoom), spring: { x: 0, v: 0 } };
    }
    kick();
  }, [reel, kick]);

  React.useEffect(() => {
    kick();
  }, [args.run.order.length, args.run.orderBase, args.run.nodes, args.run.running, args.pinned, args.enabled, kick]);

  React.useEffect(
    () => () => {
      if (loop.current) cancelAnimationFrame(loop.current.handle);
      loop.current = null;
    },
    []
  );

  return { ...frame, detached, resume };
}
