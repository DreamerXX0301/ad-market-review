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
const retainedReadRecheckMs = 60_000;
const gainersWarmTtlMs = 90_000;
const gainersWarmMaxBytes = 512 * 1024;
const gainersWarmReads = new Map<
  string,
  { proof: string; value: Row; expires: number }
>();
let gainersWarmRevision = 0;

export function firstGainersReadPath(day: string) {
  return `/market/gainers?date=${day}&page=1&page_size=25&query=&sort_by=change_30d_pct&sort_order=desc&top_n=100`;
}

function clearGainersWarmReads() {
  gainersWarmRevision += 1;
  gainersWarmReads.clear();
}

export type ReadChange = {
  scope: string;
  date?: string;
};

const readChangeEvent = "workbench-read-changed";
const revisionScopes = new Set<string>();
const scopePaths: Record<string, RegExp> = {
  market:
    /^\/(?:bootstrap|market|instruments|watchlist|tracking|sector-tracking|strategies|sector-strategies|topics|topic-members|topic-hotspots|hot-universe|zodiac|data\/status)/,
  auction: /^\/market\/(?:auction(?:[/?-]|$)|intraday\/auction-reference)/,
  intraday: /^\/market\/intraday(?:[/?]|$)/,
  noon: /^\/market\/(?:noon(?:[/?-]|$)|intraday(?:[/?]|$))/,
  news: /^\/market\/news-events(?:[/?-]|$)/,
  calendar: /^\/market\/(?:noon|auction)\/dates(?:\?|$)/,
  supplement:
    /^\/(?:bootstrap|data\/status|market\/(?:overview|emotion|broken-boards|ladder|sectors|gainers|intraday|noon))(?:[/?-]|$)/,
  suspensions: /^\/market\/gainers(?:\?|$)/,
  dragon_tiger: /^\/market\/dragon-tiger(?:[/?-]|$)/,
  sector_charts:
    /^\/(?:market\/(?:sector-chart|sectors)|data\/status)(?:[/?]|$)/,
  news_calendar: /^\/market\/news-events\/calendar(?:\?|$)/,
  strategy:
    /^\/(?:strateg(?:ies|y-runs|y-batches)|sector-strateg(?:ies|y-runs))(?:[/?]|$)/,
  tracking:
    /^\/(?:tracking(?:-rounds|-events)?|sector-tracking(?:-rounds|-events)?|strategies|sector-strategies)(?:[/?]|$)/,
  research: /^\/research\/jobs(?:[/?]|$)/,
  backtest: /^\/backtests(?:[/?]|$)/,
  tasks: /^\/tasks(?:[/?]|$)/,
  topics:
    /^\/(?:topics|topic-members|stock-topic-memberships|topic-hotspots|hot-universe|watchlist|tracking|sector-tracking|market\/(?:overview|sectors|ladder|gainers))(?:[/?-]|$)/,
  sources: /^\/(?:sources|data\/status)(?:[/?]|$)/,
  zodiac: /^\/zodiac(?:[/?-]|$)/,
};

function matchesReadChange(path: string, { scope, date }: ReadChange) {
  if (!scopePaths[scope]?.test(path)) return false;
  const query = new URLSearchParams(path.split("?")[1]);
  const readDate = query.get("date") || query.get("as_of");
  return !date || !readDate || readDate === date;
}

export function observeReadScopes(scopes: string[]) {
  scopes.forEach((scope) => revisionScopes.add(scope));
}

function hasReadRevision(scope: string) {
  return revisionScopes.has(scope);
}

export function notifyReadChanges(changes: ReadChange[]) {
  if (!changes.length) return;
  if (
    changes.some(({ scope }) =>
      ["market", "supplement", "suspensions", "topics"].includes(scope),
    )
  )
    clearGainersWarmReads();
  for (const path of pendingReads.keys())
    if (changes.some((change) => matchesReadChange(path, change)))
      pendingReads.delete(path);
  if (typeof window !== "undefined")
    window.dispatchEvent(new CustomEvent(readChangeEvent, { detail: changes }));
}

export function subscribeReadChanges(
  listener: (changes: ReadChange[]) => void,
) {
  const receive = (event: Event) => listener((event as CustomEvent).detail);
  window.addEventListener(readChangeEvent, receive);
  return () => window.removeEventListener(readChangeEvent, receive);
}

function revisionCoversPath(path: string) {
  return (
    hasReadRevision("market") &&
    /^\/market\/(?:overview|indices|emotion|broken-boards|sectors|ladder|gainers|abnormal|dragon-tiger|auction|intraday|noon|news-events)(?:\?|$)/.test(
      path,
    )
  );
}

function savedMutationScope(path: string, method: string | undefined) {
  if (!method || method === "GET") return;
  const endpoint = path.split("?")[0];
  if (
    /^\/(?:sector-)?strategies(?:\/[^/]+(?:\/restore)?|\/bootstrap)?$/.test(
      endpoint,
    )
  )
    return "strategy";
  if (
    /^\/(?:sector-)?tracking-rounds(?:\/[^/]+\/(?:policy|remove))?$/.test(
      endpoint,
    )
  )
    return "tracking";
  if (/^\/backtests\/[^/]+(?:\/restore)?$/.test(endpoint)) return "backtest";
  if (/^\/sources\/[^/]+\/(?:configure|verify)$/.test(endpoint))
    return "sources";
  if (
    /^\/zodiac-(?:pools\/\d+\/members|pool-members\/[^/]+(?:\/end)?)$/.test(
      endpoint,
    )
  )
    return "zodiac";
}

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
    clearGainersWarmReads();
    pendingReads.clear();
    return readResponse(path, options).finally(() => {
      clearGainersWarmReads();
      pendingReads.clear();
    });
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
  const scope = savedMutationScope(path, options.method?.toUpperCase());
  if (scope) notifyReadChanges([{ scope }]);
  return data;
}
export const post = <T = any>(path: string, data: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
export const patch = <T = any>(path: string, data: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(data) });

async function gainersReadProof(signal: AbortSignal) {
  const receipt = await api<Row>("/tasks/activity", { signal });
  const revision = receipt?.read_revisions;
  const supplement = revision?.domains?.supplement;
  const suspensions = revision?.domains?.suspensions;
  if (
    receipt?.active_count !== 0 ||
    revision?.market_locked !== false ||
    typeof revision.market !== "string" ||
    (supplement !== null && typeof supplement !== "string") ||
    (suspensions !== null && typeof suspensions !== "string")
  )
    return null;
  return JSON.stringify([revision.market, supplement, suspensions]);
}

async function readGainersWarmBody(path: string, signal: AbortSignal) {
  const response = await fetch("/api/v1" + path, {
    signal,
    priority: "low",
    cache: "no-store",
  });
  if (!response.ok) {
    await response.body?.cancel();
    return null;
  }
  if (!response.body) return null;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let size = 0;
  let content = "";
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return JSON.parse(content + decoder.decode()) as Row;
      size += chunk.value.byteLength;
      if (size > gainersWarmMaxBytes) {
        await reader.cancel();
        return null;
      }
      content += decoder.decode(chunk.value, { stream: true });
    }
  } finally {
    reader.releaseLock();
  }
}

export async function prefetchGainersReads(
  days: string[],
  snapshotId: string,
  signal: AbortSignal,
  canContinue: () => boolean,
) {
  if (signal.aborted || !canContinue()) return;
  const revision = gainersWarmRevision;
  const proof = await gainersReadProof(signal);
  if (!proof) return;
  for (const day of days.slice(0, 2)) {
    if (signal.aborted || revision !== gainersWarmRevision || !canContinue())
      return;
    const path = firstGainersReadPath(day);
    const saved = gainersWarmReads.get(path);
    if (saved?.proof === proof && saved.expires > performance.now()) continue;
    const value = await readGainersWarmBody(path, signal);
    if (signal.aborted || revision !== gainersWarmRevision || !canContinue())
      return;
    if (
      value?.date !== day ||
      value.snapshot_id !== snapshotId ||
      !Array.isArray(value.items)
    )
      continue;
    gainersWarmReads.set(path, {
      proof,
      value,
      expires: performance.now() + gainersWarmTtlMs,
    });
    while (gainersWarmReads.size > 2)
      gainersWarmReads.delete(gainersWarmReads.keys().next().value!);
  }
}

async function readRetainedPage<T>(path: string, signal: AbortSignal) {
  const saved = gainersWarmReads.get(path);
  if (saved) {
    gainersWarmReads.delete(path);
    if (saved.expires > performance.now()) {
      const revision = gainersWarmRevision;
      let proof: string | null = null;
      try {
        proof = await gainersReadProof(signal);
      } catch (cause: any) {
        if (signal.aborted || cause?.incrementLocked) throw cause;
      }
      if (
        !signal.aborted &&
        revision === gainersWarmRevision &&
        proof === saved.proof
      )
        return saved.value as T;
    }
  }
  return api<T>(path, { signal });
}

export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const pageActivity = useContext(ApiPageActivity);
  const active = pageActivity !== false;
  const reuseOnReturn = pageActivity !== null;
  const [data, setData] = useState<T | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [errorStatus, setErrorStatus] = useState<number>(),
    [key, setKey] = useState(0);
  const previousPath = useRef<string | null>(null);
  const resolvedPath = useRef<string | null>(null);
  const incrementBlocked = useRef(false);
  const lastRead = useRef<{
    path: string;
    key: number;
    deps: unknown[];
    completedAt: number;
  } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const background = useRef(false);
  const reload = useCallback(() => {
    controller.current?.abort();
    background.current = false;
    if (path) {
      pendingReads.delete(path);
      gainersWarmReads.delete(path);
    }
    setKey((x) => x + 1);
  }, [path]);
  const invalidate = useCallback(() => {
    controller.current?.abort();
    if (path) {
      pendingReads.delete(path);
      gainersWarmReads.delete(path);
    }
    background.current = true;
    setKey((x) => x + 1);
  }, [path]);
  useEffect(() => {
    if (!active) {
      setLoading(false);
      return;
    }
    if (!path) {
      lastRead.current = null;
      setData(null);
      setLoading(false);
      setError("");
      setErrorStatus(undefined);
      return;
    }
    const previous = lastRead.current;
    const sameRead =
      reuseOnReturn &&
      previous?.path === path &&
      previous.key === key &&
      previous.deps.length === deps.length &&
      previous.deps.every((value, index) => Object.is(value, deps[index]));
    if (
      sameRead &&
      (revisionCoversPath(path) ||
        performance.now() - previous.completedAt < retainedReadRecheckMs)
    )
      return;
    const abort = new AbortController();
    controller.current = abort;
    setLoading(
      !(sameRead || (background.current && resolvedPath.current === path)),
    );
    background.current = false;
    setError("");
    setErrorStatus(undefined);
    if (previousPath.current !== path) setData(null);
    previousPath.current = path;
    readRetainedPage<T>(path, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) {
          incrementBlocked.current = false;
          resolvedPath.current = path;
          lastRead.current =
            value != null
              ? { path, key, deps: [...deps], completedAt: performance.now() }
              : null;
          setData(value);
        }
      })
      .catch((e) => {
        if (!abort.signal.aborted && e.name !== "AbortError") {
          lastRead.current = null;
          if (e.incrementLocked) incrementBlocked.current = true;
          setError(e.message);
          setErrorStatus(e.status);
        }
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [path, key, active, reuseOnReturn, ...deps]);
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
      if ((event as Event & { navigation?: boolean }).navigation) return;
      const route = (event as CustomEvent).detail;
      if (path && prefixes[route]?.some((prefix) => path.startsWith(prefix)))
        invalidate();
    };
    window.addEventListener("workbench-route", onRoute);
    const onTopicsChanged = () => {
      if (path && matchesReadChange(path, { scope: "topics" })) invalidate();
    };
    const stopReadChanges = subscribeReadChanges((changes) => {
      if (path && changes.some((change) => matchesReadChange(path, change)))
        invalidate();
    });
    const onIncrementUnlocked = () => {
      if (incrementBlocked.current) invalidate();
      incrementBlocked.current = false;
    };
    window.addEventListener(
      "workbench-increment-unlocked",
      onIncrementUnlocked,
    );
    window.addEventListener("workbench-topics-changed", onTopicsChanged);
    return () => {
      window.removeEventListener("workbench-route", onRoute);
      stopReadChanges();
      window.removeEventListener("workbench-topics-changed", onTopicsChanged);
      window.removeEventListener(
        "workbench-increment-unlocked",
        onIncrementUnlocked,
      );
    };
  }, [path, invalidate]);
  // A changed URL must never render the old date/security response for even one frame.
  return {
    data: resolvedPath.current === path ? data : null,
    loading: active && (loading || (!!path && previousPath.current !== path)),
    error: previousPath.current === path ? error : "",
    errorStatus: previousPath.current === path ? errorStatus : undefined,
    reload,
    setData,
  };
}
export const list = (value: any): Row[] =>
  Array.isArray(value) ? value : Array.isArray(value?.items) ? value.items : [];
export const requestKey = () => crypto.randomUUID();
