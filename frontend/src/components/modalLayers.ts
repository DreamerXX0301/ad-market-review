type Layer = { id: string; parent: string | null };

export function createModalLayers() {
  const layers: Layer[] = [];
  const listeners = new Set<() => void>();
  const eventLayers = new WeakMap<object, string | null>();
  const top = () => layers.at(-1)?.id ?? null;
  const notify = () => listeners.forEach((listener) => listener());
  function descendsFrom(layer: Layer, parent: string) {
    const seen = new Set<string>();
    let current = layer.parent;
    while (current && !seen.has(current)) {
      if (current === parent) return true;
      seen.add(current);
      current = layers.find((entry) => entry.id === current)?.parent ?? null;
    }
    return false;
  }
  return {
    top,
    has: (id: string) => layers.some((layer) => layer.id === id),
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    register(id: string, parent: string | null = null) {
      const existing = layers.findIndex((layer) => layer.id === id);
      if (existing >= 0) layers.splice(existing, 1);
      const entry = { id, parent };
      // React may mount a nested child's passive effect before its parent.
      // A parent must still sit below every already registered descendant.
      const descendant = layers.findIndex((layer) => descendsFrom(layer, id));
      layers.splice(descendant < 0 ? layers.length : descendant, 0, entry);
      notify();
      return () => {
        const index = layers.indexOf(entry);
        if (index >= 0) {
          layers.splice(index, 1);
          notify();
        }
      };
    },
    isTopForEvent(id: string, event: object) {
      // Removing a child synchronously must not make the same Escape eligible
      // to close its parent later in that event's listener sequence.
      if (!eventLayers.has(event)) eventLayers.set(event, top());
      return eventLayers.get(event) === id;
    },
  };
}

export function canRestoreModalFocus(
  target: HTMLElement | null,
  activeLayer: HTMLElement | null = null,
) {
  return (
    !!target &&
    target.isConnected &&
    typeof target.focus === "function" &&
    !target.closest('[inert], [aria-hidden="true"]') &&
    target.getClientRects().length > 0 &&
    (!activeLayer || activeLayer.contains(target))
  );
}
