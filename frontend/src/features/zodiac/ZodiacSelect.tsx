import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ChangeEvent, ComponentPropsWithoutRef, ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

type Option = { value: string; label: string; disabled?: boolean };
function text(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      isValidElement<{ children?: ReactNode }>(child)
        ? text(child.props.children)
        : String(child),
    )
    .join("");
}
function optionsFor(children: ReactNode): Option[] {
  return Children.toArray(children).flatMap((child) => {
    if (
      !isValidElement<{
        value?: string | number;
        children?: ReactNode;
        disabled?: boolean;
      }>(child)
    )
      return [];
    return child.type === "option"
      ? [
          {
            value: String(child.props.value ?? text(child.props.children)),
            label: text(child.props.children),
            disabled: child.props.disabled,
          },
        ]
      : optionsFor(child.props.children);
  });
}
/** Styled listbox with keyboard selection and modal-safe focus ownership. */
export default function ZodiacSelect({
  children,
  value,
  onChange,
  disabled,
  "aria-label": label,
  ...props
}: ComponentPropsWithoutRef<"select">) {
  const options = optionsFor(children),
    selected = options.find((o) => o.value === String(value)) || options[0];
  const [open, setOpen] = useState(false),
    [index, setIndex] = useState(0),
    [position, setPosition] = useState({
      left: 0,
      top: 0,
      width: 220,
      maxHeight: 280,
    }),
    [host, setHost] = useState<Element | null>(null);
  const trigger = useRef<HTMLButtonElement>(null),
    popup = useRef<HTMLDivElement>(null),
    id = useId();
  const choose = (option: Option) => {
    if (option.disabled) return;
    onChange?.({
      target: { value: option.value },
      currentTarget: { value: option.value },
    } as ChangeEvent<HTMLSelectElement>);
    setOpen(false);
    trigger.current?.focus();
  };
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const bounds = trigger.current?.getBoundingClientRect();
      if (!bounds) return;
      const width = Math.min(
          Math.max(bounds.width, 210),
          window.innerWidth - 24,
        ),
        below = window.innerHeight - bounds.bottom - 12,
        above = bounds.top - 12,
        height = Math.min(280, Math.max(below, above));
      setPosition({
        left: Math.max(
          12,
          Math.min(bounds.left, window.innerWidth - width - 12),
        ),
        top:
          below >= Math.min(280, options.length * 38 + 12)
            ? bounds.bottom + 6
            : Math.max(
                12,
                bounds.top - Math.min(height, options.length * 38 + 12) - 6,
              ),
        width,
        maxHeight: height,
      });
    };
    setHost(trigger.current?.closest('[role="dialog"]') || document.body);
    update();
    const outside = (event: PointerEvent) => {
      if (
        !trigger.current?.contains(event.target as Node) &&
        !popup.current?.contains(event.target as Node)
      )
        setOpen(false);
    };
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    document.addEventListener("pointerdown", outside);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      document.removeEventListener("pointerdown", outside);
    };
  }, [open, options.length]);
  useEffect(() => {
    if (open)
      popup.current
        ?.querySelector(`[data-option-index="${index}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [index, open]);
  function start() {
    setIndex(
      Math.max(
        0,
        options.findIndex((o) => o.value === String(value)),
      ),
    );
    setOpen(true);
  }
  return (
    <span className="zodiac-select">
      <button
        id={props.id}
        ref={trigger}
        type="button"
        className="zodiac-select-trigger"
        role="combobox"
        aria-label={label || selected?.label || "选择选项"}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-haspopup="listbox"
        aria-activedescendant={open ? `${id}-${index}` : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : start())}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            return;
          }
          if (event.key === "Tab") {
            setOpen(false);
            return;
          }
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            if (!open) {
              start();
              return;
            }
            setIndex((i) =>
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? options.length - 1
                  : (i +
                      (event.key === "ArrowDown" ? 1 : -1) +
                      options.length) %
                    options.length,
            );
          } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (open && options[index]) choose(options[index]);
            else start();
          } else if (event.key.length === 1 && open) {
            const match = options.findIndex((o) =>
              o.label
                .toLocaleLowerCase()
                .startsWith(event.key.toLocaleLowerCase()),
            );
            if (match >= 0) setIndex(match);
          }
        }}
      >
        <span>{selected?.label || "请选择"}</span>
        <ChevronDown size={14} className={open ? "rotated" : ""} />
      </button>
      {open &&
        host &&
        createPortal(
          <div
            ref={popup}
            id={id}
            role="listbox"
            aria-label={label || "选项"}
            className="zodiac-select-menu"
            style={position}
          >
            {options.map((option, i) => (
              <div
                key={option.value}
                id={`${id}-${i}`}
                role="option"
                aria-selected={option.value === String(value)}
                aria-disabled={option.disabled || undefined}
                data-option-index={i}
                className={`${i === index ? "highlighted" : ""} ${option.value === String(value) ? "selected" : ""}`}
                onPointerMove={() => setIndex(i)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(option)}
              >
                <span>{option.label}</span>
                {option.value === String(value) && <Check size={15} />}
              </div>
            ))}
          </div>,
          host,
        )}
    </span>
  );
}
