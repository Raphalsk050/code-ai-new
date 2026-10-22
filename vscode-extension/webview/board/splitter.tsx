import * as React from "react";

/** The panel sizes the user dragged to, kept with the tab's state. */
export interface PanelSizes {
  drawer: number;
  activity: number;
  lane: number;
}

export const DEFAULT_SIZES: PanelSizes = { drawer: 430, activity: 210, lane: 260 };

const LIMITS: Record<keyof PanelSizes, [number, () => number]> = {
  drawer: [300, () => window.innerWidth * 0.75],
  activity: [80, () => window.innerHeight * 0.7],
  lane: [200, () => 560],
};

export function clampSize(which: keyof PanelSizes, value: number): number {
  const [low, high] = LIMITS[which];
  return Math.round(Math.max(low, Math.min(high(), value)));
}

/**
 * A bar to drag a panel's size by: along x for widths, y for heights.
 * ``grow`` says which way enlarges it: 1 when dragging right or down does,
 * -1 when left or up does. A double-click puts the default size back.
 */
export function Splitter({
  axis,
  grow,
  size,
  label,
  onSize,
  onReset,
  className = "",
}: {
  axis: "x" | "y";
  grow: 1 | -1;
  size: number;
  label: string;
  onSize: (size: number, done: boolean) => void;
  onReset: () => void;
  className?: string;
}): JSX.Element {
  const [dragging, setDragging] = React.useState(false);
  return (
    <div
      className={`splitter splitter-${axis} ${dragging ? "dragging" : ""} ${className}`}
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-label={label}
      title={label}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const start = axis === "x" ? event.clientX : event.clientY;
        const from = size;
        setDragging(true);
        document.body.classList.add(axis === "x" ? "resizing-x" : "resizing-y");
        const move = (moved: PointerEvent) => {
          const now = axis === "x" ? moved.clientX : moved.clientY;
          onSize(from + grow * (now - start), false);
        };
        const up = (ended: PointerEvent) => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          document.body.classList.remove("resizing-x", "resizing-y");
          setDragging(false);
          const now = axis === "x" ? ended.clientX : ended.clientY;
          onSize(from + grow * (now - start), true);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      }}
      onDoubleClick={onReset}
      onClick={(event) => event.stopPropagation()}
    />
  );
}
