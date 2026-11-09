import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Layers3, Search } from "lucide-react";
import type { Row } from "../../lib/api";
import "./StrategyPicker.css";

export default function StrategyPicker({
  value,
  strategies,
  onChange,
}: {
  value: string;
  strategies: Row[];
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [query, setQuery] = useState(""),
    [active, setActive] = useState(0);
  const [position, setPosition] = useState({
    left: 0,
    top: 0,
    width: 340,
    maxHeight: 420,
  });
  const trigger = useRef<HTMLButtonElement>(null),
    popup = useRef<HTMLDivElement>(null),
    search = useRef<HTMLInputElement>(null);
  const id = useId();
  const current = strategies.find((strategy) => strategy.strategy_id === value);
  const choices = [
    {
      strategy_id: "",
      name: "全部策略",
      tracking_count: strategies.reduce(
        (sum, strategy) => sum + (Number(strategy.tracking_count) || 0),
        0,
      ),
    },
    ...strategies,
  ].filter(
    (strategy) =>
      !query.trim() ||
      strategy.name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const choose = (next: string) => {
    onChange(next);
    setOpen(false);
    trigger.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(360, window.innerWidth - 24);
      const below = window.innerHeight - rect.bottom - 20;
      const above = rect.top - 20;
      const openBelow = below >= 340 || below >= above;
      const maxHeight = Math.min(420, Math.max(0, openBelow ? below : above));
      setPosition({
        width,
        left: Math.max(
          12,
          Math.min(rect.right - width, window.innerWidth - width - 12),
        ),
        top: openBelow ? rect.bottom + 8 : rect.top - maxHeight - 8,
        maxHeight,
      });
    };
    const outside = (event: PointerEvent) => {
      if (
        !trigger.current?.contains(event.target as Node) &&
        !popup.current?.contains(event.target as Node)
      )
        setOpen(false);
    };
    place();
    search.current?.focus();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", outside);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", outside);
    };
  }, [open]);
  useEffect(() => setActive(0), [query]);
  return (
    <>
      <button
        ref={trigger}
        className={`tracking-strategy-picker ${open ? "is-open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => {
          setQuery("");
          setOpen(!open);
        }}
      >
        <span className="tracking-picker-icon">
          <Layers3 size={17} />
        </span>
        <span className="tracking-picker-caption">
          <small>筛选策略</small>
          <strong>{current?.name || "全部策略"}</strong>
        </span>
        <span className="tracking-picker-total">
          {current
            ? `${current.tracking_count ?? 0} 跟踪`
            : `${strategies.length} 个策略`}
        </span>
        <ChevronDown size={15} />
      </button>
      {open &&
        createPortal(
          <div
            ref={popup}
            className="tracking-strategy-popup"
            style={{ position: "fixed", ...position }}
          >
            <div className="tracking-picker-search">
              <Search size={16} />
              <input
                ref={search}
                role="combobox"
                aria-label="搜索跟踪策略"
                aria-expanded="true"
                aria-controls={id}
                aria-activedescendant={
                  choices[active] ? `${id}-${active}` : undefined
                }
                placeholder="搜索策略名称…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setOpen(false);
                    trigger.current?.focus();
                  }
                  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    const next = Math.max(
                      0,
                      Math.min(
                        choices.length - 1,
                        active + (event.key === "ArrowDown" ? 1 : -1),
                      ),
                    );
                    setActive(next);
                    popup.current
                      ?.querySelectorAll('[role="option"]')
                      [next]?.scrollIntoView({ block: "nearest" });
                  }
                  if (event.key === "Enter" && choices[active]) {
                    event.preventDefault();
                    choose(choices[active].strategy_id);
                  }
                  if (event.key === "Tab") setOpen(false);
                }}
              />
            </div>
            <div
              id={id}
              className="tracking-picker-list"
              role="listbox"
              aria-label="跟踪策略"
            >
              {choices.map((strategy, index) => (
                <button
                  role="option"
                  id={`${id}-${index}`}
                  key={strategy.strategy_id}
                  aria-selected={value === strategy.strategy_id}
                  className={active === index ? "is-active" : ""}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(strategy.strategy_id)}
                >
                  <span>
                    <strong>{strategy.name}</strong>
                    <small>
                      {strategy.strategy_id
                        ? `跟踪中 ${strategy.tracking_count ?? 0} 只`
                        : "查看所有策略的跟踪股票"}
                    </small>
                  </span>
                  {value === strategy.strategy_id && <Check size={17} />}
                </button>
              ))}
              {!choices.length && (
                <p className="tracking-picker-empty">没有匹配的策略</p>
              )}
            </div>
            <div className="tracking-picker-foot">
              ↑ ↓ 选择 · Enter 确认 · Esc 收起
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
