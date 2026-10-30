import { useState } from "react";
import {
  ArrowUpRight,
  CalendarDays,
  ChevronRight,
  FileText,
  History,
  Layers3,
  Link2,
  Pencil,
  ShieldCheck,
  Star,
} from "lucide-react";
import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Modal, Loading, ErrorBox } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import { dateTime } from "../../lib/format";
import { groups, groupKeys, columns } from "./zodiacPresentation";
import { MetricCell, Memberships } from "./ZodiacCells";
import ZodiacSelect from "./ZodiacSelect";
export default function ZodiacDetails({
  item,
  onClose,
  onStock,
  onVerify,
  onEdit,
}: {
  item: Row;
  onClose: () => void;
  onStock: () => void;
  onVerify: () => void;
  onEdit: (row: Row) => void;
}) {
  const result = useApi<Row>(`/zodiac-pool-members/${item.member_id}/metrics`),
    [group, setGroup] = useState<string>("综合"),
    [period, setPeriod] = useState("D");
  const row = result.data?.item || item;
  const technical =
    group === "技术趋势" && period !== "D"
      ? {
          ...row,
          ...Object.fromEntries(
            groupKeys.技术趋势.map((k) => [
              k,
              result.data?.period_metrics?.[period]?.[k] ?? null,
            ]),
          ),
        }
      : row;
  const keyMetrics = [
    "return_ytd_pct",
    "change_30d_pct",
    "limit_up_ytd",
    "since_entry_pct",
  ];
  const shown = groupKeys[group].filter((k) => k !== "current_topics");
  const events: Row[] = result.data?.retained_events || [];
  return (
    <Modal title="个股观察" onClose={onClose} wide>
      <div className="zodiac-detail">
        <div className="zodiac-detail-hero">
          <span className="zodiac-stock-avatar">
            {row.name?.slice(0, 1) || "股"}
          </span>
          <div className="zodiac-detail-heading">
            <h3>
              {row.name}
              {row.focused && (
                <Star size={16} className="zodiac-star" fill="currentColor" />
              )}
            </h3>
            <div>
              <CopyStockCode instrumentId={row.instrument_id} variant="full" />
              <span>{row.board_label}</span>
              <span>第{row.round_number}轮</span>
              <span className="zodiac-status-pill">
                {row.status === "ended" ? "已移出" : "观察中"}
              </span>
            </div>
          </div>
          <button className="btn primary" onClick={onStock}>
            <ArrowUpRight size={15} />
            查看K线
          </button>
        </div>
        <div className="zodiac-detail-dates">
          <CalendarDays size={13} />
          <span>行情截止 {row.metrics_as_of}</span>
          <i />
          <span>加入 {row.added_business_date}</span>
          {row.end_business_date && (
            <>
              <i />
              <span>移出 {row.end_business_date}</span>
            </>
          )}
          <span className="zodiac-detail-year">{row.stats_year}年内统计</span>
        </div>
        <div className="zodiac-detail-highlights">
          {keyMetrics.map((key) => {
            const col = columns.find((c) => c.key === key)!;
            return (
              <div key={key}>
                <small>{col.label}</small>
                <MetricCell row={row} column={col} />
              </div>
            );
          })}
        </div>
        {result.loading && <Loading />}
        {result.error && (
          <ErrorBox message={result.error} retry={result.reload} />
        )}
        <div className="zodiac-detail-layout">
          <section className="zodiac-detail-primary">
            <div className="zodiac-detail-tabs">
              {groups.map((g) => (
                <button
                  className={group === g ? "active" : ""}
                  key={g}
                  onClick={() => setGroup(g)}
                >
                  {g}
                </button>
              ))}
            </div>
            <div className="zodiac-detail-group-head">
              <h4>{group === "综合" ? "行情概览" : group}</h4>
              {group === "技术趋势" ? (
                <ZodiacSelect
                  aria-label="技术周期"
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                >
                  <option value="D">日线</option>
                  <option value="W">完整周线</option>
                  <option value="M">完整月线</option>
                </ZodiacSelect>
              ) : (
                <small>截止 {row.metrics_as_of}</small>
              )}
            </div>
            <div className="zodiac-detail-grid">
              {shown.map((key) => {
                const column = columns.find((c) => c.key === key)!;
                return (
                  <div key={key}>
                    <small>{column.label}</small>
                    <MetricCell row={technical} column={column} />
                  </div>
                );
              })}
            </div>
            <p className="zodiac-detail-hint">
              <ShieldCheck size={14} />
              {group === "技术趋势"
                ? "均线按所选周期计数；停牌缺口或预热不足显示待核验。"
                : "待核验项保留未知，悬停数值可查看原因与统计口径。"}
            </p>
            <details className="zodiac-detail-section">
              <summary>
                <FileText size={16} />
                <span>已留存事件与研究</span>
                <small>{events.length}项</small>
                <ChevronRight size={15} />
              </summary>
              <div className="zodiac-section-content">
                {events.length ? (
                  events.map((event) => (
                    <div className="zodiac-event-line" key={event.id}>
                      <span>{event.date}</span>
                      <strong>{event.label}</strong>
                      {event.state && <small>{event.state}</small>}
                    </div>
                  ))
                ) : (
                  <div className="zodiac-inline-empty">
                    暂未留存相关事件
                    <p>仅展示已保存资料，不会因查看详情发起采集。</p>
                  </div>
                )}
              </div>
            </details>
            <details className="zodiac-detail-section">
              <summary>
                <History size={16} />
                <span>观察维护记录</span>
                <small>{result.data?.history?.length ?? 0}次</small>
                <ChevronRight size={15} />
              </summary>
              <div className="zodiac-section-content zodiac-timeline">
                {result.data?.history?.map((event: Row) => (
                  <div className="zodiac-audit" key={event.audit_id}>
                    <span className="zodiac-timeline-dot" />
                    <div>
                      <strong>
                        {(
                          {
                            add: "加入观察",
                            edit: "更新观察信息",
                            end: "移出观察",
                            verify_baseline: "重新核验基准",
                          } as Row
                        )[event.action] || event.action}
                      </strong>
                      <small>{dateTime(event.occurred_at_ms)}</small>
                      <p>
                        {event.after.reason || "未填写观察理由"}
                        {event.after.end_reason
                          ? ` · 移出：${event.after.end_reason}`
                          : ""}
                      </p>
                      <details>
                        <summary>查看修改前后</summary>
                        <pre>
                          {JSON.stringify(
                            { 修改前: event.before, 修改后: event.after },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </div>
                  </div>
                ))}
              </div>
            </details>
          </section>
          <aside className="zodiac-detail-aside">
            <section className="zodiac-aside-card">
              <h4>
                <Layers3 size={15} />
                行业与概念
              </h4>
              <Memberships row={row} />
              <p className="zodiac-aside-caption">
                当前归属 · {row.current_topics_observed_date || "日期未知"}
              </p>
            </section>
            <section className="zodiac-aside-card">
              <h4>
                <FileText size={15} />
                我的观察
                <button
                  className="icon-btn"
                  aria-label="编辑观察信息"
                  onClick={() => onEdit(row)}
                >
                  <Pencil size={14} />
                </button>
              </h4>
              <span className="zodiac-relation-pill">
                {row.custom_label || row.relation_label}
              </span>
              <p className="zodiac-user-text">
                {row.reason || "还没有写下观察理由"}
              </p>
              {row.links?.length > 0 && (
                <div className="zodiac-note-links">
                  {row.links.map((link: string, index: number) => (
                    <a
                      key={link}
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <Link2 size={13} />
                      依据链接 {index + 1}
                      <ArrowUpRight size={12} />
                    </a>
                  ))}
                </div>
              )}
              {row.end_reason && (
                <p className="zodiac-aside-caption">
                  移出原因：{row.end_reason}
                </p>
              )}
            </section>
            <details className="zodiac-aside-card zodiac-evidence">
              <summary>
                <ShieldCheck size={15} />
                <span>基准与来源</span>
                <ChevronRight size={14} />
              </summary>
              <dl>
                <dt>基准行情日</dt>
                <dd>{row.added_business_date}</dd>
                <dt>原始收盘</dt>
                <dd>
                  {row.entry_price ?? "待核验"}
                  {row.entry_price != null ? " 元" : ""}
                </dd>
                <dt>行情口径</dt>
                <dd>未复权原始收盘</dd>
                <dt>基准状态</dt>
                <dd>
                  {row.entry_price == null ? "缺少有效报价" : "已冻结保存"}
                </dd>
              </dl>
              {row.entry_price == null && (
                <button className="btn" onClick={onVerify}>
                  重新核验缺失基准
                </button>
              )}
              <p className="zodiac-aside-caption">
                修改理由或跨年切换不会覆盖原基准。
              </p>
              <details>
                <summary>查看完整核验凭据</summary>
                <pre>{JSON.stringify(row.entry_basis, null, 2)}</pre>
              </details>
            </details>
          </aside>
        </div>
      </div>
    </Modal>
  );
}
