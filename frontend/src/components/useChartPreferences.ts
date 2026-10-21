import { useSyncExternalStore } from "react";
import {
  CHART_PREFERENCES_KEY,
  createChartPreferenceStore,
} from "./chartPreferences";

let storage: Storage | null = null;
try {
  if (typeof window !== "undefined") storage = window.localStorage;
} catch {
  /* Restricted storage still supports synchronized preferences in memory. */
}
const store = createChartPreferenceStore(storage);
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (
      (event.key === CHART_PREFERENCES_KEY || event.key === null) &&
      (!event.storageArea || event.storageArea === storage)
    ) {
      store.receiveStorage(event.key === null ? null : event.newValue);
    }
  });
}

export function useChartPreferences() {
  const snapshot = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
  return { ...snapshot, update: store.update, reset: store.reset };
}
