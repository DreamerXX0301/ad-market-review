import { useCallback, useEffect, useRef, useState } from "react";
export type Row = Record<string, any>;
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const content = await response.text();
  let data: any;
  try {
    data = content ? JSON.parse(content) : null;
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
      { status: response.status },
    );
  }
  return data;
}
export const post = <T = any>(path: string, data: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
export const patch = <T = any>(path: string, data: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(data) });
export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [key, setKey] = useState(0);
  const previousPath = useRef<string | null>(null);
  const resolvedPath = useRef<string | null>(null);
  const reload = useCallback(() => setKey((x) => x + 1), []);
  useEffect(() => {
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
          resolvedPath.current = path;
          setData(value);
        }
      })
      .catch((e) => {
        if (!abort.signal.aborted && e.name !== "AbortError")
          setError(e.message);
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [path, key, ...deps]);
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
      const route = (event as CustomEvent).detail;
      if (path && prefixes[route]?.some((prefix) => path.startsWith(prefix)))
        reload();
    };
    window.addEventListener("workbench-route", onRoute);
    const onTopicsChanged = () => {
      if (
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
    window.addEventListener("workbench-topics-changed", onTopicsChanged);
    return () => {
      window.removeEventListener("workbench-route", onRoute);
      window.removeEventListener("workbench-topics-changed", onTopicsChanged);
    };
  }, [path, reload]);
  // A changed URL must never render the old date/security response for even one frame.
  return {
    data: resolvedPath.current === path ? data : null,
    loading: loading || (!!path && previousPath.current !== path),
    error: previousPath.current === path ? error : "",
    reload,
    setData,
  };
}
export const list = (value: any): Row[] =>
  Array.isArray(value) ? value : Array.isArray(value?.items) ? value.items : [];
export const requestKey = () => crypto.randomUUID();
