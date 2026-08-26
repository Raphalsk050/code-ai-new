import * as React from "react";

type IconProps = { size?: number; className?: string };

const base = (size: number): React.SVGProps<SVGSVGElement> => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
});

export const IconUser = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </svg>
);

// "CI" lettermark — the Code-AI brand glyph (matches media/icon.svg).
export const IconCI = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M12.4 7.6 A5 5 0 1 0 12.4 16.4" />
    <path d="M15.6 7 H19.4" />
    <path d="M15.6 17 H19.4" />
    <path d="M17.5 7 V17" />
  </svg>
);

export const IconTool = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2l-5.5 5.5a1.5 1.5 0 0 0 2.1 2.1l5.5-5.5a4 4 0 0 0 5.2-5.4l-2.5 2.5-2.1-.4-.4-2.1z" />
  </svg>
);

export const IconBrain = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V15a3 3 0 0 0 4 2.8" />
    <path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8V15a3 3 0 0 1-4 2.8" />
    <path d="M9 4a3 3 0 0 1 6 0" />
  </svg>
);

export const IconChevron = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M9 18l6-6-6-6" />
  </svg>
);

export const IconCheck = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M20 6L9 17l-5-5" />
  </svg>
);

export const IconX = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M18 6L6 18M6 6l12 12" />
  </svg>
);

export const IconWarn = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    <path d="M12 9v4M12 17h.01" />
  </svg>
);

export const IconSend = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
  </svg>
);

export const IconStop = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </svg>
);

export const IconBack = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M19 12H5M12 19l-7-7 7-7" />
  </svg>
);

export const IconPlus = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export const IconTrash = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
  </svg>
);

export const IconBroom = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M19.4 4.6 13 11M9 21l-5-5a3 3 0 0 1 0-4l3-3 6 6-3 3a3 3 0 0 1-4 0l1.5 1.5M5 16l3 3" />
  </svg>
);

export const IconHistory = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M3 3v5h5" />
    <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
    <path d="M12 7v5l3 2" />
  </svg>
);

export const IconFile = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
  </svg>
);

export const IconSettings = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

export const IconWand = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M15 4V2M15 10V8M12.5 5.5h-2M19.5 5.5h-2M4 20l10-10M17 7l1.5-1.5" />
    <path d="M14 6l4 4" />
  </svg>
);

export const IconBook = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

export const IconRefresh = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
    <path d="M3 21v-5h5" />
  </svg>
);

export const IconSpark = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M12 3l1.8 4.6L18.5 9l-4.7 1.4L12 15l-1.8-4.6L5.5 9l4.7-1.4z" />
  </svg>
);
