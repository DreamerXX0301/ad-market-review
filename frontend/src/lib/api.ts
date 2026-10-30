import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { restoreIncrementSession } from "../components/incrementSession";
export type Row = Record<string, any>;
// null is an application-level reader; retained pages explicitly provide
// visibility so their independent state survives without background GETs.
export const ApiPageActivity = createContext<boolean | null>(null);
export function usePageActive() {
  return useContext(ApiPageActivity) !== false;
}
type ResponseText = { ok: boolean; status: number; content: string };
type PendingRead = {
  controller: AbortController;
  promise: Promise<ResponseText>;
  users: number;
  settled: boolean;
};
const pendingReads = new Map<string, PendingRead>();
export function decodeChartColumns(value: any): any {
  if (
    !value ||
    typeof value !== "object" ||
    !Object.hasOwn(value, "bars_columnar")
  )
    return value;
  const { version, count, fields, columns, missing } =
    value.bars_columnar || {};
  const invalid = () => {
    throw new Error("图表数据格式不完整，请重新加载");
  };
  if (
    version !== "chart-bars-columns-v1" ||
    !Number.isInteger(count) ||
    count < 0 ||
    count > 100000 ||
    !Array.isArray(fields) ||
    fields.length > 512 ||
    new Set(fields).size !== fields.length ||
    fields.some((field: unknown) => typeof field !== "string") ||
    !Array.isArray(columns) ||
    columns.length !== fields.length ||
    !missing ||
    typeof missing !== "object" ||
    Array.isArray(missing)
  )
    return invalid();
  const bars: Row[] = Array.from({ length: count }, () => ({}));
  fields.forEach((field: string, index: number) => {
    const column = columns[index];
    const absent = Object.hasOwn(missing, field) ? missing[field] : [];
    if (
      !Array.isArray(column) ||
      column.length !== count ||
      !Array.isArray(absent) ||
      absent.some(
        (position: unknown) =>
          !Number.isInteger(position) ||
          (position as number) < 0 ||
          (position as number) >= count,
      )
    )
      return invalid();
    const skipped = new Set(absent);
    for (let i = 0; i < count; i++)
      if (!skipped.has(i)) {
        if (field === "__proto__")
          Object.defineProperty(bars[i], field, {
            value: column[i],
            enumerable: true,
            writable: true,
            configurable: true,
          });
        else bars[i][field] = column[i];
      }
  });
  const { bars_columnar: _encoded, ...metadata } = value;
  return { ...metadata, bars };
}
async function readResponse(path: string, options: RequestInit) {
  const chart =
    /^\/(?:market\/sector-chart|instruments\/[^/?]+\/chart)(?:\?|$)/.test(path);
  const transportPath =
    chart && !/[?&]encoding=/.test(path)
      ? path + (path.includes("?") ? "&" : "?") + "encoding=columns-v1"
      : path;
  const response = await fetch("/api/v1" + transportPath, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  return {
    ok: response.ok,
    status: response.status,
    content: await response.text(),
  };
}
function responseText(
  path: string,
  options: RequestInit,
): Promise<ResponseText> {
  // Share only concurrent default GETs. Each consumer parses its own value;
  // no completed responses, policies or task states are cached here.
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET") {
    pendingReads.clear();
    return readResponse(path, options).finally(() => pendingReads.clear());
  }
  if (
    Object.keys(options).some((key) => key !== "signal") ||
    pendingReads.size >= 64
  )
    return readResponse(path, options);
  const signal = options.signal;
  if (signal?.aborted)
    return Promise.reject(new DOMException("Aborted", "AbortError"));
  let entry = pendingReads.get(path);
  if (!entry) {
    const controller = new AbortController();
    entry = {
      controller,
      users: 0,
      settled: false,
      promise: readResponse(path, { signal: controller.signal }),
    };
    const owned = entry;
    entry.promise = entry.promise.finally(() => {
      owned.settled = true;
      if (pendingReads.get(path) === owned) pendingReads.delete(path);
    });
    pendingReads.set(path, entry);
  }
  const owned = entry;
  owned.users++;
  return new Promise((resolve, reject) => {
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      signal?.removeEventListener("abort", abort);
      if (--owned.users === 0 && !owned.settled) {
        if (pendingReads.get(path) === owned) pendingReads.delete(path);
        owned.controller.abort();
      }
    };
    const abort = () => {
      release();
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
    owned.promise.then(
      (value) => {
        if (!released) {
          release();
          resolve(value);
        }
      },
      (error) => {
        if (!released) {
          release();
          reject(error);
        }
      },
    );
  });
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await responseText(path, options);
  const content = response.content;
  let data: any;
  try {
    data = content ? decodeChartColumns(JSON.parse(content)) : null;
  } catch {
    throw Object.assign(
      new Error(
        response.ok
          ? "服务器返回了无法读取的响应"
          : "服务暂时不可用，请确认后端已启动",
      ),
      { status: response.status },
    );
  }
  if (!response.ok) {
    const d = data?.detail;
    if (d?.increment_session) restoreIncrementSession(d.increment_session);
    throw Object.assign(
      new Error(
        typeof d === "string"
          ? d
          : typeof d?.message === "string"
            ? d.message
            : Array.isArray(d)
              ? d.map((x: any) => x.msg || JSON.stringify(x)).join("；")
              : JSON.stringify(d || data || "请求失败"),
      ),
      { status: response.status, incrementLocked: !!d?.increment_session },
    );
  }
  return data;
}
export const post = <T = any>(path: string, data: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
export const patch = <T = any>(path: string, data: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(data) });
export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const pageActivity = useContext(ApiPageActivity);
  const active = pageActivity !== false;
  const [data, setData] = useState<T | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [key, setKey] = useState(0);
  const previousPath = useRef<string | null>(null);
  const resolvedPath = useRef<string | null>(null);
  const incrementBlocked = useRef(false);
  const reload = useCallback(() => {
    if (path) pendingReads.delete(path);
    setKey((x) => x + 1);
  }, [path]);
  useEffect(() => {
    if (!active) {
      setLoading(false);
      return;
    }
    if (!path) {
      setData(null);
      setLoading(false);
      setError("");
      return;
    }
    const abort = new AbortController();
    setLoading(true);
    setError("");
    if (previousPath.current !== path) setData(null);
    previousPath.current = path;
    api<T>(path, { signal: abort.signal })
      .then((value) => {
        if (!abort.signal.aborted) {
          incrementBlocked.current = false;
          resolvedPath.current = path;
          setData(value);
        }
      })
      .catch((e) => {
        if (!abort.signal.aborted && e.name !== "AbortError") {
          if (e.incrementLocked) incrementBlocked.current = true;
          setError(e.message);
        }
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [path, key, active, ...deps]);
  useEffect(() => {
    const prefixes: Record<string, string[]> = {
      topics: ["/topics", "/topic-members", "/bootstrap"],
      strategy: ["/strateg", "/sector-strateg", "/topics", "/topic-hotspots"],
      tracking: [
        "/tracking",
        "/strategies",
        "/sector-tracking",
        "/sector-strateg",
        "/topics",
        "/topic-hotspots",
      ],
      backtest: ["/backtests", "/strategies"],
      data: ["/data", "/tasks"],
      sources: ["/sources"],
      research: ["/research"],
    };
    const onRoute = (event: Event) => {
      if (!active) return;
      // Activation already starts a fresh read. Keep route notifications for
      // task completion/unlock, without aborting and restarting that read.
      if (
        pageActivity !== null &&
        (event as Event & { navigation?: boolean }).navigation
      )
        return;
      const route = (event as CustomEvent).detail;
      if (path && prefixes[route]?.some((prefix) => path.startsWith(prefix)))
        reload();
    };
    window.addEventListener("workbench-route", onRoute);
    const onTopicsChanged = () => {
      if (
        active &&
        path &&
        [
          "/topics",
          "/topic-members",
          "/stock-topic-memberships",
          "/topic-hotspots",
          "/bootstrap",
          "/watchlist",
          "/tracking",
        ].some((prefix) => path.startsWith(prefix))
      )
        reload();
    };
    const onIncrementUnlocked = () => {
      if (incrementBlocked.current) {
        incrementBlocked.current = false;
        if (active) reload();
      }
    };
    window.addEventListener(
      "workbench-increment-unlocked",
      onIncrementUnlocked,
    );
    window.addEventListener("workbench-topics-changed", onTopicsChanged);
    return () => {
      window.removeEventListener("workbench-route", onRoute);
      window.removeEventListener("workbench-topics-changed", onTopicsChanged);
      window.removeEventListener(
        "workbench-increment-unlocked",
        onIncrementUnlocked,
      );
    };
  }, [path, reload, active, pageActivity]);
  // A changed URL must never render the old date/security response for even one frame.
  return {
    data: resolvedPath.current === path ? data : null,
    loading: active && (loading || (!!path && previousPath.current !== path)),
    error: previousPath.current === path ? error : "",
    reload,
    setData,
  };
}
export const list = (value: any): Row[] =>
  Array.isArray(value) ? value : Array.isArray(value?.items) ? value.items : [];
export const requestKey = () => crypto.randomUUID();
