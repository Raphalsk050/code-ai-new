import * as React from "react";

import { Codicon } from "./codicon";

export interface ChipOption<V extends string> {
  value: V;
  label: string;
  hint?: string;
}

/**
 * A pill that opens a menu above itself: the composer's mode, plan, permission
 * and model pickers. Arrow keys move, Enter picks, Escape or a click outside
 * closes and gives focus back to the pill.
 */
export function ChipMenu<V extends string>({
  label,
  value,
  options,
  icon,
  keyLabel,
  title,
  className = "",
  onOpen,
  onPick,
  status,
}: {
  /** What the chip shows; the selected option's label when omitted. */
  label?: React.ReactNode;
  value: V;
  options: ChipOption<V>[];
  icon?: React.ReactNode;
  /** A quiet prefix naming the setting ("plan", "perms"). */
  keyLabel?: string;
  title: string;
  className?: string;
  onOpen?: () => void;
  onPick: (value: V) => void;
  /** Shown instead of the list while it is unavailable (loading, error). */
  status?: React.ReactNode;
}): JSX.Element {
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const rootRef = React.useRef<HTMLDivElement>(null);
  const buttonRef = React.useRef<HTMLButtonElement>(null);
  const menuId = React.useId();

  const selected = options.find((o) => o.value === value);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const toggle = () => {
    if (!open) {
      onOpen?.();
      setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    }
    setOpen(!open);
  };

  const pick = (v: V) => {
    setOpen(false);
    buttonRef.current?.focus();
    if (v !== value) onPick(v);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        toggle();
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (e.key === "ArrowDown" && options.length) {
      e.preventDefault();
      setActive((i) => (i + 1) % options.length);
    } else if (e.key === "ArrowUp" && options.length) {
      e.preventDefault();
      setActive((i) => (i - 1 + options.length) % options.length);
    } else if ((e.key === "Enter" || e.key === " ") && options[active]) {
      e.preventDefault();
      pick(options[active].value);
    }
  };

  return (
    <div className={`chip-menu ${className}`} ref={rootRef} onKeyDown={onKey}>
      <button
        ref={buttonRef}
        type="button"
        className={`chip ${open ? "open" : ""}`}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={toggle}
      >
        {icon}
        {keyLabel && <span className="chip-key">{keyLabel}</span>}
        <span className="chip-value">{label ?? selected?.label ?? value}</span>
        <Codicon name="chevron-down" size={12} className="chip-chev" />
      </button>
      {open && (
        <div className="chip-pop" id={menuId} role="listbox" aria-label={title}>
          {status ??
            options.map((o, i) => (
              <div
                key={o.value}
                role="option"
                aria-selected={o.value === value}
                className={`chip-opt ${i === active ? "active" : ""} ${o.value === value ? "selected" : ""}`}
                onPointerEnter={() => setActive(i)}
                onClick={() => pick(o.value)}
              >
                <span className="chip-check">{o.value === value && <Codicon name="check" size={14} />}</span>
                <span className="chip-opt-label">{o.label}</span>
                {o.hint && <span className="chip-opt-hint">{o.hint}</span>}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
