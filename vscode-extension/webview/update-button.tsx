import * as React from "react";

import type { UpdateState } from "../src/protocol";
import { t, tf } from "./i18n";
import { IconRefresh, IconUser } from "./icons";
import { ripple } from "./motion";

export interface UpdateView {
  version: string;
  current: string;
  notes: string;
  state: UpdateState;
  message: string;
  /** The releases host, while it wants a login. */
  host?: string;
}

/**
 * Shown once the backend finds a release newer than this build. One click
 * downloads the package for this platform, installs it, and offers a reload.
 * When the releases need a login first, it offers that instead.
 */
export function UpdateButton(props: {
  update: UpdateView;
  onInstall: () => void;
  onReload: () => void;
  onSignIn: () => void;
}): JSX.Element {
  const { update } = props;
  const busy =
    update.state === "downloading" || update.state === "installing" || update.state === "signingIn";
  const signIn = update.state === "login" || update.state === "signingIn";
  const label =
    update.state === "downloading"
      ? t("Downloading…")
      : update.state === "installing"
        ? t("Installing…")
        : update.state === "installed"
          ? t("Reload to finish")
          : update.state === "failed"
            ? t("Update failed · retry")
            : update.state === "login"
              ? t("Sign in to update")
              : update.state === "signingIn"
                ? t("Signing in…")
                : tf("Update {0}", `v${update.version}`);
  const title =
    update.state === "failed"
      ? update.message
      : update.state === "signingIn"
        ? t("Finish signing in in the window that opened.")
        : update.state === "login"
          ? [update.message, tf("Code-AI updates come from {0}. Sign in once, in the same window git uses.", update.host ?? "GitHub")]
              .filter(Boolean)
              .join("\n\n")
          : [tf("Code-AI v{0} is out (this is v{1}).", update.version, update.current), firstLines(update.notes)]
              .filter(Boolean)
              .join("\n\n");
  const onClick =
    update.state === "installed" ? props.onReload : update.state === "login" ? props.onSignIn : props.onInstall;
  return (
    <button
      className={`update-btn update-${update.state}`}
      title={title}
      disabled={busy}
      onPointerDown={ripple}
      onClick={onClick}
    >
      {busy ? <span className="spinner" /> : signIn ? <IconUser size={12} /> : <IconRefresh size={12} />}
      <span>{label}</span>
    </button>
  );
}

/** The top of the release notes, enough for a tooltip. */
function firstLines(notes: string): string {
  return notes.split("\n").slice(0, 12).join("\n").trim();
}
