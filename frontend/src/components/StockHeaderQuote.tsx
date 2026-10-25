import { num, percent, tone } from "../lib/format";
import { stockHeaderQuote } from "./stockQuotePresentation";

export default function StockHeaderQuote({
  data,
  instrumentId,
  asOf,
}: {
  data: Record<string, any> | null;
  instrumentId: string;
  asOf?: string;
}) {
  const view = stockHeaderQuote(data, instrumentId, asOf);
  return (
    <div
      className="stock-header-quote"
      aria-label="观察日原始收盘报价"
      title={view.reason}
    >
      <div>
        <strong className={view.change == null ? "" : tone(view.change)}>
          {num(view.price)}
        </strong>
        <span className={view.change == null ? "muted" : tone(view.change)}>
          {view.change == null ? "涨幅待核验" : percent(view.change)}
        </span>
      </div>
      <small>原始收盘报价 · {view.label} · 未复权 · 非实时</small>
    </div>
  );
}
