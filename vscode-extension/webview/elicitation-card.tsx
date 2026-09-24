import * as React from "react";

import { t, tf } from "./i18n";

import {
  ElicitationField,
  initialValues,
  missingField,
  PendingElicitation,
} from "./store/elicitations";

export type ElicitationAction = "accept" | "decline" | "cancel";

/**
 * A question an MCP server asked in the middle of a tool call. The call waits
 * for this card, so it sits where the approval card does, above the composer.
 */
export function ElicitationCard({
  request,
  error,
  onAnswer,
}: {
  request: PendingElicitation;
  /** Why the last answer was refused, when it was. */
  error: string;
  onAnswer: (action: ElicitationAction, content?: Record<string, unknown>) => void;
}): JSX.Element {
  const [values, setValues] = React.useState(() => initialValues(request.fields));
  const [problem, setProblem] = React.useState("");

  React.useEffect(() => {
    setValues(initialValues(request.fields));
    setProblem("");
  }, [request.id]);

  const set = (name: string, value: string | boolean) =>
    setValues((current) => ({ ...current, [name]: value }));

  const send = () => {
    const missing = missingField(request.fields, values);
    if (missing) {
      setProblem(tf("{0} is required.", missing.title));
      return;
    }
    const content: Record<string, unknown> = {};
    for (const field of request.fields) {
      const value = values[field.name];
      if (field.type === "boolean" || String(value ?? "").trim()) content[field.name] = value;
    }
    setProblem("");
    onAnswer("accept", content);
  };

  return (
    <div className="qcard elicit-card" role="dialog" aria-label={tf("{0} asks", request.server)}>
      <div className="qcard-head">
        <strong>{tf("{0} asks", request.server)}</strong>
        <span className="rail-section-note">MCP</span>
      </div>
      <div className="qcard-body">
        {request.message && <div className="qcard-prompt">{request.message}</div>}
        {request.fields.map((field) => (
          <FieldInput
            key={field.name}
            field={field}
            value={values[field.name]}
            onChange={(value) => set(field.name, value)}
            onEnter={send}
          />
        ))}
        {(problem || error) && <div className="elicit-error">{problem || error}</div>}
        <div className="qcard-foot">
          <span className="hint">{t("The server's tool call waits for this answer.")}</span>
          <button className="btn-ghost" onClick={() => onAnswer("decline")}>
            {t("Decline")}
          </button>
          <button className="btn-primary" onClick={send}>
            {t("Send")}
          </button>
        </div>
      </div>
    </div>
  );
}

function FieldInput({
  field,
  value,
  onChange,
  onEnter,
}: {
  field: ElicitationField;
  value: string | boolean | undefined;
  onChange: (value: string | boolean) => void;
  onEnter: () => void;
}): JSX.Element {
  const label = (
    <span className="elicit-label">
      {field.title}
      {field.required && <span className="elicit-required"> *</span>}
    </span>
  );
  const hint = field.description ? <span className="elicit-hint">{field.description}</span> : null;

  if (field.type === "boolean") {
    return (
      <label className="elicit-field elicit-check">
        <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
        {label}
        {hint}
      </label>
    );
  }
  if (field.enum) {
    return (
      <label className="elicit-field">
        {label}
        <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {!field.required && <option value="">{t("(none)")}</option>}
          {field.required && !value && <option value="">{t("(choose)")}</option>}
          {field.enum.map((option, index) => (
            <option key={option} value={option}>
              {field.enum_names?.[index] ?? option}
            </option>
          ))}
        </select>
        {hint}
      </label>
    );
  }
  const type =
    field.type === "number" || field.type === "integer"
      ? "number"
      : field.format === "email"
        ? "email"
        : field.format === "uri"
          ? "url"
          : field.format === "date"
            ? "date"
            : "text";
  return (
    <label className="elicit-field">
      {label}
      <input
        type={type}
        step={field.type === "integer" ? 1 : undefined}
        min={field.minimum}
        max={field.maximum}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onEnter();
        }}
      />
      {hint}
    </label>
  );
}
