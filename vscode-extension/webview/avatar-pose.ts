import type { SessionState } from "./store/session";
import type { Item } from "./store/transcript";
import { ToolKind, toolKind } from "./tool-target";

export type AvatarPose = "idle" | "search" | "type" | "think" | "anxious";
/** What it does while the agent works; idle and anxious are for while it waits on the user. */
export type WorkPose = Exclude<AvatarPose, "idle" | "anxious">;

const TOOL_POSE: Record<ToolKind, WorkPose> = {
  read: "search",
  search: "search",
  web: "search",
  write: "type",
  run: "type",
  agent: "think",
  plan: "think",
  tool: "think",
};

export function avatarPose(session: Pick<SessionState, "status" | "runningTools">, last: Item | undefined): WorkPose {
  const tools = session.runningTools;
  if (session.status === "EXECUTING_TOOL" && tools.length > 0) {
    return TOOL_POSE[toolKind(tools[tools.length - 1].name)];
  }
  if (last?.kind === "assistant" && last.streaming) return "type";
  return "think";
}

/**
 * How the turn that just ended went, as the avatar takes it: glad of work
 * that got done, sorry when it broke. A plain answer, or a turn the user
 * stopped, is nothing to it.
 */
export function turnMood(status: string, items: Item[]): "went-well" | "went-wrong" | null {
  if (status === "ERROR" || status === "FAILED") return "went-wrong";
  if (status !== "READY" && status !== "IDLE") return null;
  for (let i = items.length - 1; i >= 0 && items[i].kind !== "user"; i--) {
    if (items[i].kind === "tool") return "went-well";
  }
  return null;
}
