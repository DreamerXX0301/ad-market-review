import { useSyncExternalStore } from "react";
import { createChartPreferenceStore } from "./chartPreferences";

let storage: Storage | null = null;
try {
  if (typeof window !== "undefined") storage = window.localStorage;
} catch {
  /* Restricted storage still supports synchronized preferences in memory. */
}
const store = createChartPreferenceStore(storage);
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    store.receiveStorageEvent(event);
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
