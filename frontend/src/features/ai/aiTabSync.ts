const channelName = "ad-market-review:ai-changes:v1";

export type AIChange =
  | { scope: "providers" }
  | { scope: "session"; id: string; usageChanged?: true }
  | { scope: "task"; id: string };

function openChannel() {
  if (typeof window === "undefined" || !window.BroadcastChannel) return;
  try {
    return new window.BroadcastChannel(channelName);
  } catch {
    // Fall back to a same-origin storage notification.
  }
}

// Only identifiers travel between tabs. API reads resolve the actual state.
export function publishAIChange(change: AIChange) {
  const channel = openChannel();
  if (!channel) {
    try {
      window.localStorage.setItem(channelName, JSON.stringify(change));
      window.localStorage.removeItem(channelName);
    } catch {
      // Some browser profiles disable both notification transports.
    }
    return;
  }
  try {
    channel.postMessage(change);
  } catch {
    // A notification failure cannot turn an accepted write into a failed one.
  } finally {
    channel.close();
  }
}

export function subscribeAIChanges(onChange: (change: AIChange) => void) {
  if (typeof window === "undefined") return () => {};
  const channel = openChannel();
  function receiveChange(value: any) {
    if (value?.scope === "providers") onChange({ scope: "providers" });
    if (
      (value?.scope === "session" || value?.scope === "task") &&
      typeof value.id === "string" &&
      value.id.length > 0 &&
      value.id.length <= 200
    ) {
      if (value.scope === "session" && value.usageChanged === true)
        onChange({ scope: "session", id: value.id, usageChanged: true });
      else onChange({ scope: value.scope, id: value.id });
    }
  }
  if (channel) {
    channel.addEventListener("message", (event) => receiveChange(event.data));
    return () => channel.close();
  }
  function storage(event: StorageEvent) {
    if (
      event.key !== channelName ||
      !event.newValue ||
      event.newValue.length > 512
    )
      return;
    try {
      receiveChange(JSON.parse(event.newValue));
    } catch {
      // Ignore unrelated or malformed storage writes.
    }
  }
  window.addEventListener("storage", storage);
  return () => {
    window.removeEventListener("storage", storage);
  };
}

export function aiChangeAfterWrite(
  path: string,
  method: string,
  result: { session_id?: string } | null,
): AIChange | undefined {
  if (path.startsWith("/providers/")) return { scope: "providers" };
  if (path === "/sessions" && method === "POST" && result?.session_id)
    return { scope: "session", id: result.session_id };
  const session = /^\/sessions\/([^/?]+)(?:\/messages)?$/.exec(path);
  if (session) return { scope: "session", id: session[1] };
  const task = /^\/tasks\/([^/?]+)\/cancel$/.exec(path);
  if (task) return { scope: "task", id: task[1] };
}
