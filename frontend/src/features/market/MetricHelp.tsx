import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import "./MarketTable.css";

export function MetricHelp({
  label,
  description,
}: {
  label: string;
  description: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false),
    [position, setPosition] = useState<{
      left: number;
      top?: number;
      bottom?: number;
      width: number;
    }>({ left: 12, top: 12, width: 300 });
  function show(element: HTMLElement) {
    const bounds = element.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    const above = bounds.bottom + 180 > window.innerHeight && bounds.top > 180;
    setPosition({
      left: Math.max(
        12,
        Math.min(bounds.left - 12, window.innerWidth - width - 12),
      ),
      width,
      ...(above
        ? { bottom: window.innerHeight - bounds.top + 8 }
        : { top: bounds.bottom + 8 }),
    });
    setOpen(true);
  }
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  return (
    <>
      <button
        type="button"
        className="market-metric-help"
        aria-label={label + "口径说明"}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={(event) => show(event.currentTarget)}
        onMouseLeave={() => setOpen(false)}
        onFocus={(event) => show(event.currentTarget)}
        onBlur={() => setOpen(false)}
        onClick={(event) => show(event.currentTarget)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setOpen(false);
            event.stopPropagation();
          }
        }}
      >
        ?
      </button>
      {open &&
        createPortal(
          <div
            id={id}
            role="tooltip"
            className="market-metric-tooltip"
            style={position}
          >
            <strong>{label}</strong>
            <span>{description}</span>
          </div>,
          document.body,
        )}
    </>
  );
}

export function ExplainedMetric({
  label,
  value,
  description,
}: {
  label: string;
  value: ReactNode;
  description?: string;
}) {
  return (
    <div className="stat">
      <span className="stat-label market-stat-caption">
        {label}
        {description && <MetricHelp label={label} description={description} />}
      </span>
      <strong>{value}</strong>
    </div>
  );
}
