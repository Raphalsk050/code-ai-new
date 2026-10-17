// The avatar loose on the desktop, outside the editor and the terminal.
//
// A native host (desktop-avatar/ on Windows and Linux, the backend's
// desktop_avatar/cocoa.py on macOS) shows this page in a transparent window
// over the whole work area, and the backend tells it what the agent is doing.
// It is the panel's own FloatingAvatar with the screen for a room.
//
// From the backend, through `desk.receive`, one object at a time:
//   {at, lang}   first: where it was last left, and the language
//   {pose}       idle, think, search or type
//   {cue}        greet, answered, refused, went-well or went-wrong
//   {waiting}    whether something waits on the user
// To the host, through `ipc.postMessage`: "ready", "face:x,y,w,h,held" for
// where clicks may land, and "out:{rest}" for where it came to rest.

import * as React from "react";
import { createRoot } from "react-dom/client";

import { useSteadyPose } from "../webview/agent-avatar";
import { type Cue, cue } from "../webview/avatar-acts";
import { isPlace, type Place } from "../webview/avatar-motion";
import type { AvatarPose } from "../webview/avatar-pose";
import { FloatingAvatar } from "../webview/floating-avatar";
import { setLanguage, t } from "../webview/i18n";
import { STYLE } from "../webview/styles";

declare global {
  interface Window {
    ipc?: { postMessage(message: string): void };
    desk?: { receive(message: Message): void; pointer(x: number, y: number): void };
  }
}

interface Message {
  at?: unknown;
  lang?: string;
  pose?: AvatarPose;
  cue?: Cue;
  waiting?: boolean;
}

/** The variables a webview gets from VS Code, Dark Modern's: the desktop has no theme to give. */
const THEME = `
:root {
  --vscode-font-family: "Segoe UI", system-ui, -apple-system, "Ubuntu", sans-serif;
  --vscode-foreground: #cccccc; --vscode-editorWidget-background: #252526;
  --vscode-panel-border: #3c3c3c; --vscode-descriptionForeground: #9d9d9d;
  --vscode-textLink-foreground: #4daafc; --vscode-widget-shadow: rgba(0,0,0,.36);
}
html, body, #root { background: transparent !important; margin: 0; height: 100%; overflow: hidden; }
.desk-room { position: relative; width: 100vw; height: 100vh; }
.desk-card {
  position: absolute; padding: 6px 10px; border-radius: 10px; white-space: nowrap;
  font: 12px var(--vscode-font-family); color: var(--vscode-foreground);
  background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-panel-border);
  box-shadow: 0 4px 14px var(--vscode-widget-shadow); pointer-events: none;
}
`;

/** What it says while something waits on the user, and how far from its face. */
const CARD_GAP = 12;
const CARD_WIDTH = 150;

const post = (message: string) => window.ipc?.postMessage(message);

function Desk({ start }: { start: Place | undefined }): JSX.Element {
  const [pose, setPose] = React.useState<AvatarPose>("idle");
  const [card, setCard] = React.useState<{ left: number; top: number } | null>(null);
  const steady = useSteadyPose(pose);

  React.useEffect(() => {
    hear = (message) => {
      if (message.pose) setPose(message.pose);
      if (message.waiting !== undefined) setCard(message.waiting ? beside() : null);
      if (message.cue) cue(message.cue);
    };
    early.splice(0).forEach(hear);
    return () => {
      hear = null;
    };
  }, []);

  return (
    <div className="desk-room">
      {card && (
        <div className="desk-card" style={card}>
          {t("Code-AI is waiting for you")}
        </div>
      )}
      <FloatingAvatar
        pose={steady}
        start={start}
        waitsOn={card ? ".desk-card" : undefined}
        onRest={(place) => post(`out:${JSON.stringify({ rest: place })}`)}
      />
    </div>
  );
}

/** Where the card goes: above the face, on whichever side has the room. */
function beside(): { left: number; top: number } {
  const face = document.querySelector(".av-float-grip")?.getBoundingClientRect();
  if (!face) return { left: CARD_GAP, top: CARD_GAP };
  const left = face.left > CARD_WIDTH + CARD_GAP ? face.left - CARD_WIDTH - CARD_GAP : face.right + CARD_GAP;
  return { left, top: Math.max(CARD_GAP, face.top - 36) };
}

let hear: ((message: Message) => void) | null = null;
/** What came before the avatar was there to hear it. */
const early: Message[] = [];
let started = false;

/**
 * Where clicks may land, told to the host whenever it changes. Measured only
 * when it moves: at rest the avatar asks for no frames, and neither does this.
 */
function watchFace(): void {
  const float = document.querySelector(".av-float");
  if (!float) {
    requestAnimationFrame(watchFace);
    return;
  }
  let told = "";
  let queued = false;
  const measure = () => {
    queued = false;
    const r = document.querySelector(".av-float-grip")?.getBoundingClientRect();
    if (!r) return;
    const face = [r.left, r.top, r.width, r.height].map((n) => n.toFixed(0)).join(",");
    const now = `face:${face},${float.hasAttribute("data-held") ? 1 : 0}`;
    if (now !== told) {
      told = now;
      post(now);
    }
  };
  const soon = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(measure);
  };
  // Its every move rewrites its style; being picked up sets data-held.
  new MutationObserver(soon).observe(float, { attributes: true, attributeFilter: ["style", "data-held"] });
  window.addEventListener("resize", soon);
  soon();
}

const style = document.createElement("style");
style.textContent = `${STYLE}\n${THEME}`;
document.head.appendChild(style);

window.desk = {
  receive(message) {
    if (!started) {
      // The first word says where it was and what language to speak: only then is it put down.
      started = true;
      if (message.lang) setLanguage(message.lang);
      createRoot(document.getElementById("root")!).render(<Desk start={isPlace(message.at) ? message.at : undefined} />);
      requestAnimationFrame(watchFace);
    }
    if (hear) hear(message);
    else early.push(message);
  },
  pointer(x, y) {
    // Off its face the window lets the pointer through, so the page never sees it: the host says where it is.
    document.dispatchEvent(new PointerEvent("pointermove", { clientX: x, clientY: y, bubbles: true }));
  },
};
post("ready");
