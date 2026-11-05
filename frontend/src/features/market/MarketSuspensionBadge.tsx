import type { Row } from "../../lib/api";

export default function MarketSuspensionBadge({
  evidence,
}: {
  evidence?: Row;
}) {
  if (evidence?.state !== "suspended" || evidence.full_session !== true)
    return null;
  const source =
    evidence.source === "exchange" ? "交易所公告" : "东方财富 Choice";
  return (
    <span className="badge warn" title={`全天停牌 · ${source}`}>
      停牌
    </span>
  );
}
