import * as React from "react";

import { Codicon } from "./codicon";
import { t, tf } from "./i18n";

export type BannerState =
  | { kind: "offline" }
  | { kind: "connecting" }
  | { kind: "retry"; attempt: string; note: string };

/** What holds the next turn up, over the composer. Gone the moment it clears. */
export function Banner({ state, onReconnect }: { state: BannerState; onReconnect: () => void }): JSX.Element {
  if (state.kind === "retry") {
    return (
      <div className="banner banner-warn" role="status">
        <Codicon name="sync" size={14} spin />
        <span className="banner-text">
          {tf("The model call failed; retrying {0}", state.attempt)}
          {state.note && <span className="banner-note"> · {state.note}</span>}
        </span>
      </div>
    );
  }
  if (state.kind === "connecting") {
    return (
      <div className="banner banner-info" role="status">
        <span className="spinner" />
        <span className="banner-text">{t("Connecting to the backend…")}</span>
      </div>
    );
  }
  return (
    <div className="banner banner-danger" role="alert">
      <Codicon name="debug-disconnect" size={14} />
      <span className="banner-text">{t("The backend is offline; messages cannot be sent until it is back.")}</span>
      <button className="btn-ghost btn-sm" onClick={onReconnect}>
        {t("Reconnect")}
      </button>
    </div>
  );
}
