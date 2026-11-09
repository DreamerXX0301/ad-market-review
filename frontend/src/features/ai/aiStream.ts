import type { AIItem } from "./aiTypes";

export interface AIStreamPacket {
  id: string;
  event: string;
  data: AIItem;
}
export interface AIStreamState {
  key: string;
  taskId: string;
  sessionId: string;
  seq: number;
  text: string;
  status: string;
  tools: AIItem[];
  finished: boolean;
  terminalState: string;
  terminalReason: string;
  connection:
    | "idle"
    | "connecting"
    | "open"
    | "reconnecting"
    | "paused"
    | "done"
    | "error";
  error: string;
}
export const streamKey = (taskId: string, sessionId: string) =>
  taskId && sessionId ? `${taskId}:${sessionId}` : "";
export const emptyStream = (taskId = "", sessionId = ""): AIStreamState => ({
  key: streamKey(taskId, sessionId),
  taskId,
  sessionId,
  seq: 0,
  text: "",
  status: "正在准备回答…",
  tools: [],
  finished: false,
  terminalState: "",
  terminalReason: "",
  connection: "idle",
  error: "",
});

export function createSSEDecoder(onPacket: (packet: AIStreamPacket) => void) {
  let buffer = "";
  return {
    push(chunk: string) {
      buffer += chunk;
      if (buffer.length > 1024 * 1024)
        throw new Error("暂时无法读取回答，请重新打开对话");
      while (true) {
        const boundary = /\r?\n\r?\n/.exec(buffer);
        if (!boundary) break;
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        let id = "",
          event = "message";
        const lines: string[] = [];
        for (const line of frame.split(/\r?\n/)) {
          if (!line || line.startsWith(":")) continue;
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          const value =
            colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
          if (field === "id") id = value;
          else if (field === "event") event = value;
          else if (field === "data") lines.push(value);
        }
        if (!lines.length) continue;
        let data: AIItem;
        try {
          data = JSON.parse(lines.join("\n"));
        } catch {
          throw new Error("暂时无法读取回答，请重新打开对话");
        }
        if (data && typeof data === "object" && !Array.isArray(data))
          onPacket({ id, event, data });
      }
    },
  };
}

export function friendlyToolLabel(tool: AIItem) {
  if (typeof tool.label === "string" && tool.label) return tool.label;
  const name = typeof tool.name === "string" ? tool.name.toLowerCase() : "";
  if (/auction/.test(name)) return "查询竞价资料";
  if (/noon|midday/.test(name)) return "查询午间资料";
  if (/web|search/.test(name)) return "查询相关资料";
  if (/report|financial|company|stock/.test(name)) return "查询公司资料";
  if (/market|sector|index/.test(name)) return "查询市场资料";
  return "查询资料";
}

export function applyStreamPacket(
  previous: AIStreamState,
  packet: AIStreamPacket,
): AIStreamState {
  const event = packet.data;
  const type = packet.event === "message" ? event.type : packet.event;
  if (
    event.task_id !== previous.taskId ||
    event.session_id !== previous.sessionId ||
    event.type !== type
  )
    return previous;
  if (!Number.isSafeInteger(event.seq) || event.seq < 0 || event.seq > 1000000)
    return previous;
  const id = packet.id ? Number(packet.id) : event.seq;
  if (
    !Number.isSafeInteger(id) ||
    (id !== event.seq && !(type === "done" && event.seq === 0 && id === 1))
  )
    return previous;
  if (previous.finished || (type !== "done" && event.seq <= previous.seq))
    return previous;
  const next = {
    ...previous,
    seq: Math.max(previous.seq, event.seq),
    connection: "open" as const,
    error: "",
  };
  if (type === "delta") {
    if (typeof event.text !== "string") return previous;
    if (previous.text.length + event.text.length > 256 * 1024)
      throw new Error("回答暂时无法显示，完成后可从历史重新打开");
    return { ...next, text: previous.text + event.text, status: "正在回答…" };
  }
  if (type === "tool") {
    const tool = {
      name: event.name,
      call_id: event.call_id,
      state: event.state,
      label: friendlyToolLabel(event),
      date: typeof event.date === "string" ? event.date : undefined,
      summary: typeof event.summary === "string" ? event.summary : undefined,
    };
    const tools = [...previous.tools];
    const index = tools.findIndex(
      (item) => item.call_id && item.call_id === tool.call_id,
    );
    if (index >= 0) tools[index] = tool;
    else if (tools.length < 64) tools.push(tool);
    return {
      ...next,
      tools,
      status:
        event.state === "running"
          ? tool.label
          : previous.text
            ? "正在回答…"
            : "正在整理回答…",
    };
  }
  if (type === "status")
    return {
      ...next,
      status:
        typeof event.message === "string" && event.message
          ? event.message
          : event.state === "queued"
            ? "正在等待回答…"
            : "正在回答…",
    };
  if (type === "done")
    return {
      ...next,
      finished: true,
      terminalState: typeof event.state === "string" ? event.state : "failed",
      connection: "done",
      status:
        event.state === "completed"
          ? ""
          : event.state === "cancelled"
            ? "已停止"
            : "回答未完成",
    };
  if (type === "error")
    return {
      ...next,
      terminalReason:
        typeof event.reason_code === "string" ? event.reason_code : "",
      status: "正在确认回答状态…",
    };
  return previous;
}

export async function readTaskEvents(
  taskId: string,
  after: number,
  signal: AbortSignal,
  onPacket: (packet: AIStreamPacket) => boolean | void,
) {
  const response = await fetch(
    `/api/v1/ai/tasks/${encodeURIComponent(taskId)}/events?after=${after}`,
    {
      method: "GET",
      headers: { Accept: "text/event-stream" },
      cache: "no-store",
      signal,
    },
  );
  if (!response.ok || !response.body)
    throw Object.assign(new Error("暂时无法连接回答"), {
      retryable: response.status >= 500,
    });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let ended = false;
  const parser = createSSEDecoder((packet) => {
    if (ended || signal.aborted) return;
    if (
      packet.event === "error" &&
      packet.data.task_id === taskId &&
      packet.data.seq == null
    )
      throw Object.assign(new Error("这次回答已无法读取，请从历史重新打开"), {
        retryable: false,
      });
    if (onPacket(packet) === false) ended = true;
  });
  try {
    while (!ended && !signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}
