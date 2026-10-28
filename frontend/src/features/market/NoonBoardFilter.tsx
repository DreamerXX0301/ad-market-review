import type { Row } from "../../lib/api";
import {
  boardLabel,
  boardOf,
  NOON_BOARD_OPTIONS,
} from "./noonBoardPresentation";
import type { NoonBoardFilterValue } from "./noonBoardPresentation";
import "./NoonBoardFilter.css";

export function NoonBoardTag({
  row,
  instrumentId,
  className = "",
}: {
  row?: Row;
  instrumentId?: string;
  className?: string;
}) {
  const board = boardOf(row ?? instrumentId);
  if (board === "unknown") return null;
  return (
    <span
      className={`noon-board-tag noon-board-tag-${board} ${className}`.trim()}
      title="按已留存证券代码与交易所分类，仅供本页显示筛选；不证明历史上市板块身份或涨跌停制度。"
    >
      {boardLabel(board)}
    </span>
  );
}

export default function NoonBoardFilter({
  value,
  onChange,
  disabled = false,
  label = "午评上市板块筛选",
}: {
  value: NoonBoardFilterValue;
  onChange: (value: NoonBoardFilterValue) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <div className="noon-board-filter" role="group" aria-label={label}>
      <span>上市板块</span>
      {NOON_BOARD_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`btn small${value === option.value ? " active" : ""}`}
          aria-pressed={value === option.value}
          disabled={disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
      <small>仅筛选显示，统计与市场温度不变；未知板别仅在“全部”中保留。</small>
    </div>
  );
}
