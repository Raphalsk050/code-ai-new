import type { SessionState } from "./store/session";
import type { Item } from "./store/transcript";
import { ToolKind, toolKind } from "./tool-target";

export type AvatarPose = "idle" | "search" | "type" | "think";
export type WorkPose = Exclude<AvatarPose, "idle">;

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
