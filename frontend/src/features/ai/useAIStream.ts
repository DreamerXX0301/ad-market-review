import { useCallback, useEffect, useRef, useState } from "react";
import { usePageActive } from "../../lib/api";
import {
  applyStreamPacket,
  emptyStream,
  readTaskEvents,
  streamKey,
} from "./aiStream";
import type { AIStreamState } from "./aiStream";

export function useAIStream(
  taskId: string,
  sessionId: string,
  enabled: boolean,
) {
  const active = usePageActive();
  const key = streamKey(taskId, sessionId);
  const [snapshot, setSnapshot] = useState<AIStreamState>(() => emptyStream());
  const [retry, setRetry] = useState(0);
  const current = useRef<AIStreamState>(emptyStream());
  const generation = useRef(0);
  const reconnect = useCallback(() => setRetry((value) => value + 1), []);
  useEffect(() => {
    if (current.current.key !== key) {
      current.current = emptyStream(taskId, sessionId);
      setSnapshot(current.current);
    }
    if (!key || !enabled || !active || current.current.finished) return;
    const run = ++generation.current;
    let stopped = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const valid = () =>
      !stopped && generation.current === run && current.current.key === key;
    const commit = (value: AIStreamState) => {
      if (valid()) {
        current.current = value;
        setSnapshot(value);
      }
    };
    async function connect() {
      if (!valid() || current.current.finished) return;
      controller = new AbortController();
      commit({
        ...current.current,
        connection: failures ? "reconnecting" : "connecting",
        error: "",
      });
      let retryable = true;
      try {
        await readTaskEvents(
          taskId,
          current.current.seq,
          controller.signal,
          (packet) => {
            if (!valid()) return false;
            const next = applyStreamPacket(current.current, packet);
            if (next !== current.current) {
              failures = 0;
              commit(next);
            }
            return !current.current.finished;
          },
        );
      } catch (error: any) {
        if (!valid() || controller.signal.aborted) return;
        retryable = error?.retryable !== false;
        commit({
          ...current.current,
          connection: retryable ? "reconnecting" : "error",
          error: retryable
            ? "连接暂时中断，正在继续接收…"
            : "暂时无法继续接收，可重新连接或从历史打开",
        });
      }
      if (!valid() || current.current.finished || !retryable) return;
      failures += 1;
      commit({
        ...current.current,
        connection: "reconnecting",
        error: "连接暂时中断，正在继续接收…",
      });
      timer = setTimeout(
        connect,
        Math.min(8000, 750 * 2 ** Math.min(failures - 1, 4)),
      );
    }
    void connect();
    return () => {
      stopped = true;
      generation.current += 1;
      controller?.abort();
      if (timer) clearTimeout(timer);
    };
  }, [key, taskId, sessionId, enabled, active, retry]);
  return {
    ...(snapshot.key === key ? snapshot : emptyStream(taskId, sessionId)),
    reconnect,
  };
}
