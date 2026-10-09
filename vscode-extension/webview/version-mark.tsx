import * as React from "react";

import { t, tf } from "./i18n";

/** "0.43.0-113", as the backend says it, in its two parts; null for anything else. */
export function versionParts(version: string | undefined): { release: string; build: string } | null {
  const found = /^(\d+\.\d+\.\d+)-(\d+)$/.exec(version ?? "");
  return found ? { release: found[1], build: found[2] } : null;
}

/** Which build this is, in small print: there to be found, not to be read. */
export function VersionMark({ version }: { version: string | undefined }): JSX.Element | null {
  const parts = versionParts(version);
  if (!parts) return null;
  return (
    <span className="version-mark" title={t("Code-AI version")}>
      {tf("v{0} · build {1}", parts.release, parts.build)}
    </span>
  );
}
