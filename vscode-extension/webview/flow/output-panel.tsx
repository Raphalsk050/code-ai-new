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

/**
 * What a node said and did, inside the node itself: thinking, answer, the tools
 * it called. Scrolls on its own; while it is being written it follows the end,
 * until the user scrolls up to read something earlier.
 */
export function OutputPanel({ nodeId, parts, live, onClose }: OutputPanelProps): JSX.Element {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const stuck = React.useRef(true);

  React.useLayoutEffect(() => {
    const element = ref.current;
    if (element && stuck.current) element.scrollTop = element.scrollHeight;
  }, [parts]);

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
        {parts.length === 0 && <div className="flow-output-part info">{t("Working…")}</div>}
        {parts.map((part, index) => (
          <div key={index} className={`flow-output-part ${part.kind}`}>
            {part.kind === "thinking" && <span className="flow-output-label">{t("Thinking")}</span>}
            {part.kind === "tool" ? `› ${part.text}` : part.text}
          </div>
        ))}
        {live && <span className="flow-output-caret" />}
      </div>
    </div>
  );
}
