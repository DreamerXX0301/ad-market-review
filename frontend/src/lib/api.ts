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
const historicalWarmTtlMs = 90_000;
const historicalWarmMaxEntries = 12;
const historicalWarmMaxBytes = 32 * 1024 * 1024;
type HistoricalBody = { chunks: Uint8Array[]; bytes: number };
const historicalWarmReads = new Map<
  string,
  {
    proof: string;
    body: HistoricalBody;
    snapshotId: string;
    heavy: boolean;
    sameDay: boolean;
    expires: number;
  }
>();
const sameDayAttempts = new Map<string, { proof: string; expires: number }>();
let historicalWarmRevision = 0;
let historicalPrefetchRunning = false;

export function firstGainersReadPath(day: string) {
  return `/market/gainers?date=${day}&page=1&page_size=25&query=&sort_by=change_30d_pct&sort_order=desc&top_n=100`;
}

function clearHistoricalWarmReads() {
  historicalWarmRevision += 1;
  historicalWarmReads.clear();
  sameDayAttempts.clear();
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
  suspensions: /^\/market\/(?:gainers|overview|auction|noon|intraday)(?:\?|$)/,
  historical_reference:
    /^\/market\/(?:overview|emotion|ladder|sectors|abnormal|gainers|intraday|noon)(?:\?|$)/,
  archive_controls: /^\/market\/(?:auction|noon|intraday)(?:\?|$)/,
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
      [
        "market",
        "supplement",
        "suspensions",
        "topics",
        "historical_reference",
        "archive_controls",
        "dragon_tiger",
        "auction",
        "noon",
        "intraday",
        "news",
        "calendar",
        "tasks",
      ].includes(scope),
    )
  )
    clearHistoricalWarmReads();
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
    clearHistoricalWarmReads();
    pendingReads.clear();
    return readResponse(path, options).finally(() => {
      clearHistoricalWarmReads();
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

const KiB = 1024;
const marketHistory = [
  "supplement",
  "suspensions",
  "historical_reference",
] as const;
const archiveControls = ["tasks", "archive_controls"] as const;
const historicalPlans = {
  overview: { bytes: 512 * KiB, days: 2, domains: marketHistory },
  indices: { bytes: 128 * KiB, days: 2, domains: [] },
  gainers: { bytes: 4096 * KiB, days: 1, domains: marketHistory },
  emotion: { bytes: 1024 * KiB, days: 1, domains: marketHistory },
  ladder: { bytes: 2048 * KiB, days: 1, domains: marketHistory },
  sectors: { bytes: 5120 * KiB, days: 1, domains: marketHistory },
  abnormal: { bytes: 128 * KiB, days: 2, domains: marketHistory },
  "dragon-tiger": { bytes: 512 * KiB, days: 2, domains: ["dragon_tiger"] },
  "news-events": { bytes: 512 * KiB, days: 2, domains: [], archive: "news" },
  auction: {
    bytes: 512 * KiB,
    days: 2,
    domains: ["suspensions", ...archiveControls],
    archive: "auction",
  },
  noon: {
    bytes: 10240 * KiB,
    days: 1,
    domains: [...marketHistory, ...archiveControls],
    archive: "noon",
  },
  intraday: {
    bytes: 12288 * KiB,
    days: 1,
    domains: [...marketHistory, ...archiveControls],
    archive: "intraday",
  },
} as const;

type HistoricalKind = keyof typeof historicalPlans;
type HistoricalTarget = {
  day: string;
  kind: HistoricalKind;
  monitorScope?: "historical" | "latest";
};

function historicalPath({ day, kind, monitorScope }: HistoricalTarget) {
  if (kind === "gainers") return firstGainersReadPath(day);
  if (kind === "abnormal")
    return `/market/abnormal?monitor_scope=${monitorScope || "historical"}&limit=1000&date=${day}`;
  const limit = ["emotion", "ladder", "sectors"].includes(kind)
    ? "&limit=1000"
    : "";
  return `/market/${kind}?date=${day}${limit}`;
}

function historicalTarget(path: string): HistoricalTarget | null {
  const [endpoint, query] = path.split("?");
  const kind = endpoint.slice("/market/".length) as HistoricalKind;
  if (!endpoint.startsWith("/market/") || !Object.hasOwn(historicalPlans, kind))
    return null;
  const parameters = new URLSearchParams(query);
  const day = parameters.get("date");
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const monitorScope =
    kind === "abnormal" && parameters.get("monitor_scope") === "latest"
      ? "latest"
      : undefined;
  const target: HistoricalTarget = { day, kind, monitorScope };
  return path === historicalPath(target) ? target : null;
}

function readMarketProof({ day, kind }: HistoricalTarget, receipt: Row) {
  const plan = historicalPlans[kind];
  const revision = receipt?.read_revisions;
  if (!marketReceiptReady(receipt, day)) return null;
  const domains = plan.domains.map((scope) => revision.domains?.[scope]);
  if (
    domains.some((value, index) => {
      const scope = plan.domains[index];
      return (
        typeof value !== "string" &&
        (value !== null || !["supplement", "suspensions"].includes(scope))
      );
    })
  )
    return null;
  const dateRevisions = revision.dates?.[day];
  const archiveRevision =
    "archive" in plan ? dateRevisions?.[plan.archive] : null;
  if ("archive" in plan && typeof archiveRevision !== "string") return null;
  const anchors =
    kind === "intraday" ? [dateRevisions?.auction, dateRevisions?.noon] : [];
  if (anchors.some((value) => value !== null && typeof value !== "string"))
    return null;
  let clockBucket: number | null = null;
  let currentWindow: string | null = null;
  if (day === revision.today && "archive" in plan) {
    if (!Number.isFinite(receipt.observed_at_ms) || receipt.observed_at_ms <= 0)
      return null;
    const window = revision.current_windows?.[plan.archive];
    if (typeof window !== "string") return null;
    currentWindow = window;
    clockBucket = Math.floor(receipt.observed_at_ms / 60_000);
  }
  return JSON.stringify([
    revision.market,
    revision.today,
    ...domains,
    archiveRevision,
    ...anchors,
    currentWindow,
    clockBucket,
  ]);
}

function marketReceiptReady(receipt: Row, day: string) {
  const revision = receipt?.read_revisions;
  return (
    receipt?.active_count === 0 &&
    revision?.market_locked === false &&
    typeof revision.market === "string" &&
    revision.market.length > 0 &&
    typeof revision.today === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(revision.today) &&
    day <= revision.today
  );
}

async function historicalReadProof(
  target: HistoricalTarget,
  signal: AbortSignal,
  sameDay = false,
) {
  const extra =
    sameDay ||
    historicalPlans[target.kind].domains.some(
      (scope) =>
        scope === "historical_reference" || scope === "archive_controls",
    );
  const receipt = await api<Row>(
    `/tasks/activity?date=${target.day}${extra ? "&historical=true" : ""}`,
    { signal },
  );
  return readMarketProof(target, receipt);
}

function validHistoricalBody(
  value: Row,
  { day, kind, monitorScope }: HistoricalTarget,
  snapshotId: string,
) {
  if (value?.date !== day || value.unavailable_reason) return false;
  if (kind === "news-events")
    return (
      Array.isArray(value.stages) &&
      value.stages.some(
        (stage) =>
          ["complete", "partial"].includes(stage?.status) &&
          typeof stage.captured_at === "string",
      )
    );
  if (kind === "auction" || kind === "noon")
    return (
      value.state === "available" &&
      value.can_capture === false &&
      !["queued", "running", "paused"].includes(value.task?.status) &&
      value.snapshot?.date === day &&
      typeof value.snapshot.snapshot_id === "string"
    );
  if (kind === "intraday")
    return (
      value.state === "available" &&
      value.can_capture === false &&
      !value.current_slot &&
      !["queued", "running", "paused"].includes(value.latest_job?.status) &&
      Array.isArray(value.snapshots) &&
      value.snapshots.some(
        (snapshot) => snapshot?.date === day && typeof snapshot.id === "string",
      )
    );
  if (value.snapshot_id !== snapshotId) return false;
  if (kind === "dragon-tiger")
    return (
      value.availability?.available === true &&
      typeof value.collection_snapshot_id === "string"
    );
  if (kind === "abnormal")
    return (
      value.monitor_scope === (monitorScope || "historical") &&
      (monitorScope !== "latest" || value.requested_date === day) &&
      value.data_coverage?.selected_date_available === true
    );
  if (kind === "overview" || kind === "indices")
    return Array.isArray(value.indices);
  return Array.isArray(value.items);
}

async function readHistoricalWarmBody(
  path: string,
  cap: number,
  signal: AbortSignal,
) {
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
  const body: HistoricalBody = { chunks: [], bytes: 0 };
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return body.bytes ? body : null;
      body.bytes += chunk.value.byteLength;
      if (body.bytes > cap) {
        await reader.cancel();
        return null;
      }
      body.chunks.push(chunk.value);
    }
  } finally {
    reader.releaseLock();
  }
}

function reserveWarmBytes(
  path: string,
  requested: number,
  sameDay: boolean,
  heavy: boolean,
) {
  historicalWarmReads.delete(path);
  for (const [key, entry] of historicalWarmReads) {
    if (
      entry.expires <= performance.now() ||
      (!sameDay && heavy && entry.heavy && !entry.sameDay)
    )
      historicalWarmReads.delete(key);
  }
  let storedBytes = [...historicalWarmReads.values()].reduce(
    (size, entry) => size + entry.body.bytes,
    0,
  );
  for (const [key, entry] of historicalWarmReads) {
    if (
      historicalWarmReads.size < historicalWarmMaxEntries &&
      historicalWarmMaxBytes - storedBytes >= requested
    )
      break;
    if (entry.sameDay) continue;
    historicalWarmReads.delete(key);
    storedBytes -= entry.body.bytes;
  }
  return historicalWarmReads.size < historicalWarmMaxEntries
    ? Math.min(requested, historicalWarmMaxBytes - storedBytes)
    : 0;
}

function rememberSameDayAttempt(path: string, proof: string) {
  sameDayAttempts.delete(path);
  sameDayAttempts.set(path, {
    proof,
    expires: performance.now() + historicalWarmTtlMs,
  });
  while (sameDayAttempts.size > 2 * historicalWarmMaxEntries)
    sameDayAttempts.delete(sameDayAttempts.keys().next().value!);
}

export async function prefetchMarketReads(
  sourcePath: string,
  currentPaths: string[],
  historicalDays: string[],
  snapshotId: string,
  signal: AbortSignal,
  canContinue: () => boolean,
) {
  const source = historicalTarget(sourcePath);
  if (!source || historicalPrefetchRunning) return false;
  const revision = historicalWarmRevision;
  const stillValid = () =>
    !signal.aborted && revision === historicalWarmRevision && canContinue();
  const mayStart = () => stillValid() && !foregroundMarketReadPending();
  if (!mayStart()) return false;

  const current = [...new Set(currentPaths)]
    .filter((path) => path !== sourcePath)
    .map((path) => ({ path, target: historicalTarget(path) }))
    .filter((item) => item.target?.day === source.day)
    .slice(0, historicalWarmMaxEntries);
  const older = historicalDays
    .slice(0, historicalPlans[source.kind].days)
    .filter((day) => /^\d{4}-\d{2}-\d{2}$/.test(day) && day < source.day)
    .map((day) => ({
      path: historicalPath({ day, kind: source.kind }),
      target: { day, kind: source.kind },
    }));

  historicalPrefetchRunning = true;
  try {
    const currentReceipt = current.length
      ? await api<Row>(`/tasks/activity?date=${source.day}&historical=true`, {
          signal,
        })
      : null;
    if (
      current.length &&
      (!currentReceipt || !marketReceiptReady(currentReceipt, source.day))
    )
      return false;
    if (currentReceipt && stillValid()) {
      const sourceProof = readMarketProof(source, currentReceipt);
      if (sourceProof) rememberSameDayAttempt(sourcePath, sourceProof);
    }
    for (const { path, target } of [...current, ...older]) {
      if (!mayStart()) return false;
      if (!target) continue;
      const sameDay = target.day === source.day;
      const proof =
        sameDay && currentReceipt
          ? readMarketProof(target, currentReceipt)
          : await historicalReadProof(target, signal);
      if (!mayStart()) return false;
      if (!proof) continue;

      const existing = historicalWarmReads.get(path);
      if (existing?.proof === proof && existing.expires > performance.now()) {
        if (sameDay) existing.sameDay = true;
        continue;
      }
      const attempted = sameDay ? sameDayAttempts.get(path) : null;
      if (attempted?.proof === proof && attempted.expires > performance.now())
        continue;
      const plan = historicalPlans[target.kind];
      const heavy = plan.days === 1;
      const cap = reserveWarmBytes(path, plan.bytes, sameDay, heavy);
      if (cap <= 0) continue;
      const body = await readHistoricalWarmBody(path, cap, signal);
      if (!stillValid()) return false;
      if (sameDay) rememberSameDayAttempt(path, proof);
      if (body)
        historicalWarmReads.set(path, {
          proof,
          body,
          snapshotId,
          heavy,
          sameDay,
          expires: performance.now() + historicalWarmTtlMs,
        });
    }
    return true;
  } finally {
    historicalPrefetchRunning = false;
  }
}

export function prefetchHistoricalReads(
  path: string,
  days: string[],
  snapshotId: string,
  signal: AbortSignal,
  canContinue: () => boolean,
) {
  return prefetchMarketReads(path, [], days, snapshotId, signal, canContinue);
}

function foregroundMarketReadPending() {
  return [...pendingReads.keys()].some(
    (path) =>
      /^\/(?:market|instruments|sectors)\//.test(path) &&
      !/\/(?:automation|dates)(?:\?|$)/.test(path),
  );
}

export function historicalPrefetchBusy() {
  return historicalPrefetchRunning || foregroundMarketReadPending();
}

async function readRetainedPage<T>(path: string, signal: AbortSignal) {
  const saved = historicalWarmReads.get(path);
  if (saved) {
    const discard = () => {
      if (historicalWarmReads.get(path) === saved)
        historicalWarmReads.delete(path);
    };
    const target = historicalTarget(path);
    if (target && saved.expires > performance.now()) {
      const revision = historicalWarmRevision;
      try {
        const proof = await historicalReadProof(target, signal, saved.sameDay);
        if (signal.aborted) throw new DOMException("Aborted", "AbortError");
        if (
          historicalWarmReads.get(path) === saved &&
          saved.expires > performance.now() &&
          revision === historicalWarmRevision &&
          proof === saved.proof
        ) {
          discard();
          const decoder = new TextDecoder();
          let text = "";
          for (const chunk of saved.body.chunks)
            text += decoder.decode(chunk, { stream: true });
          const value = JSON.parse(text + decoder.decode());
          if (validHistoricalBody(value, target, saved.snapshotId))
            return decodeChartColumns(value) as T;
        }
      } catch (cause: any) {
        // An unmounted reader has not consumed this verified-date candidate.
        if (signal.aborted) throw cause;
        if (cause?.incrementLocked) {
          discard();
          throw cause;
        }
      }
    }
    discard();
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
      historicalWarmReads.delete(path);
    }
    setKey((x) => x + 1);
  }, [path]);
  const invalidate = useCallback(() => {
    controller.current?.abort();
    if (path) {
      pendingReads.delete(path);
      historicalWarmReads.delete(path);
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
