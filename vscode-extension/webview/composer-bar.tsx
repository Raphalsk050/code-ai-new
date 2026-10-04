import * as React from "react";

import type { AppMode, PermissionMode } from "../src/protocol";
import { ChipMenu, ChipOption } from "./chip-menu";
import { t } from "./i18n";
import { IconBrand } from "./icons";
import type { ModelsState } from "./settings";

const PLANNER = ["auto", "plan", "act"] as const;
type Planner = (typeof PLANNER)[number];

/**
 * The session's controls, beside where one writes: mode, plan, permissions on
 * the left (they wrap when narrow), model and send/stop on the right (they
 * never do, so the send button sits in the same corner at every width).
 */
export function ComposerBar({
  mode,
  planner,
  permission,
  model,
  models,
  onMode,
  onPlanner,
  onPermission,
  onModel,
  onListModels,
  action,
}: {
  mode: AppMode;
  planner: string;
  permission: string;
  model: string;
  models: ModelsState;
  onMode: (mode: AppMode) => void;
  onPlanner: (mode: string) => void;
  onPermission: (mode: PermissionMode) => void;
  onModel: (model: string) => void;
  onListModels: () => void;
  /** The send or stop button; omitted where there is nothing to send. */
  action?: React.ReactNode;
}): JSX.Element {
  const modeOptions: ChipOption<AppMode>[] = [
    { value: "agent", label: t("Agent"), hint: t("Talk to the agent") },
    { value: "refactor", label: t("Refactor"), hint: t("Suggest architectural improvements for the selection") },
    { value: "explain", label: t("Explain"), hint: t("Explain the selected code on hover") },
  ];
  const plannerOptions: ChipOption<Planner>[] = [
    { value: "auto", label: "auto" },
    { value: "plan", label: "plan", hint: t("writes the steps before acting") },
    { value: "act", label: "act" },
  ];
  const permissionOptions: ChipOption<PermissionMode>[] = [
    { value: "ask", label: t("Ask") },
    { value: "auto", label: t("Auto") },
    { value: "bypass", label: t("Bypass") },
  ];
  const list = models.list.includes(model) || !model ? models.list : [model, ...models.list];
  const modelOptions: ChipOption<string>[] = list.map((m) => ({ value: m, label: m }));
  const modelStatus =
    models.status === "loading" || (models.status === "idle" && modelOptions.length === 0) ? (
      <div className="chip-note">
        <span className="spinner" /> {t("Listing…")}
      </div>
    ) : models.status === "error" ? (
      <div className="chip-note bad">{models.error}</div>
    ) : modelOptions.length === 0 ? (
      <div className="chip-note">{t("The provider returned no models.")}</div>
    ) : undefined;

  return (
    <div className="composer-bar">
      <div className="composer-opts">
        <ChipMenu
          value={mode}
          options={modeOptions}
          icon={<IconBrand size={14} />}
          title={t("Mode")}
          onPick={onMode}
        />
        {mode === "agent" && (
          <>
            <ChipMenu
              value={(PLANNER as readonly string[]).includes(planner) ? (planner as Planner) : "auto"}
              options={plannerOptions}
              keyLabel={t("plan")}
              title={t("Planner mode")}
              onPick={onPlanner}
            />
            <ChipMenu
              value={permission as PermissionMode}
              options={permissionOptions}
              keyLabel={t("perms")}
              title={t("Permission mode for commands and edits")}
              onPick={onPermission}
            />
          </>
        )}
      </div>
      <div className="composer-end">
        <ChipMenu
          className="model-chip"
          value={model}
          label={model || t("(no model)")}
          options={modelOptions}
          title={model ? `${t("Model")}: ${model}` : t("Model")}
          onOpen={onListModels}
          onPick={onModel}
          status={modelStatus}
        />
        {action}
      </div>
    </div>
  );
}
