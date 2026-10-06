import * as React from "react";

// The arc is a dash on the field's own outline, so it covers the same distance
// every instant whatever the field's shape. Lengths are in hundredths of the
// outline (the rects carry pathLength 100).
const TAIL = 25;
const LEAD = 19.5;
// Dashes stacked from the whole arc down to its brightest point. Layer k of n
// is painted at 1 / (n - k), which leaves the stack one even step brighter per
// layer: a straight fade from the border's colour up to the accent.
const LAYERS = 16;

// Every value is a plain length. A unitless number, or a calc() over one, is
// accepted for a dash but not interpolated: the arc would jump half the outline
// at mid-cycle instead of running round it.
const px = (value: number) => `${Math.round(value * 1000) / 1000}px`;

const ARC = Array.from({ length: LAYERS }, (_, k) => {
  const share = (LAYERS - k) / LAYERS;
  const length = (TAIL + LEAD) * share;
  const tail = TAIL * share;
  return {
    "--ring-dash": px(length),
    "--ring-gap": px(100 - length),
    "--ring-from": px(tail),
    "--ring-to": px(tail - 100),
    strokeOpacity: 1 / (LAYERS - k),
  } as React.CSSProperties;
});

/** The accent arc that runs around a field while the agent works. */
export function BusyRing(): JSX.Element {
  return (
    <svg className="busy-ring" aria-hidden="true" focusable="false">
      {ARC.map((style, k) => (
        <rect key={k} pathLength={100} style={style} />
      ))}
    </svg>
  );
}
