import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import "./ChartControls.css";

export type ChartChoice = {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
};

/** Small chart menus share one visual and keyboard model across price and display controls. */
export function ChartSelect({
  label,
  value,
  options,
  onChange,
  title,
  disabled = false,
}: {
  label: string;
  value: string;
  options: ChartChoice[];
  onChange: (value: string) => void;
  title?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState({ top: 0, left: 0, width: 220 });
  const current = options.find((item) => item.value === value);
  const scope = JSON.stringify([value, options, disabled]);
  const openingScope = useRef(scope);
  const visible = open && !disabled && scope === openingScope.current;
  const enabled = options
    .map((item, index) => (item.disabled ? -1 : index))
    .filter((index) => index >= 0);
  function close(focus = false) {
    setOpen(false);
    if (focus && trigger.current?.getClientRects().length)
      trigger.current.focus();
  }
  function show(last = false) {
    if (disabled || !enabled.length) return;
    openingScope.current = scope;
    const selected = options.findIndex(
      (item) => item.value === value && !item.disabled,
    );
    setActive(
      last
        ? enabled[enabled.length - 1]
        : selected >= 0
          ? selected
          : enabled[0],
    );
    setOpen(true);
  }
  function choose(index: number) {
    const item = options[index];
    if (!visible || !item || item.disabled) return;
    onChange(item.value);
    close(true);
  }
  useLayoutEffect(() => {
    if (open && !visible) setOpen(false);
  }, [open, visible]);
  useLayoutEffect(() => {
    if (!visible) return;
    function place() {
      const button = trigger.current;
      if (
        !button?.isConnected ||
        button.closest('[hidden],[inert],[aria-hidden="true"]') ||
        !button.getClientRects().length
      ) {
        setOpen(false);
        return;
      }
      const rect = button.getBoundingClientRect();
      const width = Math.min(Math.max(rect.width, 240), window.innerWidth - 24);
      const height = Math.min(
        300,
        popup.current?.offsetHeight || 200,
        window.innerHeight - 24,
      );
      setPosition({
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        top: Math.max(
          12,
          rect.bottom + height + 8 > window.innerHeight
            ? rect.top - height - 6
            : rect.bottom + 6,
        ),
        width,
      });
    }
    place();
    popup.current?.focus();
    popup.current
      ?.querySelectorAll<HTMLElement>('[role="option"]')
      [active]?.scrollIntoView({ block: "nearest" });
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [visible]);
  useEffect(() => {
    if (!visible) return;
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !wrapper.current?.contains(event.target) &&
        !popup.current?.contains(event.target)
      )
        close();
    };
    const hide = () => setOpen(false);
    const focusOutside = (event: FocusEvent) => {
      if (
        event.target instanceof Node &&
        !wrapper.current?.contains(event.target) &&
        !popup.current?.contains(event.target)
      )
        close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        close(true);
      }
    };
    const observer = new MutationObserver(() => {
      if (trigger.current?.closest('[hidden],[inert],[aria-hidden="true"]'))
        hide();
    });
    let ancestor = wrapper.current?.parentElement;
    while (ancestor) {
      observer.observe(ancestor, {
        attributes: true,
        attributeFilter: ["hidden", "inert", "aria-hidden"],
      });
      ancestor = ancestor.parentElement;
    }
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", focusOutside);
    window.addEventListener("keydown", escape, true);
    window.addEventListener("hashchange", hide);
    return () => {
      observer.disconnect();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", focusOutside);
      window.removeEventListener("keydown", escape, true);
      window.removeEventListener("hashchange", hide);
    };
  }, [visible]);
  return (
    <div className="chart-choice" ref={wrapper}>
      <button
        ref={trigger}
        type="button"
        className={`chart-choice-button${visible ? " open" : ""}`}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={visible}
        aria-controls={visible ? id : undefined}
        title={title}
        disabled={disabled || !enabled.length}
        onClick={() => (visible ? close() : show())}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            show(event.key === "ArrowUp");
          }
        }}
      >
        <span>{current?.label || label}</span>
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      {visible &&
        createPortal(
          <div
            ref={popup}
            id={id}
            role="listbox"
            aria-label={label}
            aria-activedescendant={`${id}-${active}`}
            tabIndex={-1}
            className="chart-choice-menu"
            style={position}
            onKeyDown={(event) => {
              if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
                event.preventDefault();
                const offset = event.key === "ArrowUp" ? -1 : 1;
                const next =
                  event.key === "Home"
                    ? enabled[0]
                    : event.key === "End"
                      ? enabled[enabled.length - 1]
                      : enabled[
                          (enabled.indexOf(active) + offset + enabled.length) %
                            enabled.length
                        ];
                setActive(next);
                popup.current
                  ?.querySelectorAll<HTMLElement>('[role="option"]')
                  [next]?.scrollIntoView({ block: "nearest" });
              } else if (["Enter", " "].includes(event.key)) {
                event.preventDefault();
                choose(active);
              } else if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                close(true);
              } else if (event.key === "Tab") close(true);
            }}
          >
            <div className="chart-choice-caption">{label}</div>
            {options.map((item, index) => (
              <div
                key={item.value}
                id={`${id}-${index}`}
                role="option"
                aria-selected={item.value === value}
                aria-disabled={item.disabled || undefined}
                className={`chart-choice-option${active === index ? " focused" : ""}`}
                onPointerMove={() => !item.disabled && setActive(index)}
                onClick={() => choose(index)}
              >
                <div>
                  <strong>{item.label}</strong>
                  {item.description && <small>{item.description}</small>}
                </div>
                {item.value === value && <Check size={15} aria-hidden="true" />}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}

export function ChartToggle({
  label,
  checked,
  onChange,
  color,
  title,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  color?: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      className={`chart-option-toggle${checked ? " selected" : ""}`}
      aria-pressed={checked}
      title={title}
      style={
        color ? ({ "--chart-toggle-color": color } as CSSProperties) : undefined
      }
      onClick={() => onChange(!checked)}
    >
      <span className="chart-toggle-dot" aria-hidden="true" />
      {label}
    </button>
  );
}
