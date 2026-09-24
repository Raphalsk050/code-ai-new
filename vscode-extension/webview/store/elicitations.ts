// Questions an MCP server is waiting on, one card each until it is answered.
// The tool call that asked is paused meanwhile, so they are not tied to the
// transcript: a new conversation does not make the server stop waiting.

import type { EventEnvelope } from "../../src/protocol";

export interface ElicitationField {
  name: string;
  title: string;
  description: string;
  type: "string" | "number" | "integer" | "boolean";
  required: boolean;
  enum?: string[];
  enum_names?: string[];
  format?: string;
  minimum?: number;
  maximum?: number;
  default?: unknown;
}

export interface PendingElicitation {
  id: string;
  server: string;
  message: string;
  fields: ElicitationField[];
}

export interface ElicitationsState {
  pending: PendingElicitation[];
}

export const initialElicitations: ElicitationsState = { pending: [] };

export function applyElicitations(
  state: ElicitationsState,
  event: EventEnvelope
): ElicitationsState {
  const p = event.payload ?? {};
  switch (event.event_type) {
    case "mcp.elicitation.requested":
      return {
        pending: [
          ...state.pending,
          {
            id: String(p.id ?? ""),
            server: String(p.server ?? ""),
            message: String(p.message ?? ""),
            fields: Array.isArray(p.fields) ? (p.fields as ElicitationField[]) : [],
          },
        ],
      };
    case "mcp.elicitation.resolved": {
      const pending = state.pending.filter((item) => item.id !== String(p.id ?? ""));
      return pending.length === state.pending.length ? state : { pending };
    }
    default:
      return state;
  }
}

/** The form's starting values: the schema's defaults, booleans off. */
export function initialValues(fields: ElicitationField[]): Record<string, string | boolean> {
  const values: Record<string, string | boolean> = {};
  for (const field of fields) {
    if (field.type === "boolean") values[field.name] = field.default === true;
    else values[field.name] = field.default == null ? "" : String(field.default);
  }
  return values;
}

/** The first required field left empty, so the card can say so before sending. */
export function missingField(
  fields: ElicitationField[],
  values: Record<string, string | boolean>
): ElicitationField | null {
  for (const field of fields) {
    if (!field.required || field.type === "boolean") continue;
    if (!String(values[field.name] ?? "").trim()) return field;
  }
  return null;
}
