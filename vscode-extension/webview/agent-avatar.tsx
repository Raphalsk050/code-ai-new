import * as React from "react";

import type { AvatarPose } from "./avatar-pose";

const FACE = "M12 3.6c5.3 0 8.6 3.1 8.6 8.2 0 5.4-3.4 8.6-8.6 8.6s-8.6-3.2-8.6-8.6c0-5.1 3.3-8.2 8.6-8.2z";
const LEAF = "M12.4 3.9C12 2 13.6.6 16.1.9c.1 2-1.4 3.4-3.7 3z";
const GLYPH_BOX = "1.34 -0.03 21.32 21.32";
const FX_BOX = "-1 -4 28 28";

export function AgentAvatar({
  pose,
  size = 16,
  fx = false,
  className,
}: {
  pose: AvatarPose | null;
  size?: number;
  fx?: boolean;
  className?: string;
}): JSX.Element {
  const mask = "agent-av-" + React.useId().replace(/:/g, "");
  return (
    <svg
      className={`agent-av${className ? " " + className : ""}`}
      data-pose={pose ?? undefined}
      width={size}
      height={size}
      viewBox={fx ? FX_BOX : GLYPH_BOX}
      fill="currentColor"
      aria-hidden="true"
    >
      <defs>
        <mask id={mask} maskUnits="userSpaceOnUse" x="-4" y="-6" width="34" height="34">
          <rect x="-4" y="-6" width="34" height="34" fill="#fff" />
          <g className="agent-av-eyes" fill="#000">
            <rect className="agent-av-eye" x="8.2" y="9.6" width="2.5" height="4.8" rx="1.25" />
            <rect className="agent-av-eye" x="13.3" y="9.6" width="2.5" height="4.8" rx="1.25" />
          </g>
        </mask>
      </defs>
      <g className="agent-av-rig">
        <path className="agent-av-leaf" d={LEAF} />
        <path d={FACE} mask={`url(#${mask})`} />
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
