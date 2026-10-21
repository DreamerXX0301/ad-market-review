import { useState } from "react";
import { Loader2, Star } from "lucide-react";
import { useToast } from "../../components/UI";
import {
  addWatchlistItem,
  removeWatchlistItem,
  useWatchlistMembership,
} from "./watchlistStore";
import type { WatchlistSource } from "./watchlistStore";
import "./Watchlist.css";

export default function WatchlistButton({
  instrumentId,
  compact = false,
  ...source
}: WatchlistSource & { instrumentId: string; compact?: boolean }) {
  const membership = useWatchlistMembership();
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const selected = membership.ids.has(instrumentId);
  const title = selected ? "移出自选" : "加入自选";
  return (
    <button
      className={`btn watchlist-button ${compact ? "small compact" : ""} ${selected ? "selected" : ""}`}
      type="button"
      disabled={busy || membership.loading}
      aria-pressed={selected}
      aria-label={title}
      title={membership.error || title}
      onClick={async (event) => {
        event.stopPropagation();
        setBusy(true);
        try {
          if (selected) {
            await removeWatchlistItem(instrumentId);
            toast("已移出自选，策略跟踪和划线提醒保留");
          } else {
            const result = await addWatchlistItem(instrumentId, source);
            toast(
              result.already_exists
                ? "已在自选中，保留原来源和事件基准"
                : "已加入自选",
            );
          }
        } catch (error: any) {
          toast(error.message, "error");
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? (
        <Loader2 size={15} className="spin" />
      ) : (
        <Star size={15} fill={selected ? "currentColor" : "none"} />
      )}
      {!compact && <span>{selected ? "已自选" : "加入自选"}</span>}
    </button>
  );
}
