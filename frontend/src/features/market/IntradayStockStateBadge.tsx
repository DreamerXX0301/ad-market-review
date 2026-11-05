import type { Row } from "../../lib/api";
import "./IntradayStockStateBadge.css";

type StockStateBadge = {
  kind: "sealed" | "broken" | "limit-down" | "resealed";
  label: string;
  title: string;
};

/** Show only explicit current states or the verified pool containing this row. */
export function intradayStockStateBadges(
  row: Row,
  broken = false,
): StockStateBadge[] {
  const upState =
    row.limit_up_state ??
    (broken ? "broken" : row.state === "promoted" ? "sealed" : row.state);
  const downSealed = row.limit_down_state === "sealed";
  if (upState === "sealed" && downSealed) return [];

  const badges: StockStateBadge[] = [];
  if (upState === "sealed") {
    const height = row.continue_day_cnt ?? row.board_count;
    const knownHeight = Number.isInteger(height) && height > 0;
    badges.push({
      kind: "sealed",
      label: knownHeight ? (height === 1 ? "首板" : `${height} 连板`) : "涨停",
      title: knownHeight ? "本时点已核验封板" : "本时点已核验封板，板数待核验",
    });
    if (row.board_transition === "resealed")
      badges.push({
        kind: "resealed",
        label: "回封",
        title: "上一可比时点已核验炸板，本时点已核验封板",
      });
  }
  if (upState === "broken")
    badges.push({
      kind: "broken",
      label: "炸板",
      title: "本时点来源炸板池已核验",
    });
  if (downSealed)
    badges.push({
      kind: "limit-down",
      label: "跌停",
      title: "本时点来源跌停池已核验",
    });
  return badges;
}

export default function IntradayStockStateBadge({
  row,
  broken = false,
}: {
  row: Row;
  broken?: boolean;
}) {
  const badges = intradayStockStateBadges(row, broken);
  if (!badges.length) return null;
  return (
    <span className="intraday-stock-state-badges">
      {badges.map(({ kind, label, title }) => (
        <span
          className="intraday-stock-state"
          data-kind={kind}
          title={title}
          key={kind}
        >
          {label}
        </span>
      ))}
    </span>
  );
}
