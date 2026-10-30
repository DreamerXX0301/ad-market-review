import ZodiacSelect from "./ZodiacSelect";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CalendarDays,
  Plus,
  RefreshCw,
  Star,
} from "lucide-react";
import { post, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import { ErrorBox, Loading, useToast } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import { MemberMetricCoverage } from "../topics/MemberHistoryCells";
import { percent, tone } from "../../lib/format";
import { nextColumnSort, poolSort, sortableFields } from "./zodiacInteractions";
import {
  columns,
  groups,
  groupKeys,
  relations,
  safePreference,
  selectedColumns,
  shanghaiYear,
  yearLabel,
} from "./zodiacPresentation";
import { MetricCell } from "./ZodiacCells";
import ZodiacEditor from "./ZodiacEditor";
import ZodiacDetails from "./ZodiacDetails";
import ZodiacTable from "./ZodiacTable";
import "./Zodiac.css";

function read(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* In-memory browsing remains available. */
  }
}
const pageSize = 50;
export default function Zodiac({
  onStock,
}: {
  onStock: (id: string, navigation?: StockNavigation) => void;
}) {
  const notify = useToast(),
    active = usePageActive();
  const [preference, setPreference] = useState(() =>
    safePreference(read("zodiac-year")),
  );
  const [year, setYear] = useState(() =>
    preference.follow ? shanghaiYear() : preference.year,
  );
  const [status, setStatus] = useState("tracking"),
    [query, setQuery] = useState(""),
    [debounced, setDebounced] = useState(""),
    [relation, setRelation] = useState(""),
    [focused, setFocused] = useState(false),
    [board, setBoard] = useState(""),
    [completeness, setCompleteness] = useState(""),
    [sort, setSort] = useState("focused:desc"),
    [secondary, setSecondary] = useState("added_business_date:desc"),
    [page, setPage] = useState(0),
    [group, setGroup] = useState<string>("综合");
  const [custom, setCustom] = useState<Record<string, string[]>>(() => {
      try {
        return JSON.parse(read("zodiac-columns") || "{}");
      } catch {
        return {};
      }
    }),
    [density, setDensity] = useState(
      read("zodiac-density") === '"compact"' ? "compact" : "normal",
    );
  const [editor, setEditor] = useState<{
      year: number;
      head: Row;
      item?: Row;
      mode: "add" | "batch" | "edit" | "end";
    } | null>(null),
    [detail, setDetail] = useState<Row | null>(null),
    [verifying, setVerifying] = useState(false);
  const verifyRequest = useRef<{
    member: string;
    head: string;
    key: string;
  } | null>(null);
  const overview = useApi<Row>("/zodiac-pools");
  const params = new URLSearchParams({
    status,
    query: debounced,
    relation,
    focused: String(focused),
    board,
    completeness,
    sort: poolSort(sort, secondary),
    offset: String(page * pageSize),
    limit: String(pageSize),
  });
  const result = useApi<Row>(`/zodiac-pools/${year}/members?${params}`);
  const data = result.data?.observation_year === year ? result.data : null;
  const resultsRef = useRef<HTMLDivElement>(null);
  const tableScrollLeft = useRef(0);
  const [sortSpace, setSortSpace] = useState(0);
  useLayoutEffect(() => {
    if (data || result.error || !active) setSortSpace(0);
  }, [data, result.error, active]);
  const shown = selectedColumns(group, custom?.[group]);
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebounced(query);
      setPage(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    write("zodiac-year", preference);
  }, [preference]);
  useEffect(() => {
    write("zodiac-columns", custom);
  }, [custom]);
  useEffect(() => {
    write("zodiac-density", density);
  }, [density]);
  useEffect(() => {
    const follow = () => {
      if (active && preference.follow && !editor && !detail) {
        const next = shanghaiYear();
        setYear(next);
        setPreference((p) => (p.year === next ? p : { ...p, year: next }));
      }
    };
    follow();
    window.addEventListener("focus", follow);
    document.addEventListener("visibilitychange", follow);
    const timer = setInterval(follow, 60000);
    return () => {
      window.removeEventListener("focus", follow);
      document.removeEventListener("visibilitychange", follow);
      clearInterval(timer);
    };
  }, [active, preference.follow, editor, detail]);
  useEffect(() => {
    setPage(0);
  }, [year, status, relation, focused, board, completeness, sort, secondary]);
  const availableYears = [
    ...new Set([
      shanghaiYear() - 1,
      shanghaiYear(),
      shanghaiYear() + 1,
      shanghaiYear() + 2,
      year,
      ...(overview.data?.items || []).map((p: Row) => p.observation_year),
    ]),
  ].sort((a, b) => a - b);
  function refresh() {
    result.reload();
    overview.reload();
  }
  function openEditor(mode: "add" | "batch" | "edit" | "end", item?: Row) {
    if (data)
      setEditor({
        year,
        head: {
          business_date: data.business_date,
          generation: data.generation,
        },
        mode,
        item,
      });
  }
  function openStock(row: Row) {
    const navigation = (data?.navigation || []).filter(
      (r: Row) => r.metrics_as_of === row.metrics_as_of,
    );
    onStock(
      row.instrument_id,
      createStockNavigation(navigation, {
        source: "zodiac",
        scopeKey: `zodiac:${year}:${status}:${params}`,
        label: yearLabel(year),
        asOf: row.metrics_as_of,
      }),
    );
  }
  function selectYear(next: number) {
    if (!Number.isInteger(next) || next < 1900 || next > 2199) return;
    setYear(next);
    setPreference({ follow: false, year: next });
  }
  async function verify(row: Row) {
    if (!data || verifying) return;
    setVerifying(true);
    const head = JSON.stringify([data.business_date, data.generation]);
    if (
      verifyRequest.current?.member !== row.member_id ||
      verifyRequest.current?.head !== head
    )
      verifyRequest.current = {
        member: row.member_id,
        head,
        key: crypto.randomUUID(),
      };
    try {
      await post(`/zodiac-pools/${year}/verify-baselines`, {
        member_ids: [row.member_id],
        expected_business_date: data.business_date,
        expected_generation: data.generation,
        request_key: verifyRequest.current!.key,
      });
      setDetail(null);
      refresh();
      notify("基准核验已完成；缺数据时仍保留未知");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setVerifying(false);
    }
  }
  function moveColumn(key: string, direction: number) {
    const keys = shown.map((c) => c.key);
    const i = keys.indexOf(key),
      j = i + direction;
    if (j < 0 || j >= keys.length) return;
    [keys[i], keys[j]] = [keys[j], keys[i]];
    setCustom({ ...custom, [group]: keys });
  }
  const sortOptions = [
    { key: "name", label: "股票名称" },
    ...columns.filter((c) => sortableFields.has(c.key)),
  ];
  function sortColumn(key: string, floating: boolean) {
    // Reserve the list's space while the new order loads, without showing stale rows.
    if (floating)
      setSortSpace(resultsRef.current?.getBoundingClientRect().height || 0);
    setSort((current) => nextColumnSort(current, key));
    if (secondary.split(":")[0] === key) setSecondary("");
    setPage(0);
  }
  function heading(key: string, label: string, floating = false) {
    const primary = sort.split(":")[0] === key;
    const direction = primary ? sort.split(":")[1] : undefined;
    const secondaryDirection =
      !primary && secondary.split(":")[0] === key
        ? secondary.split(":")[1]
        : undefined;
    const currentDirection = direction || secondaryDirection;
    return (
      <th
        key={key}
        aria-sort={
          direction === "desc"
            ? "descending"
            : direction === "asc"
              ? "ascending"
              : undefined
        }
      >
        {sortableFields.has(key) ? (
          <button
            type="button"
            tabIndex={floating ? -1 : undefined}
            className={`zodiac-column-sort ${primary ? "active" : ""}`}
            onClick={() => sortColumn(key, floating)}
            aria-label={`${label}，${primary ? `当前${direction === "desc" ? "降序" : "升序"}，` : ""}点击${sort === `${key}:desc` ? "升序" : "降序"}`}
          >
            {label}
            {currentDirection === "desc" ? (
              <ArrowDown size={13} />
            ) : currentDirection === "asc" ? (
              <ArrowUp size={13} />
            ) : (
              <ArrowUpDown size={13} />
            )}{" "}
            {secondaryDirection && <small>次</small>}
          </button>
        ) : (
          label
        )}
      </th>
    );
  }
  const sorter = (
    value: string,
    onChange: (v: string) => void,
    label: string,
  ) => (
    <label>
      {label}
      <ZodiacSelect
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {label === "次排序" && <option value="">不使用</option>}
        <option value="focused:desc">重点优先</option>
        {sortOptions.flatMap((c) =>
          ["desc", "asc"].map((direction) => (
            <option key={c.key + direction} value={`${c.key}:${direction}`}>
              {c.label} {direction === "desc" ? "↓" : "↑"}
            </option>
          )),
        )}
      </ZodiacSelect>
    </label>
  );
  const rowActions = (row: Row) => (
    <div className="zodiac-row-actions">
      <button className="btn small" onClick={() => setDetail(row)}>
        详情
      </button>
      <button className="btn small" onClick={() => openEditor("edit", row)}>
        编辑
      </button>
      <button
        className="btn small"
        onClick={() =>
          row.status === "tracking"
            ? openEditor("end", row)
            : openEditor("add", { ...row, focused: false })
        }
      >
        {row.status === "tracking" ? "移出" : "重新加入"}
      </button>
    </div>
  );
  const identity = (row: Row) => (
    <div className="zodiac-identity">
      <Star
        size={14}
        className={row.focused ? "zodiac-star" : "muted"}
        fill={row.focused ? "currentColor" : "none"}
      />
      <div>
        <button className="zodiac-stock-name" onClick={() => openStock(row)}>
          {row.name}
        </button>
        <CopyStockCode instrumentId={row.instrument_id} variant="full" />
        <small>
          {row.board_label} · 第{row.round_number}轮
          {row.stats_year !== year ? ` · ${row.stats_year}年行情` : ""}
        </small>
      </div>
    </div>
  );
  return (
    <div className={`zodiac-page zodiac-${density}`}>
      <div className="zodiac-hero">
        <div className="zodiac-hero-title">
          <CalendarDays size={28} />
          <div>
            <h2>生肖跟踪</h2>
            <p>自己选股，逐年积累观察。名单、理由与每轮基准独立保留。</p>
          </div>
        </div>
        <div className="zodiac-toolbar">
          <button className="btn" onClick={refresh} disabled={result.loading}>
            <RefreshCw size={16} />
            刷新
          </button>
          <button
            className="btn"
            disabled={!data?.business_date || result.loading}
            onClick={() => openEditor("batch")}
          >
            批量添加
          </button>
          <button
            className="btn primary"
            disabled={!data?.business_date || result.loading}
            onClick={() => openEditor("add")}
          >
            <Plus size={16} />
            添加股票
          </button>
        </div>
      </div>
      <section className="zodiac-year-panel">
        <div>
          <strong>{yearLabel(year)}</strong>
          <p>
            行情截止 {data?.business_date || "待发布"} ·
            年内涨幅和涨停按行情截止年的1月1日起统计
            {status === "ended" ? "；已移出轮次按各自移出日截止" : ""}
          </p>
        </div>
        <div className="zodiac-year-controls">
          <label>
            观察年度
            <ZodiacSelect
              aria-label="观察年度"
              value={year}
              onChange={(e) => selectYear(Number(e.target.value))}
            >
              {availableYears.map((y) => (
                <option value={y} key={y}>
                  {y}年
                </option>
              ))}
            </ZodiacSelect>
          </label>
          <label>
            指定年份
            <input
              aria-label="指定观察年份"
              type="number"
              min={1900}
              max={2199}
              value={year}
              onChange={(e) => selectYear(Number(e.target.value))}
            />
          </label>
          <label className="zodiac-check">
            <input
              type="checkbox"
              checked={preference.follow}
              onChange={(e) => {
                const next = e.target.checked ? shanghaiYear() : year;
                setPreference({ follow: e.target.checked, year: next });
                setYear(next);
              }}
            />
            跟随当前年份
          </label>
        </div>
      </section>
      <p className="zodiac-scope">
        每年1月1日仅切换默认年度。旧池与名单保留，股票由你手动添加；不会自动迁移。行业概念是当前研究归属。
      </p>
      {data && (
        <div className="zodiac-summary">
          <span>
            本池{status === "tracking" ? "观察中" : "已移出"}{" "}
            <b>{data.total}</b>
          </span>
          <span>
            筛选结果 <b>{data.matched_count}</b>
          </span>
          <span>
            重点 <b>{data.summary.focused}</b>
          </span>
          <span>
            涨 / 跌 / 平{" "}
            <b
              className="zodiac-breadth"
              aria-label={`上涨${data.summary.up}只，下跌${data.summary.down}只，平盘${data.summary.flat}只`}
            >
              <span className="up">{data.summary.up}</span>
              <span className="muted"> / </span>
              <span className="down">{data.summary.down}</span>
              <span className="muted"> / {data.summary.flat}</span>
            </b>
          </span>
          <span>
            涨幅未知 <b className="muted">{data.summary.unknown}</b>
          </span>
          <span>
            当日涨幅中位数{" "}
            <b className={tone(data.summary.median_change_pct)}>
              {percent(data.summary.median_change_pct)}
            </b>
            <small>
              {data.summary.known_count}/{data.summary.sample_count}只可核验
              {status === "ended" ? " · 各自移出日样本" : ""}
            </small>
          </span>
        </div>
      )}
      <div className="zodiac-controls">
        <div className="zodiac-groups">
          <button
            className={status === "tracking" ? "active" : ""}
            onClick={() => setStatus("tracking")}
          >
            观察中
          </button>
          <button
            className={status === "ended" ? "active" : ""}
            onClick={() => setStatus("ended")}
          >
            已移出历史
          </button>
        </div>
        <div className="zodiac-filters">
          <input
            aria-label="搜索生肖池"
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="股票、代码、行业、概念或理由"
          />
          <ZodiacSelect
            aria-label="关联类型"
            value={relation}
            onChange={(e) => setRelation(e.target.value)}
          >
            <option value="">全部关联类型</option>
            {Object.entries(relations).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </ZodiacSelect>
          <ZodiacSelect
            aria-label="上市板块"
            value={board}
            onChange={(e) => setBoard(e.target.value)}
          >
            <option value="">全部上市板块</option>
            <option value="main">主板</option>
            <option value="chinext">创业板</option>
            <option value="star">科创板</option>
            <option value="beijing">北交所</option>
            <option value="unknown">未知</option>
          </ZodiacSelect>
          <ZodiacSelect
            aria-label="指标覆盖"
            value={completeness}
            onChange={(e) => setCompleteness(e.target.value)}
          >
            <option value="">全部覆盖状态</option>
            <option value="complete">年度与30日指标可核验</option>
            <option value="missing">存在统计缺口</option>
          </ZodiacSelect>
          <label className="zodiac-check">
            <input
              type="checkbox"
              checked={focused}
              onChange={(e) => setFocused(e.target.checked)}
            />
            仅重点
          </label>
        </div>
        <div className="zodiac-toolbar zodiac-table-toolbar">
          <div className="zodiac-groups">
            {groups.map((g) => (
              <button
                key={g}
                className={group === g ? "active" : ""}
                onClick={() => setGroup(g)}
              >
                {g}
              </button>
            ))}
          </div>
          <label>
            密度
            <ZodiacSelect
              aria-label="显示密度"
              value={density}
              onChange={(e) => setDensity(e.target.value)}
            >
              <option value="normal">标准</option>
              <option value="compact">紧凑</option>
            </ZodiacSelect>
          </label>
          <details className="zodiac-column-picker">
            <summary>自定义列</summary>
            <div>
              {columns.map((c) => (
                <label className="zodiac-check" key={c.key}>
                  <input
                    type="checkbox"
                    checked={shown.some((s) => s.key === c.key)}
                    onChange={(e) =>
                      setCustom({
                        ...custom,
                        [group]: e.target.checked
                          ? [...shown.map((s) => s.key), c.key]
                          : shown
                              .filter((s) => s.key !== c.key)
                              .map((s) => s.key),
                      })
                    }
                  />
                  {c.label}
                  {shown.some((s) => s.key === c.key) && (
                    <>
                      <button
                        onClick={() => moveColumn(c.key, -1)}
                        aria-label={`${c.label}前移`}
                      >
                        ↑
                      </button>
                      <button
                        onClick={() => moveColumn(c.key, 1)}
                        aria-label={`${c.label}后移`}
                      >
                        ↓
                      </button>
                    </>
                  )}
                </label>
              ))}
              <button
                className="btn"
                onClick={() =>
                  setCustom({ ...custom, [group]: groupKeys[group] })
                }
              >
                恢复本组默认列
              </button>
            </div>
          </details>
        </div>
        <div className="zodiac-sort">
          {sorter(sort, setSort, "主排序")}
          {sorter(secondary, setSecondary, "次排序")}
          <small>
            全池过滤后排序，再分页；未知始终置后，涨停下界不参与精确次数排名。
          </small>
        </div>
      </div>
      <div
        className="zodiac-results"
        ref={resultsRef}
        style={{ minHeight: sortSpace || undefined }}
      >
        {result.error && <ErrorBox message={result.error} retry={refresh} />}{" "}
        {result.loading && <Loading />}
        {data && (
          <>
            <MemberMetricCoverage coverage={data.coverage} />
            {data.items.length ? (
              <>
                <ZodiacTable
                  active={active}
                  scrollLeft={tableScrollLeft}
                  header={(floating) => (
                    <tr>
                      {heading("name", "股票", floating)}
                      {shown.map((c) => heading(c.key, c.label, floating))}
                      <th>维护</th>
                    </tr>
                  )}
                >
                  {data.items.map((row: Row) => (
                    <tr key={row.member_id}>
                      <td>{identity(row)}</td>
                      {shown.map((c) => (
                        <td key={c.key}>
                          <MetricCell row={row} column={c} />
                        </td>
                      ))}
                      <td>{rowActions(row)}</td>
                    </tr>
                  ))}
                </ZodiacTable>
                <div className="zodiac-mobile-cards">
                  {data.items.map((row: Row) => (
                    <article key={row.member_id}>
                      {identity(row)}
                      <div className="zodiac-card-metrics">
                        {selectedColumns("综合", undefined).map((c) => (
                          <div
                            className={c.type === "topics" ? "wide" : ""}
                            key={c.key}
                          >
                            <small>{c.label}</small>
                            <MetricCell row={row} column={c} />
                          </div>
                        ))}
                      </div>
                      <details>
                        <summary>{group} · 全部显示字段</summary>
                        {shown.map((c) => (
                          <div className="zodiac-card-extra" key={c.key}>
                            <span>{c.label}</span>
                            <MetricCell row={row} column={c} />
                          </div>
                        ))}
                      </details>
                      {rowActions(row)}
                    </article>
                  ))}
                </div>
              </>
            ) : (
              <div className="zodiac-empty">
                <CalendarDays size={34} />
                <h3>
                  {data.total
                    ? "没有符合筛选条件的股票"
                    : status === "tracking"
                      ? "这个年度还没有观察股票"
                      : "暂无移出历史"}
                </h3>
                <p>
                  {data.total
                    ? "调整搜索或过滤条件后再查看。"
                    : status === "tracking"
                      ? "从添加股票开始，保存你自己的关联理由。"
                      : "结束观察后，原轮次及基准会保存在这里。"}
                </p>
                {!data.total && status === "tracking" && (
                  <button
                    className="btn primary"
                    disabled={!data.business_date}
                    onClick={() => openEditor("add")}
                  >
                    添加第一只股票
                  </button>
                )}
                {!data.business_date && <p>请先在数据中心发布真实行情。</p>}
              </div>
            )}
            <div className="zodiac-pagination">
              <button
                className="btn"
                disabled={!page || result.loading}
                onClick={() => setPage((p) => p - 1)}
              >
                上一页
              </button>
              <span>
                第 {page + 1} /{" "}
                {Math.max(1, Math.ceil(data.matched_count / pageSize))} 页 ·
                每页50条
              </span>
              <button
                className="btn"
                disabled={
                  (page + 1) * pageSize >= data.matched_count || result.loading
                }
                onClick={() => setPage((p) => p + 1)}
              >
                下一页
              </button>
            </div>
          </>
        )}
      </div>
      {editor && (
        <ZodiacEditor
          {...editor}
          onClose={() => setEditor(null)}
          onSaved={(message) => {
            setEditor(null);
            refresh();
            notify(message);
          }}
        />
      )}
      {detail && (
        <ZodiacDetails
          item={detail}
          onClose={() => setDetail(null)}
          onStock={() => openStock(detail)}
          onVerify={() => void verify(detail)}
          onEdit={(row) => {
            setDetail(null);
            openEditor("edit", row);
          }}
        />
      )}
    </div>
  );
}
