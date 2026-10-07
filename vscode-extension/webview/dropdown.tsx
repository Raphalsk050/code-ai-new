import * as React from "react";

import { Codicon } from "./codicon";

export interface DropdownOption {
  value: string;
  label: string;
}

/**
 * A select the page draws itself. The browser's own `<select>` opens a native
 * popup, and an IDE's embedded browser does not always put the pointer where
 * that popup is; a list that is part of the page has no such gap.
 *
 * Arrow keys move, Enter or Space picks, Escape or a press outside closes, and
 * a letter jumps to the option that starts with it. It opens downwards, or
 * upwards when there is no room below.
 */
export function Dropdown({
  value,
  options,
  onChange,
  className = "",
  label,
}: {
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  className?: string;
  /** What it chooses, for a screen reader: the visible label is beside it, not in it. */
  label?: string;
}): JSX.Element {
  const [open, setOpen] = React.useState(false);
  const [up, setUp] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const rootRef = React.useRef<HTMLDivElement>(null);
  const buttonRef = React.useRef<HTMLButtonElement>(null);
  const listRef = React.useRef<HTMLDivElement>(null);
  const listId = React.useId();
  const selected = options.find((option) => option.value === value);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);
  React.useEffect(() => {
    if (open) listRef.current?.querySelector(".active")?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const show = () => {
    const box = buttonRef.current?.getBoundingClientRect();
    const below = window.innerHeight - (box?.bottom ?? 0);
    const wanted = Math.min(options.length, 8) * 28 + 16;
    setUp(below < wanted && (box?.top ?? 0) > below);
    setActive(Math.max(0, options.findIndex((option) => option.value === value)));
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };
  const pick = (picked: string) => {
    close();
    if (picked !== value) onChange(picked);
  };

  const onKey = (event: React.KeyboardEvent) => {
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        show();
      }
      return;
    }
    const last = options.length - 1;
    if (event.key === "Escape") {
      // The panel's own Escape (leave a screen, hand over the keyboard) is not meant here.
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") {
      setOpen(false);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(last, i + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActive(event.key === "Home" ? 0 : last);
    } else if ((event.key === "Enter" || event.key === " ") && options[active]) {
      event.preventDefault();
      pick(options[active].value);
    } else if (event.key.length === 1) {
      const letter = event.key.toLowerCase();
      const found = options.findIndex((option) => option.label.toLowerCase().startsWith(letter));
      if (found >= 0) setActive(found);
    }
  };

  return (
    <div className={`dropdown ${className}`} ref={rootRef} data-value={value} onKeyDown={onKey}>
      <button
        ref={buttonRef}
        type="button"
        className={`dropdown-button${open ? " open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={label}
        onClick={() => (open ? setOpen(false) : show())}
      >
        <span>{selected?.label ?? value}</span>
        <Codicon name="chevron-down" size={12} />
      </button>
      {open && (
        <div className={`dropdown-pop${up ? " up" : ""}`} id={listId} ref={listRef} role="listbox" aria-label={label}>
          {options.map((option, i) => (
            <div
              key={option.value}
              role="option"
              aria-selected={option.value === value}
              data-value={option.value}
              className={`dropdown-opt${i === active ? " active" : ""}${option.value === value ? " selected" : ""}`}
              onPointerEnter={() => setActive(i)}
              // On the press, not the click: nothing that happens between the
              // two (a list redrawn, focus moving) can lose the choice.
              onMouseDown={(event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                pick(option.value);
              }}
            >
              {option.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
