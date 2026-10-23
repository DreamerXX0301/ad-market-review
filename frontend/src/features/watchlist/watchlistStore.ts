import { useEffect, useSyncExternalStore } from "react";
import { api, post } from "../../lib/api";
import { LINE_ALERT_CHECK_FINISHED } from "../../lib/workbenchEvents";

export const WATCHLIST_CHANGED = "workbench-watchlist-changed";
export type WatchlistSource = {
  trackingRoundId?: string;
  sourceKind?: "stock" | "tracking" | "sector" | "topic";
  sourceId?: string;
  sourceName?: string;
  observedDate?: string;
};
type MembershipState = {
  ids: ReadonlySet<string>;
  loaded: boolean;
  loading: boolean;
  error: string;
};
let state: MembershipState = {
  ids: new Set(),
  loaded: false,
  loading: false,
  error: "",
};
const listeners = new Set<() => void>();
let inflight: Promise<void> | null = null;
let revision = 0;
let refreshingCompletion = false;
let completionAgain = false;
async function refreshAfterLineCheck() {
  completionAgain = true;
  if (refreshingCompletion) return;
  refreshingCompletion = true;
  try {
    while (completionAgain) {
      // A pre-completion request may still return the earlier ID list. Read
      // again after it settles instead of treating that response as fresh.
      if (inflight) await inflight;
      completionAgain = false;
      const started = revision;
      await refreshWatchlistMembership();
      if (revision !== started) completionAgain = true;
    }
  } finally {
    refreshingCompletion = false;
    window.dispatchEvent(new Event(WATCHLIST_CHANGED));
  }
}
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  if (listeners.size === 1) {
    window.addEventListener(LINE_ALERT_CHECK_FINISHED, refreshAfterLineCheck);
    // Changes can happen while no stock detail or star button is mounted.
    if (state.loaded) void refreshWatchlistMembership();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size)
      window.removeEventListener(
        LINE_ALERT_CHECK_FINISHED,
        refreshAfterLineCheck,
      );
  };
};
const snapshot = () => state;
function publish(next: MembershipState) {
  state = next;
  listeners.forEach((listener) => listener());
}
export function refreshWatchlistMembership() {
  if (inflight) return inflight;
  const started = revision;
  publish({ ...state, loading: true, error: "" });
  inflight = api<{ instrument_ids: string[] }>("/watchlist/ids")
    .then((data) => {
      if (revision === started)
        publish({
          ids: new Set(data.instrument_ids),
          loaded: true,
          loading: false,
          error: "",
        });
    })
    .catch((error: Error) => {
      publish({ ...state, loading: false, error: error.message });
    })
    .finally(() => {
      inflight = null;
      if (state.loading) publish({ ...state, loading: false });
    });
  return inflight;
}
function changed(instrumentId: string, selected: boolean) {
  revision += 1;
  const ids = new Set(state.ids);
  if (selected) ids.add(instrumentId);
  else ids.delete(instrumentId);
  publish({ ...state, ids, error: "" });
  window.dispatchEvent(new Event(WATCHLIST_CHANGED));
}
export async function addWatchlistItem(
  instrumentId: string,
  source: WatchlistSource = {},
) {
  const result = await post("/watchlist", {
    instrument_id: instrumentId,
    tracking_round_id: source.trackingRoundId,
    source_kind: source.sourceKind,
    source_id: source.sourceId,
    source_name: source.sourceName,
    observed_date: source.observedDate,
  });
  changed(instrumentId, true);
  return result;
}
export async function removeWatchlistItem(instrumentId: string) {
  await api("/watchlist/" + encodeURIComponent(instrumentId), {
    method: "DELETE",
  });
  changed(instrumentId, false);
}
export function useWatchlistMembership(enabled = true) {
  const value = useSyncExternalStore(subscribe, snapshot);
  useEffect(() => {
    if (enabled && !state.loaded && !state.loading)
      void refreshWatchlistMembership();
  }, [enabled]);
  return value;
}
