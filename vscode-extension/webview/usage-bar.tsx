import * as React from "react";

import { pulse } from "./motion";
import type { UsageState } from "./store";
import { formatTokens } from "./store/usage";

/**
 * Context budget, always on screen. The TUI kept this in the status line and it
 * is the one number that decides whether the next turn survives, so it gets a
 * permanent strip rather than a pill that scrolls away.
 */
export function UsageBar({
  usage,
  onCompact,
}: {
  usage: UsageState;
  onCompact: () => void;
}): JSX.Element | null {
  const labelRef = React.useRef<HTMLSpanElement>(null);
  const previous = React.useRef<number | null>(null);

  React.useEffect(() => {
    if (usage.used != null && previous.current != null && usage.used !== previous.current) {
      pulse(labelRef.current);
    }
    previous.current = usage.used;
  }, [usage.used]);

  if (usage.used == null) return null;
  const ratio = usage.budget ? Math.min(1, usage.used / usage.budget) : 0;
  const level = ratio >= 1 ? "over" : ratio >= usage.threshold ? "warn" : "";

  return (
    <div className="usagebar">
      <span ref={labelRef}>
        {usage.estimated ? "~" : ""}
        {formatTokens(usage.used)}
        {usage.budget ? ` / ${formatTokens(usage.budget)}` : " tok"}
      </span>
      <div className="track">
        <div className={`fill ${level}`} style={{ width: `${Math.max(2, ratio * 100)}%` }} />
      </div>
      <span>{usage.cumulative ? `${formatTokens(usage.cumulative)} acum.` : ""}</span>
      {level && (
        <button className="compact-btn" onClick={onCompact}>
          compactar
        </button>
      )}
    </div>
  );
}
