import * as React from "react";

import { t, tf } from "./i18n";
import { pulse } from "./motion";
import type { UsageState } from "./store";
import { formatTokens } from "./store/usage";

export type MeterLevel = "" | "warn" | "over" | "unknown";

export function meterLevel(usage: UsageState): MeterLevel {
  if (usage.used == null || !usage.budget) return "unknown";
  const ratio = usage.used / usage.budget;
  return ratio >= 1 ? "over" : ratio >= usage.threshold ? "warn" : "";
}

/**
 * Context budget, always in the header: the number that decides whether the
 * next turn survives. Unknown is drawn (a dashed track), never hidden. Past the
 * compaction threshold it turns into the button that compacts.
 */
export function ContextMeter({
  usage,
  onCompact,
  compact = false,
}: {
  usage: UsageState;
  onCompact: () => void;
  /** Narrow header: numbers only, no track. */
  compact?: boolean;
}): JSX.Element {
  const labelRef = React.useRef<HTMLSpanElement>(null);
  const previous = React.useRef<number | null>(null);
  React.useEffect(() => {
    if (usage.used != null && previous.current != null && usage.used !== previous.current) {
      pulse(labelRef.current);
    }
    previous.current = usage.used;
  }, [usage.used]);

  const level = meterLevel(usage);
  const ratio = usage.used != null && usage.budget ? Math.min(1, usage.used / usage.budget) : 0;
  const used = usage.used == null ? "-" : `${usage.estimated ? "~" : ""}${formatTokens(usage.used)}`;
  const budget = usage.budget ? formatTokens(usage.budget) : "";
  const title =
    usage.used == null
      ? t("Context: not measured yet")
      : tf("Context: {0} of {1} tokens · {2} spent in the session", used, budget || "?", formatTokens(usage.cumulative));
  const actionable = level === "warn" || level === "over";

  const body = (
    <>
      <span className="meter-used" ref={labelRef}>
        {used}
      </span>
      {!compact && (
        <span className="meter-track" aria-hidden="true">
          <span className="meter-fill" style={{ width: `${level === "unknown" ? 0 : Math.max(2, ratio * 100)}%` }} />
        </span>
      )}
      {budget && <span className="meter-budget">{budget}</span>}
    </>
  );

  return actionable ? (
    <button
      type="button"
      className={`context-meter ${level}`}
      title={`${title} · ${t("click to compact")}`}
      onClick={onCompact}
    >
      {body}
    </button>
  ) : (
    <span className={`context-meter ${level}`} title={title} role="meter" aria-valuenow={usage.used ?? undefined} aria-valuemax={usage.budget ?? undefined}>
      {body}
    </span>
  );
}
