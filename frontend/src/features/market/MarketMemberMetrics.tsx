import type { Row } from "../../lib/api";
import { Badge } from "../../components/UI";
import { integer } from "../../lib/format";
import {
  HistoryReturnCell,
  YearLimitCountCell,
} from "../topics/MemberHistoryCells";

const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
const boardNames: Record<string, string> = {
  main: "主板",
  chinext: "创业板",
  star: "科创板",
  beijing: "北交所",
};

export function MemberBoardCell({ row }: { row: Row }) {
  const known = typeof row.board === "string" && !!boardNames[row.board];
  return (
    <span
      title={[
        row.board_scope ||
          "按当前代码与交易所分类，仅供本页板块筛选，不证明历史身份",
        row.board_reason,
      ]
        .filter(Boolean)
        .join("；")}
    >
      <Badge tone={known ? "neutral" : "warn"}>
        {known ? row.board_label || boardNames[row.board] : "板块待核验"}
      </Badge>
    </span>
  );
}

export function MemberReturnCell({
  row,
  field,
}: {
  row: Row;
  field: "return_ytd_pct" | "return_mtd_pct" | "change_30d_pct";
}) {
  const prefix = field.replace(/_pct$/, "");
  const coverage = row[prefix + "_coverage"] || {};
  return (
    <div className="market-member-history">
      <HistoryReturnCell
        value={row[field]}
        reason={row[prefix + "_reason"]}
        start={coverage.start}
        end={coverage.end}
        priceBasis={coverage.price_basis}
        missingSessions={coverage.missing_sessions}
      />
    </div>
  );
}

export function MemberYearLimitCell({ row }: { row: Row }) {
  return (
    <div className="market-member-history">
      <YearLimitCountCell row={row} />
    </div>
  );
}

export function MemberRecentLimitCell({
  row,
  date,
}: {
  row: Row;
  date: string;
}) {
  const latest =
    typeof row.last_limit_up_date === "string" ? row.last_limit_up_date : null;
  const complete = row.last_limit_up_complete === true;
  return (
    <div
      className="market-member-recent"
      title={[
        row.last_limit_up_reason,
        `截至所选盘面日 ${date}；距所选日按市场交易日历计算`,
        complete
          ? "最近事件至所选日状态已核验"
          : "存在覆盖缺口，仅标最近已确认事件，不证明之后没有涨停",
      ]
        .filter(Boolean)
        .join("；")}
    >
      <span className={latest ? "" : "muted"}>{latest || "—"}</span>
      {latest ? (
        <>
          <small>
            {count(row.sessions_since_limit_up)
              ? `距所选日 ${integer(row.sessions_since_limit_up)} 个交易日`
              : "交易日距离待核验"}
          </small>
          {!complete && (
            <small className="market-member-warning">最近已确认</small>
          )}
        </>
      ) : (
        <small>未有可确认事件</small>
      )}
    </div>
  );
}
