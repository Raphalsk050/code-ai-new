import { STYLE as BASE_STYLE } from "./styles/base";
import { MOTION_STYLE } from "./styles/motion";
import { PANELS_STYLE } from "./styles/panels";
import { RAIL_STYLE } from "./styles/rail";
import { TOKENS } from "./styles/tokens";
import { CHROME_STYLE } from "./styles/chrome";
import { TRANSCRIPT_STYLE } from "./styles/transcript";
import { SETTINGS_STYLE } from "./styles/settings";

/**
 * One stylesheet, composed in cascade order: base first, then motion, then the
 * rail's layout and the panels inside it. Split by concern rather than by
 * component so a change to timing or to the drawer never means reading 900
 * lines of chat CSS.
 */
export const STYLE = [TOKENS, BASE_STYLE, MOTION_STYLE, RAIL_STYLE, PANELS_STYLE, CHROME_STYLE, TRANSCRIPT_STYLE, SETTINGS_STYLE].join("\n");
