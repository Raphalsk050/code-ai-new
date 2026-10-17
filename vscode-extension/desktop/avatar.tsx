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
//   {seat}       where the panel's logo is on the screen, in physical pixels,
//                while its home screen is up; null once it is not
// From the host, `desk.frame(x, y, scale)`: where this window is, in the same
// pixels, so the logo's place can be found in it. To the host, through `ipc.postMessage`: "ready", "face:x,y,w,h,held" for
// where clicks may land, and "out:{rest}" for where it came to rest.

import * as React from "react";
import { createRoot } from "react-dom/client";

import { useSteadyPose } from "../webview/agent-avatar";
import type { ScreenBox } from "../webview/desktop-seat";
import { type Cue, cue } from "../webview/avatar-acts";
import { isPlace, type Place } from "../webview/avatar-motion";
import type { AvatarPose } from "../webview/avatar-pose";
import { FloatingAvatar } from "../webview/floating-avatar";
import { setLanguage, t } from "../webview/i18n";
import { STYLE } from "../webview/styles";

declare global {
  interface Window {
    ipc?: { postMessage(message: string): void };
    desk?: {
      receive(message: Message): void;
      pointer(x: number, y: number): void;
      frame(x: number, y: number, scale: number): void;
    };
  }
}

interface Message {
  at?: unknown;
  lang?: string;
  pose?: AvatarPose;
  cue?: Cue;
  waiting?: boolean;
  seat?: ScreenBox | null;
}

type Box = { left: number; top: number; width: number; height: number };

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
.desk-seat { position: absolute; pointer-events: none; }
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
  const [seat, setSeat] = React.useState<Box | null>(null);
  const steady = useSteadyPose(pose);

  React.useEffect(() => {
    hear = (message) => {
      if (message.pose) setPose(message.pose);
      if (message.waiting !== undefined) setCard(message.waiting ? beside() : null);
      if (message.cue) cue(message.cue);
      if ("seat" in message) setSeat(inRoom(message.seat ?? null));
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
      {seat && <div className="desk-seat" style={seat} />}
      <FloatingAvatar
        pose={steady}
        start={start}
        dock={seat ? ".desk-seat" : undefined}
        scene={seat ? `${seat.left},${seat.top},${seat.width},${seat.height}` : ""}
        waitsOn={card ? ".desk-card" : undefined}
        onRest={(place) => post(`out:${JSON.stringify({ rest: place })}`)}
      />
    </div>
  );
}

/** Where this window is on the screen, in physical pixels, and how many of those to a CSS one. */
let frame = { x: 0, y: 0, scale: 1 };

/** A box on the screen, in this window's CSS pixels; null when it lies off this window. */
function inRoom(box: ScreenBox | null): Box | null {
  if (!box) return null;
  const at = {
    left: (box.x - frame.x) / frame.scale,
    top: (box.y - frame.y) / frame.scale,
    width: box.w / frame.scale,
    height: box.h / frame.scale,
  };
  const off = at.left + at.width < 0 || at.top + at.height < 0 || at.left > innerWidth || at.top > innerHeight;
  return off ? null : at;
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
  frame(x, y, scale) {
    frame = { x, y, scale: scale || 1 };
  },
  pointer(x, y) {
    // Off its face the window lets the pointer through, so the page never sees it: the host says where it is.
    document.dispatchEvent(new PointerEvent("pointermove", { clientX: x, clientY: y, bubbles: true }));
  },
};
post("ready");
