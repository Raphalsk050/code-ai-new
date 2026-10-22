import * as React from "react";
import { createPortal } from "react-dom";

import { t } from "./i18n";

/** How big the floating avatar may be, in px. */
export const AVATAR_SIZE = { default: 88, min: 40, max: 220 } as const;

const PRESETS: { label: string; size: number }[] = [
  { label: "Small", size: 56 },
  { label: "Medium", size: 88 },
  { label: "Large", size: 120 },
  { label: "Huge", size: 168 },
];

export function clampAvatarSize(size: unknown): number {
  const value = Number(size);
  if (!Number.isFinite(value) || value <= 0) return AVATAR_SIZE.default;
  return Math.round(Math.max(AVATAR_SIZE.min, Math.min(AVATAR_SIZE.max, value)));
}

const pixels = (size: number) => `${size}px`;

const MENU_W = 224;
const MENU_H = 128;

/**
 * Where the menu goes, beside the avatar's box: right of it when there is
 * room, else left, and kept inside the window.
 */
export function menuSpot(avatar: DOMRect, width = innerWidth, height = innerHeight): { left: number; top: number } {
  const gap = 8;
  const left = avatar.right + gap + MENU_W <= width ? avatar.right + gap : Math.max(gap, avatar.left - gap - MENU_W);
  const top = Math.max(gap, Math.min(height - MENU_H - gap, avatar.top + avatar.height / 2 - MENU_H / 2));
  return { left, top };
}

/** Right-click on the avatar: how big it is, picked or slid to. */
export function AvatarSizeMenu({
  at,
  size,
  onSize,
  onClose,
}: {
  at: { left: number; top: number };
  size: number;
  onSize: (size: number) => void;
  onClose: () => void;
}): JSX.Element {
  const box = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) onClose();
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    // At once: the right-click that opened it pressed before the menu came up.
    window.addEventListener("pointerdown", outside, true);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("keydown", key);
    };
  }, [onClose]);
  React.useEffect(() => {
    box.current?.querySelector<HTMLElement>("button.on, button")?.focus();
  }, []);
  return createPortal(
    <div
      ref={box}
      className="av-size-menu"
      role="dialog"
      aria-label={t("Avatar size")}
      style={{ left: at.left, top: at.top, width: MENU_W }}
      onContextMenu={(event) => event.preventDefault()}
    >
      <div className="av-size-title">
        {t("Avatar size")}
        <span className="av-size-value">{pixels(size)}</span>
      </div>
      <div className="av-size-presets">
        {PRESETS.map((preset) => (
          <button
            key={preset.size}
            className={size === preset.size ? "on" : ""}
            onClick={() => onSize(preset.size)}
            title={pixels(preset.size)}
          >
            {t(preset.label, "avatar")}
          </button>
        ))}
      </div>
      <input
        type="range"
        min={AVATAR_SIZE.min}
        max={AVATAR_SIZE.max}
        step={4}
        value={size}
        aria-label={t("Avatar size")}
        onChange={(event) => onSize(clampAvatarSize(event.target.value))}
      />
      <button className="av-size-reset" disabled={size === AVATAR_SIZE.default} onClick={() => onSize(AVATAR_SIZE.default)}>
        {t("Default size")}
      </button>
    </div>,
    document.body
  );
}
