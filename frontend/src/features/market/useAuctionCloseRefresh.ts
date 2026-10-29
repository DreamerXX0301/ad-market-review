import { useEffect } from "react";
import { MARKET_INCREMENT_FINISHED } from "../../lib/workbenchEvents";

/** Re-read published prices only; collapsed/hidden readers refresh through useApi on return. */
export default function useAuctionCloseRefresh({
  active,
  open,
  hasSnapshot,
  reload,
}: {
  active: boolean;
  open: boolean;
  hasSnapshot: boolean;
  reload: () => void;
}) {
  useEffect(() => {
    if (!active || !open || !hasSnapshot) return;
    const refresh = () => reload();
    window.addEventListener(MARKET_INCREMENT_FINISHED, refresh);
    return () => window.removeEventListener(MARKET_INCREMENT_FINISHED, refresh);
  }, [active, open, hasSnapshot, reload]);
}
