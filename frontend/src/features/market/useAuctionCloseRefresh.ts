import { useEffect } from "react";
import { MARKET_INCREMENT_FINISHED } from "../../lib/workbenchEvents";

/** Re-read published prices only; useApi marks hidden readers stale until they are visible. */
export default function useAuctionCloseRefresh({
  open,
  hasSnapshot,
  reload,
}: {
  open: boolean;
  hasSnapshot: boolean;
  reload: () => void;
}) {
  useEffect(() => {
    if (!open || !hasSnapshot) return;
    const refresh = () => reload();
    window.addEventListener(MARKET_INCREMENT_FINISHED, refresh);
    return () => window.removeEventListener(MARKET_INCREMENT_FINISHED, refresh);
  }, [open, hasSnapshot, reload]);
}
