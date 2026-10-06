import * as React from "react";

import type { AvatarPose } from "./avatar-pose";
import { type AvatarSkin, useAvatarSkin } from "./avatar-skin";

const FACE = "M12 3.6c5.3 0 8.6 3.1 8.6 8.2 0 5.4-3.4 8.6-8.6 8.6s-8.6-3.2-8.6-8.6c0-5.1 3.3-8.2 8.6-8.2z";
const LEAF = "M12.4 3.9C12 2 13.6.6 16.1.9c.1 2-1.4 3.4-3.7 3z";
const GLYPH_BOX = "1.34 -0.03 21.32 21.32";
const FX_BOX = "-1 -4 28 28";
const GLYPH_UNITS = 21.32;
const FX_UNITS = 28;

// Halloween: the face carved and lit from inside. The eyes stay where the
// eyes are; the corners are rounded in the mask, like everything in the mark.
const CARVED_EYE_L = "M9.45 9.4l1.8 4.1h-3.6z";
const CARVED_EYE_R = "M14.55 9.4l1.8 4.1h-3.6z";
const GRIN = "M6.9 15Q12 16.6 17.1 15C15.6 19.6 8.4 19.6 6.9 15Z";
// The outer segments of the rind, a shade down. Cut to the face where drawn.
const LOBE_L = "M9.2 4.3c-2.5 3.7-2.5 11.9 0 15.6H2V4.3z";
const LOBE_R = "M14.8 4.3c2.5 3.7 2.5 11.9 0 15.6H22V4.3z";
const STUMP = "M10.3 4.6l.5-2.4c.1-.6 1.5-.7 1.7-.1l.4 2.5z";

// Christmas: a hat where the leaf was. The tail falls to the left, clear of
// the thought and the lens on the right, and sways as the leaf did.
// The body ends on the band's own line, so no red shows under the fur.
const HAT_BODY = "M18.6 6.9C18 2.4 14.6-2.6 9.2-3.1 6.9-3.3 5.9-1.4 6.2 1.2 5.2 2.6 4.9 4.9 5.3 6.9 8 5 16 5 18.6 6.9z";
const HAT_FOLD = "M6.2 1.2C7.9.8 9.6-.9 10.6-2.9c.2 2.7-1.4 5.1-3.5 6-.6-.5-.9-1.2-.9-1.9z";
const HAT_TAIL = "M10.6-2.9C6.2-4 1.9-1.4.7 3l1.8 1.5c.8-1.7 2.2-2.9 3.7-3.3C7.9.8 9.6-.9 10.6-2.9z";
// The fur band: a thick stroke along a shallow arc, a little wider than the
// head. Its shade is the same band half a unit lower, drawn underneath, so
// what shows is a rim along the bottom that follows the round ends.
const HAT_BAND = "M4.6 7.05C7.4 4.65 16.6 4.65 19.4 7.05";
const HAT_BAND_SHADE = "M4.6 7.6C7.4 5.2 16.6 5.2 19.4 7.6";

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
  const outline = mask + "-o";
  const worn = useAvatarSkin();
  const dress = skin ?? worn;
  // Under about a pixel per unit a mouth or a rind is a smudge, not a detail.
  const detail = size / (fx ? FX_UNITS : GLYPH_UNITS) >= 0.9;
  const carved = dress === "halloween";
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
              {carved ? (
                <g stroke="#000" strokeWidth=".6" strokeLinejoin="round">
                  <path className="agent-av-eye" d={CARVED_EYE_L} />
                  <path className="agent-av-eye" d={CARVED_EYE_R} />
                </g>
              ) : (
                <>
                  <rect className="agent-av-eye" x="8.2" y="9.6" width="2.5" height="4.8" rx="1.25" />
                  <rect className="agent-av-eye" x="13.3" y="9.6" width="2.5" height="4.8" rx="1.25" />
                </>
              )}
              {/* In with the eyes: a carving looks around as one piece, and
                  eyes looking down would otherwise run into the grin. */}
              {carved && detail && (
                <>
                  <path d={GRIN} stroke="#000" strokeWidth=".6" strokeLinejoin="round" />
                  {/* Two teeth: white in a mask leaves the face there. */}
                  <rect x="9.9" y="15.1" width="1.7" height="1.7" rx=".35" fill="#fff" />
                  <rect x="12.6" y="17" width="1.7" height="2.4" rx=".35" fill="#fff" />
                </>
              )}
            </g>
          </g>
        </mask>
        {carved && detail && (
          <clipPath id={outline}>
            <path d={FACE} />
          </clipPath>
        )}
      </defs>
      <g className="agent-av-inertia">
        <g className="agent-av-rig">
          {carved && <path className="agent-av-glow" d={FACE} />}
          {carved && <path className="agent-av-stump" d={STUMP} />}
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
          {carved && detail && (
            <g className="agent-av-lobes" clipPath={`url(#${outline})`} mask={`url(#${mask})`}>
              <path d={LOBE_L} />
              <path d={LOBE_R} />
            </g>
          )}
          {dress === "easter" && detail && <path className="agent-av-blush" d={NOSE} />}
          {dress === "christmas" && (
            <g className="agent-av-hat-set">
              <path className="agent-av-hat" d={HAT_BODY} />
              <path className="agent-av-hat-fold" d={HAT_FOLD} />
              <g className="agent-av-leaf-lag agent-av-hat-lag">
                <g className="agent-av-leaf agent-av-hat-tail">
                  <path className="agent-av-hat" d={HAT_TAIL} />
                  <circle className="agent-av-snow-shade" cx="1.3" cy="3.7" r="1.9" />
                  <circle className="agent-av-snow" cx="1.1" cy="3.45" r="1.6" />
                </g>
              </g>
              <path className="agent-av-band agent-av-band-shade" d={HAT_BAND_SHADE} />
              <path className="agent-av-band" d={HAT_BAND} />
            </g>
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
