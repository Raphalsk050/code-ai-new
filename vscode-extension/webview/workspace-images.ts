// Pictures in an answer or a document name files in the workspace: a path
// relative to it, or an absolute one inside it. The panel cannot load a path;
// its host says under which URL each workspace folder is served, and an image's
// path is turned into one of those. Anything outside the workspace stays a path
// that loads nothing.

import type { ResourceRoot } from "../src/protocol";

let roots: ResourceRoot[] = [];

export function setResourceRoots(next: ResourceRoot[]): void {
  roots = next;
}

const slashes = (path: string) => path.replace(/\\/g, "/");
const encode = (path: string) => path.split("/").map(encodeURIComponent).join("/");
/** A drive letter, or a share: Windows' own absolute paths. */
const WINDOWS_ABSOLUTE = /^([a-z]:\/|\/\/)/i;

/** The URL an image's `src` loads from: the same one unless it names a workspace file. */
export function resolveImage(src: string, served: readonly ResourceRoot[] = roots): string {
  if (!src || served.length === 0) return src;
  let path = src.trim();
  if (/^file:/i.test(path)) {
    path = path.replace(/^file:\/\/(localhost)?/i, "");
    // file:///C:/x arrives as /C:/x.
    if (/^\/[a-z]:\//i.test(path)) path = path.slice(1);
  } else if (/^[a-z][a-z0-9+.-]*:/i.test(path) && !WINDOWS_ABSOLUTE.test(slashes(path))) {
    return src; // https:, data: and the rest: already a URL.
  }
  try {
    path = decodeURI(path);
  } catch {
    // Not percent-encoded after all: taken as written.
  }
  path = slashes(path).split(/[?#]/)[0];
  const absolute = path.startsWith("/") || WINDOWS_ABSOLUTE.test(path);
  if (!absolute) {
    const relative = path.replace(/^(\.\/)+/, "");
    if (relative.split("/").includes("..")) return src;
    return `${served[0].base}/${encode(relative)}`;
  }
  for (const root of served) {
    const folder = slashes(root.path).replace(/\/+$/, "");
    const windows = WINDOWS_ABSOLUTE.test(folder);
    const inside = windows
      ? path.toLowerCase().startsWith(folder.toLowerCase() + "/")
      : path.startsWith(folder + "/");
    if (!inside) continue;
    const relative = path.slice(folder.length + 1);
    if (relative.split("/").includes("..")) return src;
    return `${root.base}/${encode(relative)}`;
  }
  return src;
}
