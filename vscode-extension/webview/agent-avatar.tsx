import * as React from "react";

import type { AvatarPose } from "./avatar-pose";
import { type AvatarSkin, useAvatarSkin } from "./avatar-skin";

const FACE = "M12 3.6c5.3 0 8.6 3.1 8.6 8.2 0 5.4-3.4 8.6-8.6 8.6s-8.6-3.2-8.6-8.6c0-5.1 3.3-8.2 8.6-8.2z";
const LEAF = "M12.4 3.9C12 2 13.6.6 16.1.9c.1 2-1.4 3.4-3.7 3z";
const GLYPH_BOX = "1.34 -0.03 21.32 21.32";
const FX_BOX = "-1 -4 28 28";
const GLYPH_UNITS = 21.32;
const FX_UNITS = 28;

// Halloween: the face carved. Eyes stay where the eyes are.
const CARVED_EYE_L = "M9.45 9.3l2 4.4h-4z";
const CARVED_EYE_R = "M14.55 9.3l2 4.4h-4z";
const CARVED_MOUTH = "M6.9 15.2l1.75 1.05 1.65-1.15 1.7 1.15 1.7-1.15 1.65 1.15 1.75-1.05c-.5 2.1-2.45 3.5-5.1 3.5s-4.6-1.4-5.1-3.5z";
const RIB_L = "M9.2 4.3c-2.5 3.7-2.5 11.9 0 15.6";
const RIB_R = "M14.8 4.3c2.5 3.7 2.5 11.9 0 15.6";

// Christmas: the hat takes the leaf's place, and its sway.
const HAT_CAP = "M5.6 6.9C6.3 2.5 9.6-.8 13.4-.9c3.3-.1 5.7 2 6.4 5.3l-1.6 2.5z";
const HAT_BRIM = "M5.2 7.6C6.8 5.3 9.2 4.2 12 4.2s5.2 1.1 6.8 3.4";

// Easter: two ears where the leaf was.
const EAR_L = "M10.3 5.1C7.9 3.2 6.6-.3 7.6-3.2c2.3.8 4.3 4 4.5 7.9z";
const EAR_R = "M13.7 5.1c2.4-1.9 3.7-5.4 2.7-8.3-2.3.8-4.3 4-4.5 7.9z";
const EAR_IN_L = "M10.2 3.9C8.9 2.5 8.1.5 8.4-1.4c1.2.9 2.2 2.8 2.5 5z";
const EAR_IN_R = "M13.8 3.9c1.3-1.4 2.1-3.4 1.8-5.3-1.2.9-2.2 2.8-2.5 5z";
const NOSE = "M11.1 14.9h1.8l-.9 1.1z";

export function AgentAvatar({
  pose,
  size = 16,
  fx = false,
  skin,
  className,
}: {
  pose: AvatarPose | null;
  size?: number;
  fx?: boolean;
  /** Left out, the avatar wears what the whole page wears. */
  skin?: AvatarSkin;
  className?: string;
}): JSX.Element {
  const mask = "agent-av-" + React.useId().replace(/:/g, "");
  const worn = useAvatarSkin();
  const dress = skin ?? worn;
  // Under about a pixel per unit a mouth or a rib is a smudge, not a detail.
  const detail = size / (fx ? FX_UNITS : GLYPH_UNITS) >= 0.9;
  return (
    <svg
      className={`agent-av${className ? " " + className : ""}`}
      data-pose={pose ?? undefined}
      data-skin={dress === "default" ? undefined : dress}
      width={size}
      height={size}
      viewBox={fx ? FX_BOX : GLYPH_BOX}
      fill="currentColor"
      aria-hidden="true"
    >
      <defs>
        <mask id={mask} maskUnits="userSpaceOnUse" x="-4" y="-6" width="34" height="34">
          <rect x="-4" y="-6" width="34" height="34" fill="#fff" />
          <g className="agent-av-look">
            <g className="agent-av-eyes" fill="#000">
              {dress === "halloween" ? (
                <>
                  <path className="agent-av-eye" d={CARVED_EYE_L} />
                  <path className="agent-av-eye" d={CARVED_EYE_R} />
                </>
              ) : (
                <>
                  <rect className="agent-av-eye" x="8.2" y="9.6" width="2.5" height="4.8" rx="1.25" />
                  <rect className="agent-av-eye" x="13.3" y="9.6" width="2.5" height="4.8" rx="1.25" />
                </>
              )}
            </g>
          </g>
          {dress === "halloween" && detail && <path d={CARVED_MOUTH} fill="#000" />}
        </mask>
      </defs>
      <g className="agent-av-inertia">
        <g className="agent-av-rig">
          {dress === "halloween" && <path className="agent-av-glow" d={FACE} />}
          {dress !== "christmas" && (
            <g className="agent-av-leaf-lag">
              {dress === "easter" ? (
                <g className="agent-av-leaf">
                  <path d={EAR_L} />
                  <path d={EAR_R} />
                  <path className="agent-av-blush" d={EAR_IN_L} />
                  <path className="agent-av-blush" d={EAR_IN_R} />
                </g>
              ) : (
                <path className="agent-av-leaf agent-av-stem" d={LEAF} />
              )}
            </g>
          )}
          <path className="agent-av-face" d={FACE} mask={`url(#${mask})`} />
          {dress === "halloween" && detail && (
            <g className="agent-av-ribs">
              <path d={RIB_L} />
              <path d={RIB_R} />
            </g>
          )}
          {dress === "easter" && detail && <path className="agent-av-blush" d={NOSE} />}
          {dress === "christmas" && (
            <>
              <g className="agent-av-leaf-lag">
                <g className="agent-av-leaf">
                  <path className="agent-av-hat" d={HAT_CAP} />
                  <circle className="agent-av-snow" cx="19.7" cy="4.7" r="1.65" />
                </g>
              </g>
              <path className="agent-av-brim" d={HAT_BRIM} />
            </>
          )}
        </g>
      </g>
      {fx && (
        <>
          <g className="agent-av-fx agent-av-fx-search">
            <g className="agent-av-lens">
              <circle className="agent-av-ring" cx="18.3" cy="15.6" r="2.4" strokeWidth="2.6" />
              <line className="agent-av-ring" x1="20.1" y1="17.4" x2="22.2" y2="19.5" strokeWidth="3.2" strokeLinecap="round" />
              <circle cx="18.3" cy="15.6" r="2.4" fill="#fff" fillOpacity=".22" stroke="currentColor" strokeWidth="1.1" />
              <line x1="20.1" y1="17.4" x2="22.2" y2="19.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </g>
          </g>
          <g className="agent-av-fx agent-av-fx-type">
            <rect className="agent-av-key" x="5.2" y="21.6" width="3" height="1.5" rx=".5" />
            <rect className="agent-av-key" x="8.95" y="21.6" width="3" height="1.5" rx=".5" />
            <rect className="agent-av-key" x="12.7" y="21.6" width="3" height="1.5" rx=".5" />
            <rect className="agent-av-key" x="16.45" y="21.6" width="3" height="1.5" rx=".5" />
          </g>
          <g className="agent-av-fx agent-av-fx-think">
            <circle className="agent-av-dot" cx="19.6" cy="3" r=".75" />
            <circle className="agent-av-dot" cx="21.8" cy=".6" r="1.05" />
            <circle className="agent-av-dot" cx="24.4" cy="-2.2" r="1.4" />
          </g>
        </>
      )}
    </svg>
  );
}

export function useSteadyPose(pose: AvatarPose | null, dwell = 400): AvatarPose | null {
  const [shown, setShown] = React.useState(pose);
  const since = React.useRef(0);
  React.useEffect(() => {
    if (pose === shown) return;
    const wait = pose === null || pose === "idle" ? 0 : Math.max(0, since.current + dwell - performance.now());
    const timer = window.setTimeout(() => {
      since.current = performance.now();
      setShown(pose);
    }, wait);
    return () => window.clearTimeout(timer);
  }, [pose, shown, dwell]);
  return shown;
}
