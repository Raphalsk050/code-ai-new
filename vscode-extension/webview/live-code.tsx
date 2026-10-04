import * as React from "react";
import { t, tf } from "./i18n";

import type { CodeStreamState } from "./store";

/**
 * The file the model is writing right now, decoded from its streaming tool-call
 * arguments. It sits between the transcript and the composer - it belongs to
 * the turn in progress, not to the scrollback behind it - and shows the tail,
 * which is where the cursor is.
 */
export function LiveCodePanel({ code }: { code: CodeStreamState }): JSX.Element | null {
  const bodyRef = React.useRef<HTMLDivElement>(null);

  React.useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [code.code]);

  if (!code.visible) return null;
  const lines = code.code ? code.code.split("\n").length : code.lines;

  return (
    <div className="livecode">
      <div className="livecode-head">
        {!code.complete && <span className="spinner" />}
        <span className="livecode-path" title={code.path}>
          {code.path || code.tool}
        </span>
        <span className="rail-section-note">
          {code.complete ? t("written") : t("writing")}
          {lines ? ` · ${tf("{0} lines", lines)}` : ""}
        </span>
      </div>
      {code.reason && <div className="livecode-reason">{code.reason}</div>}
      <div className="livecode-body" ref={bodyRef}>
        {code.code || "…"}
      </div>
    </div>
  );
}
