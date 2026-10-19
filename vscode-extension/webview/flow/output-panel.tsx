import * as React from "react";

import { t } from "../i18n";
import type { OutputPart } from "./store";

export interface OutputPanelProps {
  nodeId: string;
  parts: OutputPart[];
  /** Still being written: the panel keeps to the bottom while it grows. */
  live: boolean;
  /** Set when the user opened the panel by hand, and so closes it by hand. */
  onClose?: () => void;
}

/** The slowest a panel writes, in characters a second; a backlog is caught up faster. */
const TYPE_MIN = 90;
/** How much of a backlog goes out per second: a long answer arriving whole still streams, briefly. */
const TYPE_CATCH_UP = 2.5;

/**
 * The parts as far as they have been written out. Text always streams: what
 * a model sends token by token keeps its pace, and what arrives whole - a
 * summary, an answer typed by the user - is written out the same way rather
 * than dropped in. A panel the user opened by hand shows what is there at once.
 */
export function useTyped(parts: OutputPart[], fromStart: boolean): { parts: OutputPart[]; typing: boolean } {
  const total = parts.reduce((sum, part) => sum + part.text.length, 0);
  const shown = React.useRef(fromStart ? 0 : total);
  const [, redraw] = React.useState(0);
  // Older parts drop off a long run's panel: what is left is never unwritten.
  if (shown.current > total) shown.current = total;

  React.useEffect(() => {
    if (shown.current >= total) return;
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const rate = Math.max(TYPE_MIN, (total - shown.current) * TYPE_CATCH_UP);
      shown.current = Math.min(total, shown.current + Math.max(1, Math.round(rate * dt)));
      redraw((tick) => tick + 1);
      if (shown.current < total) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [total]);

  return { parts: cut(parts, shown.current), typing: shown.current < total };
}

function cut(parts: OutputPart[], length: number): OutputPart[] {
  const out: OutputPart[] = [];
  let left = length;
  for (const part of parts) {
    if (left <= 0) break;
    out.push(part.text.length <= left ? part : { ...part, text: part.text.slice(0, left) });
    left -= part.text.length;
  }
  return out;
}

/**
 * What a node said and did, inside the node itself: thinking, answer, the tools
 * it called. Scrolls on its own; while it is being written it follows the end,
 * until the user scrolls up to read something earlier.
 */
export function OutputPanel({ nodeId, parts: all, live, onClose }: OutputPanelProps): JSX.Element {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const stuck = React.useRef(true);
  // Opened by the run, it writes out from the start; by hand, it shows what is there.
  const { parts, typing } = useTyped(all, !onClose);
  const written = parts.reduce((sum, part) => sum + part.text.length, 0);

  React.useLayoutEffect(() => {
    const element = ref.current;
    if (element && stuck.current) element.scrollTop = element.scrollHeight;
  }, [written, parts.length]);

  return (
    <div
      className="flow-output"
      data-testid={`output-${nodeId}`}
      // The canvas zooms on the wheel and drags on a press: here both mean the text.
      onWheel={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.stopPropagation()}
    >
      {onClose && (
        <button
          className="flow-output-close"
          data-testid={`output-close-${nodeId}`}
          title={t("Hide the output")}
          onClick={onClose}
        >
          ▴
        </button>
      )}
      <div
        ref={ref}
        className="flow-output-scroll"
        onScroll={(event) => {
          const element = event.currentTarget;
          stuck.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
        }}
      >
        {all.length === 0 && <div className="flow-output-part info">{t("Working…")}</div>}
        {parts.map((part, index) => (
          <div key={index} className={`flow-output-part ${part.kind}`}>
            {part.kind === "thinking" && <span className="flow-output-label">{t("Thinking")}</span>}
            {part.kind === "tool" ? `› ${part.text}` : part.text}
          </div>
        ))}
        {(live || typing) && <span className="flow-output-caret" />}
      </div>
    </div>
  );
}
