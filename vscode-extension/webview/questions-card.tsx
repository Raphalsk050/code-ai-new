import * as React from "react";

import { ripple } from "./motion";
import { Question, renderAnswers } from "./store/questions";

/**
 * The agent's questions, answered in the chat instead of a modal: on a narrow
 * sidebar a modal hides the very context the choice depends on. One card per
 * question, number keys like the terminal cards, free text when the agent
 * allows it.
 */
export function QuestionsCard({
  questions,
  onAnswer,
}: {
  questions: Question[];
  onAnswer: (text: string) => void;
}): JSX.Element {
  const [index, setIndex] = React.useState(0);
  const [chosen, setChosen] = React.useState<string[][]>(() => questions.map(() => []));
  const [other, setOther] = React.useState<string[]>(() => questions.map(() => ""));

  React.useEffect(() => {
    setIndex(0);
    setChosen(questions.map(() => []));
    setOther(questions.map(() => ""));
  }, [questions]);

  const question = questions[index];
  if (!question) return <></>;
  const last = index === questions.length - 1;

  const pick = (label: string) => {
    setChosen((prev) => {
      const next = prev.map((c) => c.slice());
      const current = next[index] ?? [];
      if (question.multiSelect) {
        next[index] = current.includes(label)
          ? current.filter((l) => l !== label)
          : [...current, label];
      } else {
        next[index] = [label];
      }
      return next;
    });
    if (!question.multiSelect && !last) setIndex((i) => i + 1);
  };

  const advance = () => {
    if (!last) {
      setIndex((i) => i + 1);
      return;
    }
    const text = renderAnswers(questions, chosen, other);
    if (text.trim()) onAnswer(text);
  };

  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const n = Number(e.key);
    if (Number.isInteger(n) && n >= 1 && n <= question.options.length) {
      e.preventDefault();
      pick(question.options[n - 1].label);
    }
  };

  return (
    <div className="qcard" onKeyDown={onKey} tabIndex={-1}>
      <div className="qcard-head">
        <span style={{ flex: 1 }}>{question.header || "O agente precisa decidir"}</span>
        {questions.length > 1 && (
          <span className="rail-section-note">
            {index + 1} de {questions.length}
          </span>
        )}
      </div>
      <div className="qcard-body">
        <div className="qcard-prompt">{question.prompt}</div>
        {question.whyRequired && <div className="qcard-why">{question.whyRequired}</div>}

        {question.options.map((option, i) => {
          const picked = (chosen[index] ?? []).includes(option.label);
          return (
            <button
              key={option.label}
              className={`qopt ${picked ? "picked" : ""}`}
              onPointerDown={ripple}
              onClick={() => pick(option.label)}
            >
              <span className="qopt-key">{i + 1}</span>
              <span style={{ flex: 1 }}>
                <span className="qopt-label">{option.label}</span>
                {option.description && <div className="qopt-desc">{option.description}</div>}
              </span>
            </button>
          );
        })}

        {question.allowOther && (
          <textarea
            rows={question.options.length ? 2 : 3}
            value={other[index] ?? ""}
            placeholder={question.options.length ? "ou responda com suas palavras" : "sua resposta"}
            onChange={(e) =>
              setOther((prev) => {
                const next = prev.slice();
                next[index] = e.target.value;
                return next;
              })
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                advance();
              }
            }}
          />
        )}

        <div className="qcard-foot">
          <span className="hint">
            {question.options.length ? "1-9 escolhe · " : ""}
            {question.multiSelect ? "várias opções · " : ""}
            Cmd/Ctrl+Enter envia
          </span>
          {index > 0 && (
            <button className="panel-btn" onClick={() => setIndex((i) => i - 1)}>
              Voltar
            </button>
          )}
          <button className="panel-btn primary" onPointerDown={ripple} onClick={advance}>
            {last ? "Responder" : "Próxima"}
          </button>
        </div>
      </div>
    </div>
  );
}
