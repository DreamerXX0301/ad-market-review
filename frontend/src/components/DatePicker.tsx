import {
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  adjacentMonth,
  calendarMonthCells,
  monthForYear,
  datePickerModel,
  datePickerSelectable,
  moveDatePickerFocus,
  validateDatePickerChoice,
} from "./marketDate";
import "./MarketDatePicker.css";

export default function DatePicker({
  value,
  onChange,
  min,
  max,
  allowedDates,
  disabled = false,
  ariaLabel = "选择日期",
  placeholder = "选择日期",
  title = "选择日期",
  help,
  compact = false,
  shortcut,
}: {
  value: string;
  onChange: (date: string) => void;
  min?: string;
  max?: string;
  allowedDates?: string[];
  disabled?: boolean;
  ariaLabel?: string;
  placeholder?: string;
  title?: string;
  help?: string;
  compact?: boolean;
  shortcut?: { label: string; date: string };
}) {
  // Calendar convenience only; it never declares market data available today.
  const today = new Date();
  const fallbackDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const model = useMemo(
    () => datePickerModel({ value, min, max, allowedDates, fallbackDate }),
    [value, min, max, allowedDates, fallbackDate],
  );
  const months = model.months;
  const years = useMemo(
    () => [...new Set(months.map((month) => month.slice(0, 4)))],
    [months],
  );
  const blocked = disabled || !months.length;
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [openState, setOpen] = useState(false);
  const scope = JSON.stringify([
    value,
    model.min,
    model.max,
    model.allowedDates,
  ]);
  const openingScope = useRef(scope);
  // Prop changes invalidate the portal immediately, before effects can clear
  // an input draft belonging to the previous value or selectable range.
  const open = openState && !blocked && openingScope.current === scope;
  const [month, setMonth] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [focused, setFocused] = useState("");
  const [position, setPosition] = useState({ top: 82, left: 12, width: 340 });
  const previousMonth = adjacentMonth(months, month, -1);
  const nextMonth = adjacentMonth(months, month, 1);
  const cells = calendarMonthCells(month);

  const triggerActive = useCallback(() => {
    const button = trigger.current;
    return (
      !!button?.isConnected &&
      !button.closest('[hidden], [inert], [aria-hidden="true"]') &&
      button.getClientRects().length > 0
    );
  }, []);
  const close = useCallback(
    (restoreFocus = false) => {
      setOpen(false);
      if (restoreFocus && triggerActive()) trigger.current?.focus();
    },
    [triggerActive],
  );

  function applyDate(selected: string) {
    if (blocked || !open || !triggerActive()) return close();
    if (!datePickerSelectable(selected, model)) return;
    if (selected !== value) onChange(selected);
    close(true);
  }

  function openCalendar() {
    if (blocked || !triggerActive()) return;
    if (open) return close();
    openingScope.current = scope;
    setMonth(model.initialDate.slice(0, 7));
    setFocused(model.initialDate);
    setDraft(datePickerSelectable(value, model) ? value : "");
    setError("");
    setOpen(true);
  }

  function focusDay(date: string) {
    setFocused(date);
    setMonth(date.slice(0, 7));
    requestAnimationFrame(() =>
      popup.current
        ?.querySelector<HTMLButtonElement>(`[data-calendar-date="${date}"]`)
        ?.focus(),
    );
  }
  function changeMonth(next: string) {
    setMonth(next);
    const candidate = calendarMonthCells(next).find(
      (cell) => cell.inMonth && datePickerSelectable(cell.date, model),
    );
    if (candidate) setFocused(candidate.date);
  }
  useLayoutEffect(() => {
    if (openState && (blocked || openingScope.current !== scope)) {
      close();
      setDraft("");
      setError("");
    }
  }, [blocked, scope, openState, close]);
  useLayoutEffect(() => {
    if (!open) return;
    const hidden = () => {
      if (!triggerActive()) close();
    };
    const routeChanged = () => close();
    // Retained routes use hidden rather than unmount. Observe only the small
    // trigger ancestor chain while open; never observe the document subtree.
    const observer = new MutationObserver(hidden);
    let ancestor: HTMLElement | null = trigger.current;
    while (ancestor) {
      observer.observe(ancestor, {
        attributes: true,
        attributeFilter: ["hidden", "inert", "aria-hidden"],
      });
      ancestor = ancestor.parentElement;
    }
    hidden();
    window.addEventListener("hashchange", routeChanged);
    return () => {
      observer.disconnect();
      window.removeEventListener("hashchange", routeChanged);
    };
  }, [open, close, triggerActive]);
  useEffect(() => {
    if (!open || blocked) return;
    if (!months.includes(month)) setMonth(model.initialDate.slice(0, 7));
    if (!datePickerSelectable(focused, model)) setFocused(model.initialDate);
  }, [open, blocked, model, months, month, focused]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      if (!triggerActive()) return close();
      const bounds = trigger.current?.getBoundingClientRect();
      if (!bounds) return;
      const width = Math.min(340, window.innerWidth - 24);
      const height = Math.min(
        popup.current?.offsetHeight || 475,
        window.innerHeight - 24,
      );
      const below = window.innerHeight - bounds.bottom - 12;
      const top =
        below >= height
          ? bounds.bottom + 8
          : bounds.top - height - 8 >= 12
            ? bounds.top - height - 8
            : Math.max(
                12,
                Math.min(bounds.bottom + 8, window.innerHeight - height - 12),
              );
      setPosition({
        top,
        left: Math.max(
          12,
          Math.min(bounds.left, window.innerWidth - width - 12),
        ),
        width,
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, error, close, triggerActive]);

  useEffect(() => {
    if (!open) return;
    const inside = (target: EventTarget | null) =>
      target instanceof Node &&
      (wrapper.current?.contains(target) || popup.current?.contains(target));
    const dismiss = (event: PointerEvent) => {
      if (!inside(event.target)) close();
    };
    const focusOutside = (event: FocusEvent) => {
      if (!inside(event.target)) close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        close(true);
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("focusin", focusOutside);
    // Run before enclosing modal document handlers, so Escape closes one layer.
    window.addEventListener("keydown", escape, true);
    const focus = requestAnimationFrame(() => input.current?.focus());
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("focusin", focusOutside);
      window.removeEventListener("keydown", escape, true);
      cancelAnimationFrame(focus);
    };
  }, [open, close]);

  return (
    <div
      className={"date-picker" + (compact ? " is-compact" : "")}
      ref={wrapper}
    >
      <button
        type="button"
        ref={trigger}
        className={"date-picker-trigger" + (open ? " is-open" : "")}
        aria-label={ariaLabel + "：" + (value || "未选择")}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        disabled={blocked}
        onClick={openCalendar}
        title={help || title}
        onKeyDown={(event) => {
          if (
            (event.key === "ArrowDown" && event.altKey) ||
            event.key === "F4"
          ) {
            event.preventDefault();
            openCalendar();
          }
        }}
      >
        <CalendarDays size={14} />
        <span className={value ? "" : "placeholder"}>
          {value || placeholder}
        </span>
        <ChevronDown size={11} />
      </button>
      {open &&
        createPortal(
          <div
            ref={popup}
            className="market-date-popup"
            style={{
              ...position,
              maxHeight: "calc(100dvh - " + (position.top + 12) + "px)",
            }}
            id={id}
            role="dialog"
            aria-label={title}
            aria-describedby={id + "-help"}
          >
            <div className="market-date-popup-title">
              <div>
                <strong>{title}</strong>
                <span>
                  {help ||
                    (model.allowedDates
                      ? "仅可选择已提供的可用日期"
                      : "选择日期或直接输入，支持快速切换年月")}
                </span>
              </div>
              <button
                type="button"
                className="market-date-close"
                aria-label="关闭日期选择"
                onClick={() => close(true)}
              >
                <X size={16} />
              </button>
            </div>
            <form
              className="market-date-jump"
              onSubmit={(event) => {
                event.preventDefault();
                const result = validateDatePickerChoice(draft, model);
                setError(result.error);
                if (result.date) applyDate(result.date);
              }}
            >
              <label htmlFor={id + "-input"}>指定日期</label>
              <div>
                <input
                  ref={input}
                  id={id + "-input"}
                  value={draft}
                  maxLength={10}
                  placeholder="YYYY-MM-DD"
                  autoComplete="off"
                  spellCheck={false}
                  aria-invalid={!!error}
                  aria-describedby={error ? id + "-error" : id + "-help"}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setError("");
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "ArrowDown") {
                      event.preventDefault();
                      focusDay(focused || model.initialDate);
                    }
                  }}
                />
                <button type="submit">确定</button>
              </div>
              <p id={id + "-error"} className="market-date-error" role="status">
                {error}
              </p>
            </form>
            <div className="market-date-month">
              <button
                type="button"
                aria-label="上一个可用月份"
                disabled={!previousMonth}
                onClick={() => previousMonth && changeMonth(previousMonth)}
              >
                <ChevronLeft size={16} />
              </button>
              <div>
                <select
                  aria-label="查看年份"
                  value={month.slice(0, 4)}
                  onChange={(event) =>
                    changeMonth(
                      monthForYear(months, event.target.value, month) || month,
                    )
                  }
                >
                  {years.map((year) => (
                    <option key={year} value={year}>
                      {year}年
                    </option>
                  ))}
                </select>
                <select
                  aria-label="查看月份"
                  value={month}
                  onChange={(event) => changeMonth(event.target.value)}
                >
                  {months
                    .filter((item) => item.slice(0, 4) === month.slice(0, 4))
                    .map((item) => (
                      <option key={item} value={item}>
                        {Number(item.slice(5))}月
                      </option>
                    ))}
                </select>
              </div>
              <button
                type="button"
                aria-label="下一个可用月份"
                disabled={!nextMonth}
                onClick={() => nextMonth && changeMonth(nextMonth)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
            <table
              className="market-date-calendar"
              aria-label={month + " 可选日期"}
            >
              <thead>
                <tr>
                  {["一", "二", "三", "四", "五", "六", "日"].map((label) => (
                    <th key={label} scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 6 }, (_, week) => (
                  <tr key={week}>
                    {cells.slice(week * 7, week * 7 + 7).map((cell) => (
                      <td key={cell.date}>
                        <button
                          type="button"
                          data-calendar-date={cell.date}
                          tabIndex={cell.date === focused ? 0 : -1}
                          className={
                            (cell.date === value ? "is-selected " : "") +
                            (!cell.inMonth ? "other-month" : "")
                          }
                          disabled={
                            !cell.inMonth ||
                            !datePickerSelectable(cell.date, model)
                          }
                          aria-pressed={cell.date === value}
                          aria-label={
                            cell.date +
                            (cell.date === value ? "，当前选择" : "")
                          }
                          title={
                            datePickerSelectable(cell.date, model)
                              ? cell.date +
                                (cell.date === shortcut?.date
                                  ? " · " + shortcut.label
                                  : "")
                              : cell.date + " · 不可选"
                          }
                          onClick={() => applyDate(cell.date)}
                          onFocus={() => setFocused(cell.date)}
                          onKeyDown={(event) => {
                            const next = moveDatePickerFocus(
                              cell.date,
                              event.key,
                              model,
                              event.shiftKey,
                            );
                            if (next) {
                              event.preventDefault();
                              focusDay(next);
                            }
                          }}
                        >
                          {cell.day}
                          {cell.date === shortcut?.date && (
                            <i aria-hidden="true" />
                          )}
                        </button>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="market-date-popup-footer">
              <span id={id + "-help"}>
                {model.allowedDates
                  ? "灰色日期不可选"
                  : "方向键移动 · PageUp/Down切换月份"}
                <small>
                  {model.min} 至 {model.max}
                </small>
              </span>
              {shortcut && (
                <button
                  type="button"
                  disabled={
                    value === shortcut.date ||
                    !datePickerSelectable(shortcut.date, model)
                  }
                  onClick={() => applyDate(shortcut.date)}
                >
                  {shortcut.label}
                </button>
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
