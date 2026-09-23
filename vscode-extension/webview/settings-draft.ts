import type { Settings } from "../src/protocol";

/**
 * Local, editable mirror of the backend settings. `null` until the bridge
 * answers `getSettings`; the panel shows a loading state meanwhile.
 */
export type Draft = Record<string, string | number | boolean | string[] | null>;

/** The panel's working copy of a backend snapshot. */
export function draftFromSettings(settings: Settings): Draft {
  return {
    model: settings.model,
    api_mode: settings.api_mode,
    base_url: settings.base_url,
    language: settings.language,
    permission_mode: settings.permission_mode,
    reasoning_effort: settings.reasoning_effort,
    followup_reasoning_effort: settings.followup_reasoning_effort ?? "none",
    learn: settings.learn,
    inline_hints_enabled: settings.inline_hints_enabled,
    inline_model: settings.inline_model,
    max_context_tokens: settings.max_context_tokens,
    workspace: settings.workspace,
    disabled_tools: settings.disabled_tools ?? [],
    subagent_models: settings.subagent_models ?? [],
    max_concurrent_subagents: settings.max_concurrent_subagents ?? 1,
    temperature: settings.temperature,
    top_p: settings.top_p,
    top_k: settings.top_k,
  };
}

/**
 * A fresh snapshot, holding on to what the user typed in every field the
 * backend refused.
 *
 * `updateSettings` answers with the configuration as it stands, so a refused
 * field comes back carrying the value the user was replacing. Writing that
 * straight into the form is what makes the save button look like it discards
 * edits instead of saving them: the refused value has to survive so the user
 * can correct it against the error message.
 */
export function keepRefusedEdits(
  fresh: Draft,
  submitted: Draft | null,
  errors: Record<string, string>
): Draft {
  if (!submitted) return fresh;
  const kept: Draft = { ...fresh };
  for (const field of Object.keys(errors)) {
    if (field in submitted) kept[field] = submitted[field];
  }
  return kept;
}
