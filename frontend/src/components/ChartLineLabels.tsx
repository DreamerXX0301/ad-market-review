import { useEffect, useRef, useState } from "react";
import type { ECharts } from "echarts/core";
import type { ChartLineLabel } from "./chartLineLabelLayout";
import {
  layoutChartLineLabels,
  shortChartLineLabel,
} from "./chartLineLabelLayout";
import "./ChartLineLabels.css";

export function ChartLineControls({
  lines,
  visible,
  open,
  onOpen,
  onToggle,
  onAll,
}: {
  lines: ChartLineLabel[];
  visible: ChartLineLabel[];
  open: boolean;
  onOpen: (value: boolean) => void;
  onToggle: (id: string, value: boolean) => void;
  onAll: (value: boolean) => void;
}) {
  const all = !!lines.length && lines.length === visible.length;
  const mixed = !!visible.length && !all;
  const checkbox = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (checkbox.current) checkbox.current.indeterminate = mixed;
  }, [mixed]);
  if (!lines.length) return null;
  const ids = new Set(visible.map((line) => line.id));
  return (
    <div className={`chart-line-controls${open ? " open" : ""}`}>
      <label
        className="chart-line-master"
        title="只切换本图显示，不删除划线，也不改变提醒和策略信号"
      >
        <input
          ref={checkbox}
          type="checkbox"
          checked={all}
          aria-checked={mixed ? "mixed" : all}
          onChange={() => onAll(!all)}
        />
        全部标注
      </label>
      <button
        type="button"
        className="btn small"
        aria-expanded={open}
        onClick={() => onOpen(!open)}
      >
        逐条 {visible.length}/{lines.length}
      </button>
      {open && (
        <div
          className="chart-line-options"
          role="group"
          aria-label="逐条切换支撑压力和事件标注"
        >
          <div className="chart-line-option-actions">
            <button
              type="button"
              className="btn small"
              onClick={() => onAll(true)}
            >
              全部显示
            </button>
            <button
              type="button"
              className="btn small"
              onClick={() => onAll(false)}
            >
              全部隐藏
            </button>
            <button
              type="button"
              className="btn small"
              onClick={() => onOpen(false)}
            >
              收起
            </button>
          </div>
          {lines.map((line) => (
            <label key={line.id} title={line.detail}>
              <input
                type="checkbox"
                checked={ids.has(line.id)}
                onChange={(event) => onToggle(line.id, event.target.checked)}
              />
              <span style={{ color: line.color }}>
                {shortChartLineLabel(line)}
              </span>
              <small>{line.detail}</small>
            </label>
          ))}
          <p>仅影响本图显示；原保存线、事件依据与提醒保持不变。</p>
        </div>
      )}
    </div>
  );
}

export function ChartLineRail({
  chart,
  lines,
  top,
  height,
  width,
  onMore,
}: {
  chart: ECharts | null;
  lines: ChartLineLabel[];
  top: number;
  height: number;
  width: number;
  onMore: () => void;
}) {
  const [positions, setPositions] = useState<Array<{ id: string; y: number }>>(
    [],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const current = useRef(lines);
  current.current = lines;
  useEffect(() => {
    if (!chart || chart.isDisposed()) {
      setPositions([]);
      return;
    }
    const place = () => {
      if (chart.isDisposed()) return;
      if (!(chart.getOption().yAxis as unknown[])?.length) return;
      const next = current.current.map((line) => ({
        id: line.id,
        y: Number(chart.convertToPixel({ yAxisIndex: 0 }, line.price)),
      }));
      setPositions((old) =>
        JSON.stringify(old) === JSON.stringify(next) ? old : next,
      );
    };
    chart.on("finished", place);
    chart.on("datazoom", place);
    const observer = new ResizeObserver(place);
    observer.observe(chart.getDom());
    place();
    return () => {
      observer.disconnect();
      if (!chart.isDisposed()) {
        chart.off("finished", place);
        chart.off("datazoom", place);
      }
    };
  }, [chart, lines]);
  if (!lines.length) return null;
  const layout = layoutChartLineLabels(positions, top, height);
  const byId = new Map(lines.map((line) => [line.id, line]));
  const active = byId.get(hovered || selected || "");
  return (
    <div
      className="chart-line-rail"
      style={{ width, top, height }}
      aria-label="支撑压力与事件短标签"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          setSelected(null);
          setHovered(null);
        }
      }}
    >
      <svg
        className="chart-line-leaders"
        width={width}
        height={height}
        aria-hidden="true"
      >
        {layout.rows.map((row) => (
          <path
            key={row.id}
            d={`M0 ${Math.max(0, Math.min(height, row.y - top))} L8 ${row.labelY - top + layout.rowHeight / 2} L13 ${row.labelY - top + layout.rowHeight / 2}`}
            stroke={byId.get(row.id)?.color}
          />
        ))}
      </svg>
      {layout.rows.map((row) => {
        const line = byId.get(row.id);
        if (!line) return null;
        const description =
          line.detail + (row.outside ? " · 当前价轴范围外" : "");
        return (
          <button
            key={row.id}
            type="button"
            className="chart-line-short-label"
            style={{
              top: row.labelY - top,
              color: line.color,
              height: layout.rowHeight,
            }}
            title={description}
            aria-label={description}
            aria-expanded={selected === line.id}
            onMouseEnter={() => setHovered(line.id)}
            onMouseLeave={() => setHovered(null)}
            onFocus={() => setHovered(line.id)}
            onBlur={() => setHovered(null)}
            onClick={() => setSelected(selected === line.id ? null : line.id)}
          >
            {row.outside === "above" ? "↑" : row.outside === "below" ? "↓" : ""}
            {shortChartLineLabel(line)}
          </button>
        );
      })}
      {!!layout.overflow && (
        <button
          type="button"
          className="chart-line-more"
          style={{ bottom: 0 }}
          onClick={onMore}
        >
          另 {layout.overflow} 条
        </button>
      )}
      {active && (
        <div
          className="chart-line-detail"
          role="status"
          style={{ right: width + 5 }}
        >
          <span>{active.detail}</span>
          <button
            type="button"
            className="icon-btn"
            aria-label="关闭标注说明"
            onClick={() => {
              setSelected(null);
              setHovered(null);
            }}
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}
