import {
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
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
  adjacentMarketDate,
  adjacentMonth,
  availableMonths,
  calendarMonthCells,
  monthForYear,
  retainedMarketDates,
  validateMarketDateJump,
} from "./marketDate";
import "./MarketDatePicker.css";

export default function MarketDatePicker({
  value,
  dates,
  onChange,
}: {
  value: string;
  dates: string[];
  onChange: (date: string) => void;
}) {
  const retained = useMemo(() => retainedMarketDates(dates), [dates]);
  const allowed = useMemo(() => new Set(retained), [retained]);
  const months = useMemo(() => availableMonths(retained), [retained]);
  const years = useMemo(
    () => [...new Set(months.map((month) => month.slice(0, 4)))],
    [months],
  );
  const newest = retained.at(-1) || "";
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [position, setPosition] = useState({ top: 82, left: 12, width: 340 });
  const previous = adjacentMarketDate(retained, value, -1);
  const next = adjacentMarketDate(retained, value, 1);
  const previousMonth = adjacentMonth(months, month, -1);
  const nextMonth = adjacentMonth(months, month, 1);
  const cells = calendarMonthCells(month);

  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus();
  }, []);

  function applyDate(selected: string) {
    if (!allowed.has(selected)) return;
    if (selected !== value) onChange(selected);
    close(true);
  }

  function openCalendar() {
    if (open) return close();
    setMonth(
      months.includes(value.slice(0, 7))
        ? value.slice(0, 7)
        : newest.slice(0, 7),
    );
    setDraft(allowed.has(value) ? value : "");
    setError("");
    setOpen(true);
  }

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const bounds = trigger.current?.getBoundingClientRect();
      if (!bounds) return;
      const width = Math.min(340, window.innerWidth - 24);
      setPosition({
        top: Math.max(
          12,
          Math.min(bounds.bottom + 8, window.innerHeight - 460),
        ),
        left: Math.max(
          12,
          Math.min(bounds.right - width, window.innerWidth - width - 12),
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
  }, [open]);

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
        close(true);
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("focusin", focusOutside);
    document.addEventListener("keydown", escape);
    const focus = requestAnimationFrame(() => input.current?.focus());
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("focusin", focusOutside);
      document.removeEventListener("keydown", escape);
      cancelAnimationFrame(focus);
    };
  }, [open, close]);

  return (
    <div className="market-date-picker" ref={wrapper}>
      <button
        type="button"
        className="market-date-step"
        aria-label="前一交易日"
        title={previous ? "前一交易日 " + previous : "已到最早留存交易日"}
        disabled={!previous}
        onClick={() => previous && applyDate(previous)}
      >
        <ChevronLeft size={14} />
      </button>
      <button
        type="button"
        ref={trigger}
        className={"market-date-trigger" + (open ? " is-open" : "")}
        aria-label={"盘面观察日期：" + (value || "暂无日期")}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        disabled={!retained.length}
        onClick={openCalendar}
        title="选择已留存交易日；仅作用于盘面研究"
      >
        <CalendarDays size={14} />
        <span>{value || "暂无日期"}</span>
        <ChevronDown size={11} />
      </button>
      <button
        type="button"
        className="market-date-step"
        aria-label="后一交易日"
        title={next ? "后一交易日 " + next : "已到最新留存交易日"}
        disabled={!next}
        onClick={() => next && applyDate(next)}
      >
        <ChevronRight size={14} />
      </button>
      <button
        type="button"
        className="market-date-step market-date-latest"
        aria-label="回到最新交易日"
        title={newest ? "回到最新交易日 " + newest : "暂无已留存交易日"}
        disabled={!newest || value === newest}
        onClick={() => newest && applyDate(newest)}
      >
        <RotateCcw size={13} />
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
            aria-label="选择盘面观察日期"
            aria-describedby={id + "-help"}
          >
            <div className="market-date-popup-title">
              <div>
                <strong>盘面观察日</strong>
                <span>仅改变盘面研究，策略与跟踪保持独立</span>
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
                const result = validateMarketDateJump(draft, retained);
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
                />
                <button type="submit">跳转</button>
              </div>
              <p id={id + "-error"} className="market-date-error" role="status">
                {error}
              </p>
            </form>
            <div className="market-date-month">
              <button
                type="button"
                aria-label="上一个有数据的月份"
                disabled={!previousMonth}
                onClick={() => previousMonth && setMonth(previousMonth)}
              >
                <ChevronLeft size={16} />
              </button>
              <div>
                <select
                  aria-label="查看年份"
                  value={month.slice(0, 4)}
                  onChange={(event) =>
                    setMonth(
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
                  onChange={(event) => setMonth(event.target.value)}
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
                aria-label="下一个有数据的月份"
                disabled={!nextMonth}
                onClick={() => nextMonth && setMonth(nextMonth)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
            <table
              className="market-date-calendar"
              aria-label={month + " 可选交易日"}
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
                          className={
                            (cell.date === value ? "is-selected " : "") +
                            (!cell.inMonth ? "other-month" : "")
                          }
                          disabled={!cell.inMonth || !allowed.has(cell.date)}
                          aria-pressed={cell.date === value}
                          aria-label={
                            cell.date +
                            (cell.date === value ? "，当前盘面日" : "")
                          }
                          title={
                            allowed.has(cell.date)
                              ? cell.date +
                                (cell.date === newest ? " · 最新留存" : "")
                              : cell.date + " · 未留存交易日"
                          }
                          onClick={() => applyDate(cell.date)}
                        >
                          {cell.day}
                          {cell.date === newest && <i aria-hidden="true" />}
                        </button>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="market-date-popup-footer">
              <span id={id + "-help"}>
                灰色日期未留存，不可选择。
                <small>
                  {retained[0]} 至 {newest}
                </small>
              </span>
              <button
                type="button"
                disabled={value === newest}
                onClick={() => newest && applyDate(newest)}
              >
                回到最新
              </button>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
