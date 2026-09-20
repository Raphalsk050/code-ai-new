import type { EventEnvelope } from "../../src/protocol";

export const OPTION_SEPARATOR = "::";

export interface QuestionOption {
  label: string;
  description: string;
}

export interface Question {
  prompt: string;
  header: string;
  whyRequired: string;
  options: QuestionOption[];
  multiSelect: boolean;
  allowOther: boolean;
}

export interface QuestionsState {
  pending: Question[];
}

export const initialQuestions: QuestionsState = { pending: [] };

/** `Label :: why it might be the right call`, the wire form from core.interaction. */
function parseOption(raw: string): QuestionOption | null {
  const text = String(raw ?? "").trim();
  if (!text) return null;
  const at = text.indexOf(OPTION_SEPARATOR);
  if (at < 0) return { label: text, description: "" };
  const label = text.slice(0, at).trim();
  if (!label) return null;
  return { label, description: text.slice(at + OPTION_SEPARATOR.length).trim() };
}

function parseQuestion(p: Record<string, any>): Question | null {
  const prompt = String(p.question ?? "").trim();
  if (!prompt) return null;
  const raw = Array.isArray(p.options) ? p.options : [];
  const options = raw
    .filter((o: unknown) => typeof o === "string")
    .map((o: string) => parseOption(o))
    .filter((o: QuestionOption | null): o is QuestionOption => o !== null);
  return {
    prompt,
    header: String(p.header ?? "").trim(),
    whyRequired: String(p.why_required ?? "").trim(),
    options,
    multiSelect: Boolean(p.multi_select),
    allowOther: Boolean(p.allow_other ?? true) || options.length === 0,
  };
}

export function applyQuestions(state: QuestionsState, event: EventEnvelope): QuestionsState {
  switch (event.event_type) {
    case "interaction.question.requested": {
      const q = parseQuestion((event.payload ?? {}) as Record<string, any>);
      return q ? { pending: [...state.pending, q] } : state;
    }
    case "interaction.question.answered":
    case "user.message":
    case "conversation.reset":
      return state.pending.length ? initialQuestions : state;
    default:
      return state;
  }
}

/** The one message the model reads back, mirroring `core.interaction.render_answers`. */
export function renderAnswers(
  questions: Question[],
  chosen: string[][],
  other: string[]
): string {
  const lines: string[] = [];
  questions.forEach((q, i) => {
    const parts = [...(chosen[i] ?? [])];
    const extra = (other[i] ?? "").trim();
    if (extra) parts.push(parts.length ? `além disso: ${extra}` : extra);
    if (!parts.length) return;
    const title = q.header || q.prompt.split("\n")[0].slice(0, 32);
    lines.push(`${i + 1}. ${title}: ${parts.join(", ")}`);
  });
  return lines.join("\n");
}
