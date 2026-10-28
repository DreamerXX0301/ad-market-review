import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CalendarDays,
  CheckCircle2,
  Clock3,
  RefreshCw,
  Sunrise,
} from "lucide-react";
import { post, requestKey, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel,
  useToast,
} from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import { amount, integer, num, percent, tone } from "../../lib/format";
import { noonClock, noonLines, noonRows } from "./noonPresentation";
import {
  auctionCanAct,
  auctionCaptureState,
  auctionCodes,
  auctionNumber,
  auctionPayload,
  auctionPreviousFocus,
  auctionShouldPoll,
  auctionSortRows,
  auctionStates,
} from "./auctionPresentation";
import "./AuctionReview.css";

const COLUMNS = [
  ["auction_price", "竞价价", "元"],
  ["auction_pct", "竞价涨幅", "%"],
  ["auction_amount", "竞价额", "元"],
  ["auction_volume", "成交量", "手"],
  ["auction_volume_ratio", "来源量比", "倍"],
  ["auction_yesterday_ratio_pct", "占昨日量", "%"],
  ["auction_turnover_pct", "竞价换手", "%"],
];
function metric(row: Row, field: string) {
  const value = auctionNumber(row[field]);
  if (value == null) return "—";
  if (field === "auction_amount") return amount(value);
  if (field === "auction_volume") return integer(value);
  if (field === "auction_pct") return percent(value);
  if (field.endsWith("_pct")) return `${num(value)}%`;
  if (field === "auction_volume_ratio") return `${num(value)}倍`;
  return num(value);
}
function sourceName(row: Row) {
  return (
    (
      {
        calendar: "交易日历",
        snapshot: "竞价终态",
        auction: "竞价终态",
        benchmark: "来源短线基准",
      } as Record<string, string>
    )[row.name] ||
    row.name ||
    row.endpoint ||
    "来源请求"
  );
}

export default function AuctionReview({
  date,
  onDate,
  onDatesChanged,
  onStock,
  chartDate,
}: {
  date: string;
  onDate: (day: string) => void;
  onDatesChanged?: () => void;
  onStock?: (id: string) => void;
  chartDate?: string;
}) {
  const result = useApi<Row>(
    `/market/auction${date ? `?date=${encodeURIComponent(date)}` : ""}`,
  );
  const active = usePageActive();
  const notify = useToast();
  const view = result.data?.date === date || !date ? result.data : null;
  const selected = date || view?.date || "";
  const payload = auctionPayload(view, selected);
  const [closeOpen, setCloseOpen] = useState(false);
  const closeResult = useApi<Row>(
    closeOpen && payload
      ? `/market/auction/close-review?date=${encodeURIComponent(selected)}`
      : null,
  );
  const closeReview =
    closeResult.data?.date === selected ? closeResult.data : null;
  const closeItems = noonRows(closeReview?.items).filter((row) =>
    /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id || ""),
  );
  const hasCloseFacts =
    closeItems.length > 0 ||
    (auctionNumber(closeReview?.summary?.requested) != null &&
      closeReview!.summary.requested >= 0 &&
      auctionNumber(closeReview?.summary?.valid) != null &&
      closeReview!.summary.valid >= 0);
  const [codes, setCodes] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [pendingDay, setPendingDay] = useState("");
  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>({
    key: "auction_pct",
    direction: "desc",
  });
  const submitting = useRef(false);
  const keys = useRef<Record<string, { key: string; ids: string[] }>>({});
  const currentDay = useRef(selected);
  currentDay.current = selected;
  const parsed = auctionCodes(codes);
  const control = auctionCaptureState(view, selected, pendingDay === selected);
  const busy = pendingDay === selected;
  const taskId = view?.task?.task_id || view?.task?.job_id;
  useEffect(() => {
    setSubmitError("");
    setCloseOpen(false);
  }, [selected]);
  useEffect(() => {
    if (!active || !auctionShouldPoll(view, selected)) return;
    const timer = window.setInterval(result.reload, 2000);
    return () => window.clearInterval(timer);
  }, [active, view?.state, selected, result.reload]);
  useEffect(() => {
    if (!active || view?.today !== selected || view?.state !== "not_collected")
      return;
    // This checks only saved local state, allowing a page opened before 09:25 to unlock.
    const timer = window.setInterval(result.reload, 20000);
    return () => window.clearInterval(timer);
  }, [active, view?.today, view?.state, selected, result.reload]);
  async function capture() {
    if (
      submitting.current ||
      !view ||
      !selected ||
      control.disabled ||
      parsed.error
    )
      return;
    submitting.current = true;
    const day = selected;
    setPendingDay(day);
    setSubmitError("");
    // An uncertain network result replays the exact request identity and candidate list.
    const saved = (keys.current[day] ||= {
      key: requestKey(),
      ids: parsed.ids,
    });
    try {
      await post("/market/auction/jobs", {
        date: day,
        request_key: saved.key,
        ...(saved.ids.length ? { instrument_ids: saved.ids } : {}),
      });
      onDatesChanged?.();
    } catch (error: any) {
      if ([400, 401, 403, 409, 422].includes(error.status))
        delete keys.current[day];
      if (currentDay.current === day)
        setSubmitError(error.message || "任务提交未完成，请重新读取本地状态。");
    } finally {
      submitting.current = false;
      setPendingDay("");
      result.reload();
    }
  }
  async function changeTask(action: "retry" | "resume" | "cancel" | "pause") {
    if (submitting.current || !auctionCanAct(view, selected, action) || !taskId)
      return;
    const day = selected;
    submitting.current = true;
    setPendingDay(day);
    setSubmitError("");
    try {
      await post(`/tasks/${encodeURIComponent(taskId)}/${action}`);
      notify(
        action === "retry"
          ? "原竞价任务已重新排队，仍使用冻结名单。"
          : action === "resume"
            ? "已继续原竞价任务。"
            : action === "pause"
              ? "已暂停采集。"
              : "已取消本日采集。",
      );
      onDatesChanged?.();
    } catch (error: any) {
      if (currentDay.current === day)
        setSubmitError(error.message || "任务操作未完成，请刷新状态。");
    } finally {
      submitting.current = false;
      setPendingDay("");
      result.reload();
    }
  }
  const summary = payload?.quotes?.summary || {};
  const universe = payload?.universe || {};
  const candidates = new Map(
    noonRows(universe.items).map((row) => [row.instrument_id, row]),
  );
  const rows = auctionSortRows(
    noonRows(payload?.quotes?.items).map((row) => ({
      ...candidates.get(row.instrument_id),
      ...row,
    })),
    sort.key,
    sort.direction,
  );
  const sources = noonRows(payload?.sources);
  const gaps = [
    ...new Set([
      ...noonLines(payload?.gaps),
      ...noonLines(payload?.quotes?.gaps),
    ]),
  ];
  const benchmark = payload?.benchmark || {};
  const analysis = noonLines(payload?.analysis);
  const previousFocus = auctionPreviousFocus(payload);
  const preparation =
    !payload && selected === view?.today && view?.preparation
      ? auctionPreviousFocus({
          date: selected,
          previous_date: view.preparation.date,
          collected_start_at: view.now,
          previous_review: view.preparation,
          quotes: { items: [] },
        })
      : null;
  const lowOpen = auctionSortRows(
    rows.filter((row) => (auctionNumber(row.auction_pct) ?? 0) < 0),
    "auction_pct",
    "asc",
  ).slice(0, 6);
  const state = view?.state || "not_collected";
  const canEdit =
    (!view || state === "not_collected") && !keys.current[selected];
  const identity = (row: Row) => (
    <div className="auction-identity">
      <button
        className="auction-stock"
        disabled={!chartDate || !onStock}
        title={
          chartDate
            ? `K线截至 ${chartDate}，竞价价格见本页`
            : "该观察日之前没有已发布收盘行情"
        }
        onClick={() => onStock?.(row.instrument_id)}
      >
        {row.name || row.instrument_id}
      </button>
      <CopyStockCode instrumentId={row.instrument_id} />
    </div>
  );
  return (
    <div className="auction-review page-stack">
      <div className="auction-page-head">
        <div>
          <div className="auction-eyebrow">
            <Sunrise size={16} /> 开盘前五分钟 · 终态观察
          </div>
          <h1>盘前竞价</h1>
          <p>昨天怎么看，今天竞价怎样，开盘后继续核对什么。</p>
        </div>
        <div className="auction-head-actions">
          {view?.today && (
            <button
              className="btn"
              onClick={() => onDate(view.today)}
              disabled={selected === view.today}
            >
              <CalendarDays size={14} />
              今天
            </button>
          )}
          <button
            className="btn"
            onClick={result.reload}
            disabled={result.loading}
          >
            <RefreshCw size={14} />
            读取状态
          </button>
          <button
            className="btn primary"
            onClick={capture}
            disabled={control.disabled || !!parsed.error || result.loading}
          >
            <Sunrise size={15} />
            {control.label}
          </button>
        </div>
      </div>
      <div className="auction-scope-strip">
        <strong>{selected || "正在读取观察日"}</strong>
        <Badge tone={state === "available" ? "good" : "neutral"}>
          {auctionStates[state] || state}
        </Badge>
        <span>
          <Clock3 size={13} /> 北京时间09:25至09:30前
        </span>
        {payload && <span>前一交易日 {payload.previous_date || "未知"}</span>}
      </div>
      <p className="auction-control-reason" role="status">
        {control.reason}
      </p>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {submitError && <ErrorBox message={submitError} retry={result.reload} />}
      {result.loading && !view && <Loading />}
      <div className="auction-method-strip">
        <div>
          <span>01</span>
          <strong>准备关注</strong>
          <p>先看昨日留存复盘与原关注，再补自选及已核验强势样本。</p>
        </div>
        <div>
          <span>02</span>
          <strong>09:25后核对</strong>
          <p>手动留存终态，比较高低开、竞价额与来源量比。</p>
        </div>
        <div>
          <span>03</span>
          <strong>带着证据观察</strong>
          <p>高开不等于持续走强；开盘后的变化另行核验。</p>
        </div>
      </div>
      {!payload && preparation && (
        <Panel
          title="采集前准备 · 最近已发布日"
          subtitle={
            view?.preparation?.notice ||
            "严格前一交易日待采集时核验；当前材料尚未冻结。"
          }
          actions={<Badge tone="warn">尚未冻结</Badge>}
        >
          <div className="auction-panel-body">
            <div className="auction-preparation-head">
              <span>
                材料日期{" "}
                <b>
                  {preparation.previous?.date ||
                    view?.preparation?.date ||
                    "未知"}
                </b>
              </span>
              <span>
                实际记录{" "}
                {preparation.retained
                  ? noonClock(preparation.previous?.recorded_at_ms)
                  : "未留存"}
              </span>
            </div>
            {preparation.retained ? (
              <>
                <ul className="auction-lines">
                  {preparation.lines.map((line, index) => (
                    <li key={index}>{line}</li>
                  ))}
                </ul>
                {preparation.items.length > 0 && (
                  <div className="auction-preparation-list">
                    {preparation.items.map((row) => (
                      <article key={row.instrument_id}>
                        {identity(row)}
                        {row.mainline && <strong>{row.mainline}</strong>}
                        {noonRows(row.notes).length ? (
                          <div className="auction-original-notes">
                            {noonRows(row.notes).map((note, index) => (
                              <p key={index}>
                                <b>{note.label}</b>
                                {note.text}
                              </p>
                            ))}
                          </div>
                        ) : (
                          <p className="auction-muted">
                            已留存关注，暂无逐股判断原文。
                          </p>
                        )}
                        <small>竞价尚未采集，等09:25后再逐项对照。</small>
                      </article>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p className="auction-muted">
                最近已发布日尚未留存可核验的复盘判断或逐股主线。可以先整理自选与自定义名单；本次不会替你补写昨日判断。
              </p>
            )}
            <p className="auction-reading-note">
              这份准备会随着本地保存的复盘变化。采集时由工作进程核实严格前一交易日，并冻结实际引用版本。
            </p>
          </div>
        </Panel>
      )}
      {!payload && (
        <Panel
          title="这次看哪些股票"
          subtitle="最多100只；只观察冻结的候选样本，不代表全市场。"
        >
          <div className="auction-panel-body">
            <label htmlFor="auction-codes">
              自定义完整代码 <span>选填 · 留空使用默认名单</span>
            </label>
            <textarea
              id="auction-codes"
              value={codes}
              onChange={(event) => setCodes(event.target.value)}
              disabled={!canEdit || busy}
              placeholder="例如：600519.SH, 000001.SZ&#10;逗号、空格或换行分隔"
              rows={3}
              aria-describedby="auction-codes-help"
            />
            <div className="auction-input-foot">
              <p id="auction-codes-help">
                {parsed.error ||
                  (parsed.ids.length
                    ? `已填写 ${parsed.ids.length} 只；仅采集这份自定义名单。`
                    : "默认优先昨日复盘关注与原主线，再补自选及严格前一交易日已核验的涨停、炸板样本；超出100只标明未纳入数量。")}
              </p>
              {parsed.error && <Badge tone="danger">请检查代码</Badge>}
            </div>
            {!canEdit && (
              <p className="auction-muted">
                本日任务已提交，重试沿用首次冻结名单。
              </p>
            )}
          </div>
        </Panel>
      )}
      {view?.task && !payload && (
        <Panel
          title="本日采集任务"
          actions={
            <div className="button-group">
              {auctionCanAct(view, selected, "retry") && (
                <button
                  className="btn primary"
                  disabled={busy}
                  onClick={() => changeTask("retry")}
                >
                  重试原任务
                </button>
              )}
              {auctionCanAct(view, selected, "resume") && (
                <button
                  className="btn primary"
                  disabled={busy}
                  onClick={() => changeTask("resume")}
                >
                  继续原任务
                </button>
              )}
              {auctionCanAct(view, selected, "pause") && (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => changeTask("pause")}
                >
                  暂停
                </button>
              )}
              {auctionCanAct(view, selected, "cancel") && (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => changeTask("cancel")}
                >
                  取消
                </button>
              )}
            </div>
          }
        >
          <div className="auction-panel-body">
            <p>
              {view.task.error_summary ||
                view.task.message ||
                view.task.phase ||
                auctionStates[state]}
            </p>
            <small className="auction-muted">
              {taskId} · 过窗不补造；暂停或取消后不会自动继续。
            </small>
          </div>
        </Panel>
      )}
      {!payload && !["queued", "running"].includes(state) && (
        <Panel>
          <Empty
            title={
              state === "not_collected"
                ? "先留下一份真实的竞价观察"
                : auctionStates[state]
            }
            description={
              state === "not_collected"
                ? "在交易日09:25后手动采集。没有留存的历史竞价保持空白，当前报价不会补成历史结果。"
                : "当前没有可用的竞价正文。窗口内可按任务状态继续或重试原任务；窗口结束后保留缺口。"
            }
          />
        </Panel>
      )}
      {payload && (
        <>
          <div className="auction-metrics">
            <div>
              <span>有效竞价覆盖</span>
              <strong>
                {integer(summary.valid)}
                <small> / {integer(summary.requested)}</small>
              </strong>
              <p>缺报价 {integer(summary.missing)} 只</p>
            </div>
            <div>
              <span>高开 / 低开 / 平开</span>
              <strong className="auction-breadth">
                <b className="up">{integer(summary.up)}</b>
                <i>/</i>
                <b className="down">{integer(summary.down)}</b>
                <i>/</i>
                <b>{integer(summary.flat)}</b>
              </strong>
              <p>只统计本次有效样本</p>
            </div>
            <div>
              <span>样本竞价涨幅中位数</span>
              <strong className={tone(auctionNumber(summary.median_pct))}>
                {percent(auctionNumber(summary.median_pct))}
              </strong>
              <p>相对来源前收盘价</p>
            </div>
            <div>
              <span>有效样本竞价额</span>
              <strong>{amount(auctionNumber(summary.amount))}</strong>
              <p>金额有效 {integer(summary.amount_valid)} 只</p>
            </div>
          </div>
          <div className="auction-capture-proof">
            <CheckCircle2 size={14} />
            <span>留存于 {noonClock(view?.snapshot?.recorded_at_ms)}</span>
            <span>
              采集 {noonClock(view?.snapshot?.collected_start_at)} —{" "}
              {noonClock(view?.snapshot?.collected_end_at)}
            </span>
            <Badge>
              {view?.snapshot?.quality === "complete" ? "核验通过" : "部分可用"}
            </Badge>
          </div>
          <Panel
            title="昨日复盘 → 今日竞价"
            subtitle="引用采集开始前已经保存的严格前一交易日材料，原判断与本次事实并列。"
            actions={
              <Badge tone={previousFocus.retained ? "good" : "warn"}>
                {previousFocus.retained ? "已留存原文" : "原判断未留存"}
              </Badge>
            }
          >
            <div className="auction-panel-body">
              <div className="auction-prior-summary">
                <div>
                  <span className="auction-section-eyebrow">昨天怎么看</span>
                  {previousFocus.retained ? (
                    <ul className="auction-lines">
                      {previousFocus.lines.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="auction-muted">
                      {previousFocus.previous?.state === "background_only"
                        ? "昨日只有已发布收盘事实，没有留存复盘判断；不补写昨天的预期。"
                        : "没有可核验的昨日留存判断。已取得的竞价仍按真实样本展示。"}
                    </p>
                  )}
                  <small>
                    {payload.previous_date || "严格前日待核验"} · 原记录{" "}
                    {previousFocus.retained
                      ? noonClock(previousFocus.previous?.recorded_at_ms)
                      : "未留存"}
                  </small>
                </div>
                <div>
                  <span className="auction-section-eyebrow">今天先核对</span>
                  <p>
                    原关注股票的竞价是否仍有承接；明显低开的样本先列为待复核。
                  </p>
                  <small>
                    自由文字不自动判定兑现或证伪，当前题材不回填昨日。
                  </small>
                </div>
              </div>
              {previousFocus.items.length ? (
                <div className="auction-focus-wrap">
                  <table className="auction-focus-table">
                    <thead>
                      <tr>
                        <th>昨日原关注与判断</th>
                        <th>本次竞价事实</th>
                        <th>开盘后核对</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previousFocus.items.map((row) => (
                        <tr key={row.instrument_id}>
                          <td>
                            {identity(row)}
                            {row.mainline && (
                              <strong className="auction-original-mainline">
                                {row.mainline}
                              </strong>
                            )}
                            {noonRows(row.notes).length ? (
                              <details className="auction-original-notes" open>
                                <summary>
                                  原判断与依据 · {row.notes.length}条
                                </summary>
                                {noonRows(row.notes).map((note, index) => (
                                  <p key={index}>
                                    <b>{note.label}</b>
                                    {note.text}
                                    {note.source && (
                                      <small>来源：{note.source}</small>
                                    )}
                                  </p>
                                ))}
                              </details>
                            ) : (
                              <small className="auction-muted">
                                原留存没有逐股判断文字
                              </small>
                            )}
                            {row.recorded_at_ms && (
                              <small className="auction-muted">
                                记录 {noonClock(row.recorded_at_ms)}
                              </small>
                            )}
                          </td>
                          <td>
                            {row.quote &&
                            auctionNumber(row.quote.auction_price) != null ? (
                              <>
                                <strong
                                  className={tone(
                                    auctionNumber(row.quote.auction_pct),
                                  )}
                                >
                                  {percent(
                                    auctionNumber(row.quote.auction_pct),
                                  )}
                                </strong>
                                <span>
                                  竞价价{" "}
                                  {num(auctionNumber(row.quote.auction_price))}{" "}
                                  元
                                </span>
                                <small>
                                  竞价额{" "}
                                  {amount(
                                    auctionNumber(row.quote.auction_amount),
                                  )}
                                </small>
                              </>
                            ) : (
                              <span className="auction-muted">
                                {row.quote?.reason ||
                                  "本次未取得该股有效竞价，不能按0处理。"}
                              </span>
                            )}
                          </td>
                          <td>
                            {row.quote &&
                            auctionNumber(row.quote.auction_price) != null ? (
                              <p>
                                观察开盘后是否维持在本次竞价价{" "}
                                <b>
                                  {num(auctionNumber(row.quote.auction_price))}
                                  元
                                </b>{" "}
                                附近，成交后有无明显回落。
                              </p>
                            ) : (
                              <p className="auction-muted">
                                先核验真实报价，再与原判断对照。
                              </p>
                            )}
                            {(noonLines(row.triggers).length > 0 ||
                              noonLines(row.invalidations).length > 0) && (
                              <details className="auction-original-notes">
                                <summary>原观察条件</summary>
                                {noonLines(row.triggers).map((line, index) => (
                                  <p key={`t-${index}`}>原观察：{line}</p>
                                ))}
                                {noonLines(row.invalidations).map(
                                  (line, index) => (
                                    <p key={`i-${index}`}>原失效条件：{line}</p>
                                  ),
                                )}
                              </details>
                            )}
                            <small className="auction-muted">
                              仅供观察，不触发策略或自动跟踪。
                            </small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="auction-prior-empty">
                  昨日没有留存明确的逐股关注。下方保留本次冻结候选，之后可先在复盘中留下原判断。
                </p>
              )}
            </div>
          </Panel>
          <Panel
            title="开盘后优先复核"
            subtitle="从本次有效样本中列示低开股票；低开不自动等于失败或买卖信号。"
          >
            <div className="auction-panel-body">
              {lowOpen.length ? (
                <div className="auction-low-open">
                  {lowOpen.map((row) => (
                    <article key={row.instrument_id}>
                      <div>
                        {identity(row)}
                        <strong className="down">
                          {percent(auctionNumber(row.auction_pct))}
                        </strong>
                      </div>
                      <p>
                        {noonLines(row.reasons).join(" · ") || "冻结候选样本"}
                      </p>
                      <span>
                        竞价价 {num(auctionNumber(row.auction_price))}元 ·
                        竞价额 {amount(auctionNumber(row.auction_amount))}
                      </span>
                      <small>
                        观察开盘后能否收回竞价参照，或成交后继续回落；结合原判断核对。
                      </small>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="auction-muted">
                  本次有效报价样本没有低开记录；缺报价仍单独保留未知。
                </p>
              )}
            </div>
          </Panel>
          <Panel
            title="竞价观察单"
            subtitle={
              universe.scope ||
              summary.scope ||
              "冻结候选样本；不据高开或量比自动产生买卖判断。"
            }
            actions={
              <Badge>
                {integer(universe.requested ?? rows.length)}只已选 ·{" "}
                {integer(universe.omitted ?? 0)}只未纳入
              </Badge>
            }
          >
            <div className="auction-table-wrap">
              <table className="auction-table">
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>入选背景</th>
                    {COLUMNS.map(([field, title, unit]) => (
                      <th
                        key={field}
                        aria-sort={
                          sort.key === field
                            ? sort.direction === "asc"
                              ? "ascending"
                              : "descending"
                            : "none"
                        }
                      >
                        <button
                          className="auction-sort"
                          onClick={() =>
                            setSort({
                              key: field,
                              direction:
                                sort.key === field && sort.direction === "desc"
                                  ? "asc"
                                  : "desc",
                            })
                          }
                        >
                          {title}
                          <small>{unit}</small>
                          {sort.key === field &&
                            (sort.direction === "asc" ? (
                              <ArrowUp size={12} />
                            ) : (
                              <ArrowDown size={12} />
                            ))}
                        </button>
                      </th>
                    ))}
                    <th>核验与缺口</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.instrument_id}>
                      <td>{identity(row)}</td>
                      <td className="auction-background">
                        {noonLines(row.reasons).join(" · ") || "自定义关注"}
                        {auctionNumber(row.previous_board_count) != null && (
                          <small>
                            昨日 {integer(row.previous_board_count)} 板
                          </small>
                        )}
                      </td>
                      {COLUMNS.map(([field]) => (
                        <td
                          key={field}
                          className={
                            field === "auction_pct"
                              ? tone(auctionNumber(row[field]))
                              : ""
                          }
                        >
                          {metric(row, field)}
                        </td>
                      ))}
                      <td className="auction-row-state">
                        {row.reason ||
                          (row.state === "unknown" ? "报价未知" : "终态已核验")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="auction-table-foot">
              竞价额以元、成交量以手展示；来源量比与占昨日量分别列示。未提供逐票行情时刻，不把响应组装时间当报价时间。K线截至{" "}
              {chartDate || "此前已发布收盘日未知"}。
            </div>
          </Panel>
          <div className="auction-reading-grid">
            <Panel
              title="本次观察要点"
              subtitle="只描述本份样本，后续走势需要继续核对。"
            >
              <div className="auction-panel-body">
                {analysis.length ? (
                  <ul className="auction-lines">
                    {analysis.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="auction-muted">暂无足够证据生成观察要点。</p>
                )}
                <p className="auction-reading-note">
                  重点看昨日关注是否仍有承接，以及明显低开的负反馈。单次终态无法还原撤单过程。
                </p>
              </div>
            </Panel>
            <Panel
              title="来源短线基准"
              subtitle="服务端标签单独展示，不等同于本次候选或全市场。"
            >
              <div className="auction-panel-body">
                {noonRows(benchmark.items).length ? (
                  <div className="auction-benchmark-list">
                    {noonRows(benchmark.items).map((row) => (
                      <div key={row.instrument_id}>
                        <div>
                          {identity(row)}
                          <span
                            className={tone(auctionNumber(row.auction_pct))}
                          >
                            {percent(auctionNumber(row.auction_pct))}
                          </span>
                        </div>
                        <div className="auction-tags">
                          {noonLines(row.tags).map((tag, index) => (
                            <Badge key={index}>{tag}</Badge>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="auction-muted">
                    {benchmark.reason ||
                      "本次未取得可核验的同日基准，保持未知。"}
                  </p>
                )}
              </div>
            </Panel>
          </div>
          {gaps.length > 0 && (
            <Panel title="本次缺口" subtitle="缺报价不填零，也不据此认定停牌。">
              <div className="auction-panel-body">
                <ul className="auction-lines">
                  {gaps.map((line, index) => (
                    <li key={index}>{line}</li>
                  ))}
                </ul>
              </div>
            </Panel>
          )}
          <Panel>
            <details
              className="auction-close-review"
              open={closeOpen}
              onToggle={(event) => setCloseOpen(event.currentTarget.open)}
            >
              <summary>
                收盘后，回头核对这份观察{" "}
                <span>竞价至收盘价差 · 同日已发布行情</span>
              </summary>
              <div className="auction-panel-body">
                <div className="auction-close-head">
                  <p>事后核对保持独立，不改写盘前名单与判断。</p>
                  <button
                    className="btn small"
                    onClick={closeResult.reload}
                    disabled={closeResult.loading}
                  >
                    <RefreshCw size={12} />
                    读取盘后结果
                  </button>
                </div>
                {closeResult.loading && !closeReview && <Loading />}
                {closeResult.error && (
                  <ErrorBox
                    message={closeResult.error}
                    retry={closeResult.reload}
                  />
                )}
                {closeReview &&
                  !["available", "partial"].includes(closeReview.state) && (
                    <p className="auction-muted">
                      {closeReview.reason || "等待同日盘后增量发布。"}
                    </p>
                  )}
                {closeReview && hasCloseFacts && (
                  <>
                    <div className="auction-close-metrics">
                      <span>
                        可比{" "}
                        <b>
                          {integer(auctionNumber(closeReview.summary?.valid))}
                        </b>{" "}
                        /{" "}
                        {integer(auctionNumber(closeReview.summary?.requested))}
                        只
                      </span>
                      <span>
                        收盘高于 / 低于竞价{" "}
                        <b className="up">
                          {integer(
                            auctionNumber(closeReview.summary?.above_auction),
                          )}
                        </b>{" "}
                        /{" "}
                        <b className="down">
                          {integer(
                            auctionNumber(closeReview.summary?.below_auction),
                          )}
                        </b>
                        只
                      </span>
                      <span>
                        价差中位数{" "}
                        <b
                          className={tone(
                            auctionNumber(
                              closeReview.summary?.median_auction_to_close_pct,
                            ),
                          )}
                        >
                          {percent(
                            auctionNumber(
                              closeReview.summary?.median_auction_to_close_pct,
                            ),
                          )}
                        </b>
                      </span>
                      <span>
                        开盘价不一致{" "}
                        {integer(
                          auctionNumber(closeReview.summary?.open_mismatch),
                        )}
                        只 · 缺失{" "}
                        {integer(auctionNumber(closeReview.summary?.missing))}只
                      </span>
                    </div>
                    <div className="auction-table-wrap">
                      <table className="auction-table">
                        <thead>
                          <tr>
                            <th>原冻结股票</th>
                            <th>盘前参照</th>
                            <th>竞价价（元）</th>
                            <th>开盘（元）</th>
                            <th>收盘（元）</th>
                            <th>竞价至收盘价差</th>
                            <th>核对状态</th>
                          </tr>
                        </thead>
                        <tbody>
                          {closeItems.map((row) => (
                            <tr key={row.instrument_id}>
                              <td>{identity(row)}</td>
                              <td>{percent(auctionNumber(row.auction_pct))}</td>
                              <td>{num(auctionNumber(row.auction_price))}</td>
                              <td>{num(auctionNumber(row.open))}</td>
                              <td>{num(auctionNumber(row.close))}</td>
                              <td
                                className={tone(
                                  row.state === "observed"
                                    ? auctionNumber(row.auction_to_close_pct)
                                    : null,
                                )}
                              >
                                {row.state === "observed"
                                  ? percent(
                                      auctionNumber(row.auction_to_close_pct),
                                    )
                                  : "—"}
                              </td>
                              <td className="auction-row-state">
                                {row.reason ||
                                  (row.state === "observed"
                                    ? "同日价格已核验"
                                    : "待核验")}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
                <p className="auction-reading-note">
                  {closeReview?.notice ||
                    "只用同日已发布未复权价格核对，价差不等于真实成交收益或策略胜率。开盘与竞价价不一致的样本单列，不计入可比统计。"}
                </p>
              </div>
            </details>
          </Panel>
          <details className="auction-source-details">
            <summary>
              来源与留存证据 ·{" "}
              {integer(view?.snapshot?.evidence_count ?? sources.length)}份
            </summary>
            <div className="auction-panel-body">
              <p>
                来源：扶摇 / 同花顺。响应阶段{" "}
                {payload.quotes?.auction_phase || "未知"}；状态{" "}
                {payload.quotes?.data_status || "未知"}
                。每份原件与正文随业务备份保留。
              </p>
              {sources.map((row, index) => (
                <div className="auction-source" key={index}>
                  <strong>{sourceName(row)}</strong>
                  <span>
                    请求 {noonClock(row.requested_at)} · 接收{" "}
                    {noonClock(row.received_at)}
                  </span>
                  <code>{row.sha256 || "原件SHA未提供"}</code>
                </div>
              ))}
              <div className="auction-source">
                <strong>正文SHA256</strong>
                <code>{view?.snapshot?.content_sha256 || "未提供"}</code>
              </div>
            </div>
          </details>
        </>
      )}
      <p className="auction-boundary-note">
        观察边界：这是一次手动终态快照，不提供09:20—09:24的挂单轨迹，也不据未匹配量推断净流入或封单方向。读取页面不会自动采集或开启实时行情。
      </p>
    </div>
  );
}
