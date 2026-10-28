import type { Row } from "../../lib/api";
import { Badge } from "../../components/UI";
import { memberHasConfirmedDailyLimit } from "./memberDailyLimit";

/** Reuse the retained member proof; unknown states intentionally have no badge. */
export default function MemberDailyLimitBadge({
  row,
  date,
}: {
  row: Row;
  date: string;
}) {
  if (!memberHasConfirmedDailyLimit(row, date)) return null;
  return (
    <span
      className="member-daily-limit"
      aria-label={`${date} 已确认收盘涨停`}
      title={`${date} 已按原始收盘报价与留存来源池确认涨停；不按涨幅阈值推断`}
    >
      <Badge tone="danger">涨停</Badge>
    </span>
  );
}
