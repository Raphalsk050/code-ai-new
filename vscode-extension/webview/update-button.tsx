import * as React from "react";

import type { UpdateState } from "../src/protocol";
import { t, tf } from "./i18n";
import { IconRefresh } from "./icons";
import { ripple } from "./motion";

export interface UpdateView {
  version: string;
  current: string;
  notes: string;
  state: UpdateState;
  message: string;
}

/**
 * Shown once the backend finds a release newer than this build. One click
 * downloads the package for this platform, installs it, and offers a reload.
 */
export function UpdateButton(props: {
  update: UpdateView;
  onInstall: () => void;
  onReload: () => void;
}): JSX.Element {
  const { update } = props;
  const busy = update.state === "downloading" || update.state === "installing";
  const label =
    update.state === "downloading"
      ? t("Downloading…")
      : update.state === "installing"
        ? t("Installing…")
        : update.state === "installed"
          ? t("Reload to finish")
          : update.state === "failed"
            ? t("Update failed · retry")
            : tf("Update {0}", `v${update.version}`);
  const title =
    update.state === "failed"
      ? update.message
      : [tf("Code-AI v{0} is out (this is v{1}).", update.version, update.current), firstLines(update.notes)]
          .filter(Boolean)
          .join("\n\n");
  return (
    <button
      className={`update-btn update-${update.state}`}
      title={title}
      disabled={busy}
      onPointerDown={ripple}
      onClick={update.state === "installed" ? props.onReload : props.onInstall}
    >
      {busy ? <span className="spinner" /> : <IconRefresh size={12} />}
      <span>{label}</span>
    </button>
  );
}

/** The top of the release notes, enough for a tooltip. */
function firstLines(notes: string): string {
  return notes.split("\n").slice(0, 12).join("\n").trim();
}
