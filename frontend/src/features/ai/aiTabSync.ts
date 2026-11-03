const channelName = "ad-market-review:ai-changes:v1";

export type AIChange =
  | { scope: "providers" }
  | { scope: "session"; id: string }
  | { scope: "task"; id: string };

function openChannel() {
  if (typeof window === "undefined" || !window.BroadcastChannel) return;
  try {
    return new window.BroadcastChannel(channelName);
  } catch {
    // Visibility and focus refresh remain available.
  }
}

// Only identifiers travel between tabs. API reads resolve the actual state.
export function publishAIChange(change: AIChange) {
  const channel = openChannel();
  if (!channel) return;
  try {
    channel.postMessage(change);
  } catch {
    // A notification failure cannot turn an accepted write into a failed one.
  } finally {
    channel.close();
  }
}

export function subscribeAIChanges(
  onChange: (change: AIChange) => void,
  onReturn: () => void,
  onPoll: () => void,
) {
  const channel = openChannel();
  function receive(event: MessageEvent) {
    const value = event.data;
    if (value?.scope === "providers") onChange({ scope: "providers" });
    if (
      (value?.scope === "session" || value?.scope === "task") &&
      typeof value.id === "string" &&
      value.id.length > 0 &&
      value.id.length <= 200
    )
      onChange({ scope: value.scope, id: value.id });
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  function returned() {
    if (document.visibilityState === "hidden" || timer !== undefined) return;
    timer = setTimeout(() => {
      timer = undefined;
      onReturn();
    }, 0);
  }
  const poll = setInterval(() => {
    if (document.visibilityState === "visible") onPoll();
  }, 10000);
  channel?.addEventListener("message", receive);
  window.addEventListener("focus", returned);
  document.addEventListener("visibilitychange", returned);
  return () => {
    channel?.close();
    window.removeEventListener("focus", returned);
    document.removeEventListener("visibilitychange", returned);
    clearTimeout(timer);
    clearInterval(poll);
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
