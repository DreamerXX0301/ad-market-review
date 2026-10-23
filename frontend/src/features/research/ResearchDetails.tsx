import type { ReactNode } from "react";
import {
  Activity,
  Building2,
  FileText,
  Landmark,
  Users,
  Wallet,
} from "lucide-react";
import type { Row } from "../../lib/api";
import { amount, dateTime, num, textValue } from "../../lib/format";
import { isHiddenIndicatorField } from "../../lib/indicatorVisibility";
import { Badge, Cell, Panel, Table } from "../../components/UI";
import { fieldLabel } from "../strategies/Conditions";
import CopyStockCode from "../../components/CopyStockCode";
import "./Research.css";
import ResearchEvidenceDetails from "./ResearchEvidenceDetails";

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
export const researchAmount = (value: unknown) =>
  finite(value) ? amount(value) : "—";
const multiple = (value: unknown) => (finite(value) ? num(value) + " 倍" : "—");
const signed = (value: unknown, suffix = "%", digits = 2) =>
  finite(value) ? (value > 0 ? "+" : "") + num(value, digits) + suffix : "—";
const direction = (value: unknown) =>
  !finite(value) || value === 0 ? "flat" : value > 0 ? "increase" : "decrease";
const reportPeriod = (value: unknown) => {
  const match = typeof value === "string" && /^(\d{4})-([1-4])$/.exec(value);
  return match
    ? match[1] +
        "年" +
        ["一季报", "半年报", "三季报", "年报"][Number(match[2]) - 1]
    : textValue(value);
};
const dateOnly = (value: unknown) => {
  if (typeof value !== "string") return null;
  const day = value.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const parsed = new Date(day + "T00:00:00Z");
  return Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === day
    ? day
    : null;
};

/** Compare a verified pair of household counts, never nulls or unrelated rows. */
export function shareholderChange(row: Row) {
  const current = row.holder_count,
    previous = row.previous_holder_count;
  const priorDate = dateOnly(
    row.previous_report_period || row.raw?.PRE_END_DATE,
  );
  const day = dateOnly(row.report_period);
  const unknown = (reason: string) => ({
    delta: null,
    percent: null,
    previousDate: priorDate,
    reason,
  });
  if (!finite(current) || current < 0 || !Number.isInteger(current))
    return unknown("本期股东户数待核验");
  if (!finite(previous) || previous < 0 || !Number.isInteger(previous))
    return unknown("未取得可核验的上期户数");
  if (row.unit !== "户" || !priorDate || !day || priorDate >= day)
    return unknown("前期日期或统计口径待核验");
  const delta = current - previous;
  const percent = previous > 0 ? (delta / previous) * 100 : null;
  if (
    finite(row.change_ratio_pct) &&
    percent != null &&
    Math.abs(row.change_ratio_pct - percent) > 0.05
  )
    return unknown("来源变化比例与本期、上期户数不一致");
  return {
    delta,
    percent,
    previousDate: priorDate,
    reason: previous === 0 ? "上期为零，变化比例不可计算" : null,
  };
}

function Metric({
  label,
  value,
  note,
  accent = "blue",
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  accent?: string;
}) {
  return (
    <div className={"research-metric research-metric--" + accent}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}
function Section({
  id,
  number,
  title,
  description,
  icon,
  children,
}: {
  id: string;
  number: string;
  title: string;
  description: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div id={id} className="research-section-anchor">
      <Panel
        className="research-section"
        title={
          <span className="research-section-title">
            <span className="research-section-icon">{icon}</span>
            {title}
            <span className="research-section-number">{number}</span>
          </span>
        }
        subtitle={description}
      >
        {children}
      </Panel>
    </div>
  );
}
function Notes({
  title = "来源、日期与统计口径",
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  return (
    <details className="research-notes">
      <summary>{title}</summary>
      <div>{children}</div>
    </details>
  );
}

function LegacyResearchDetails({
  report,
  onMaterial,
}: {
  report: Row;
  onMaterial: (material: Row) => void;
}) {
  const instrument = report.instrument || {},
    valuation = report.valuation || {},
    cap = report.market_cap || {};
  const statements = report.financials?.statements || {};
  const income = [...(statements.income || [])].sort((a, b) =>
    String(b.report_period).localeCompare(String(a.report_period)),
  );
  const latestIncome = income[0] || {};
  const metrics: Row[] = report.financials?.metrics || [];
  const holders = [...(report.shareholders?.items || [])].sort((a, b) =>
    String(b.report_period).localeCompare(String(a.report_period)),
  );
  const holderRows = holders.map((row) => ({
    ...row,
    ...shareholderChange(row),
  }));
  const latestHolder = holderRows[0];
  const announcements = report.announcements || {},
    complete = announcements.coverage?.complete === true;
  const topics = Array.isArray(report.topics) ? report.topics : [];
  const daily = (report.trends || []).find(
    (trend: Row) => trend.period === "D",
  );
  const totalGaps = Array.isArray(report.gaps) ? report.gaps.length : 0;
  const announcementColumns = [
    {
      key: "published_at_ms",
      title: "披露日期",
      render: (value: unknown) => (
        <span className="research-date">{dateTime(value).split(" ")[0]}</span>
      ),
    },
    {
      key: "title",
      title: "公告标题",
      render: (value: unknown, row: Row) => (
        <button
          className="link research-announcement-title"
          onClick={() => onMaterial({ ...row, data: row.raw || row })}
        >
          {textValue(value)}
        </button>
      ),
    },
    {
      key: "categories",
      title: "事件标签",
      render: (value: unknown) => (
        <div className="research-inline-tags">
          {Array.isArray(value) && value.length
            ? value.map((tag) => <Badge key={String(tag)}>{String(tag)}</Badge>)
            : "—"}
        </div>
      ),
    },
    {
      key: "url",
      title: "原文",
      sortable: false,
      render: (_: unknown, row: Row) => (
        <span className="research-document-links">
          {typeof row.url === "string" && /^https?:\/\//.test(row.url) && (
            <a href={row.url} target="_blank" rel="noreferrer">
              详情 ↗
            </a>
          )}
          {typeof row.document_url === "string" &&
            /^https?:\/\//.test(row.document_url) && (
              <a href={row.document_url} target="_blank" rel="noreferrer">
                PDF ↗
              </a>
            )}
        </span>
      ),
    },
  ];
  return (
    <div className="research-report-content">
      <section id="research-overview" className="research-company">
        <div className="research-company-heading">
          <div className="research-company-mark">
            <Building2 size={28} />
          </div>
          <div className="research-company-name">
            <span className="research-eyebrow">公司概览 · 已留存研究</span>
            <h2>
              {instrument.name || instrument.instrument_id || "公司研究"}{" "}
              <CopyStockCode
                code={instrument.code}
                instrumentId={instrument.instrument_id}
              />
            </h2>
            <p>
              {(
                {
                  SH: "上海证券交易所",
                  SZ: "深圳证券交易所",
                  BJ: "北京证券交易所",
                } as Record<string, string>
              )[instrument.exchange] || "上市市场待核验"}
              {instrument.list_date
                ? " · " + instrument.list_date + " 上市"
                : " · 上市日期待核验"}
            </p>
          </div>
          <div className="research-company-price">
            <span>报告行情收盘价</span>
            <strong>
              {finite(daily?.observations?.close)
                ? num(daily.observations.close)
                : "—"}
              <small> 元</small>
            </strong>
            <span>行情截止 {report.data_cutoff || "待核验"}</span>
          </div>
        </div>
        <div className="research-company-footer">
          <div className="research-inline-tags">
            <span className="research-inline-label">已留存题材</span>
            {topics.length ? (
              topics.map((topic: unknown) => (
                <Badge key={textValue(topic)}>{textValue(topic)}</Badge>
              ))
            ) : (
              <span className="muted">尚无可核验归属</span>
            )}
          </div>
          <span
            className={"research-coverage-pill " + (totalGaps ? "partial" : "")}
          >
            {totalGaps ? totalGaps + " 项材料存在缺口" : "以各项材料覆盖为准"}
          </span>
        </div>
      </section>

      <nav className="research-report-nav" aria-label="研究报告章节">
        {[
          ["research-valuation", "估值与市值"],
          ["research-financials", "财务与业绩"],
          ["research-holders", "股东变化"],
          ["research-announcements", "公告事件"],
          ["research-trends", "趋势观察"],
        ].map(([id, label]) => (
          <button
            type="button"
            key={id}
            onClick={() =>
              document
                .getElementById(id)
                ?.scrollIntoView({ behavior: "smooth", block: "start" })
            }
          >
            {label}
          </button>
        ))}
      </nav>

      <Section
        id="research-valuation"
        number="01"
        title="估值与市值"
        icon={<Landmark size={19} />}
        description="估值采用来源最新快照；市值按独立来源日期展示"
      >
        <div className="research-section-body">
          <div className="research-metrics research-metrics--four">
            <Metric
              label="市盈率 PE · TTM"
              value={multiple(valuation.pe_ttm)}
              note="滚动十二个月"
            />
            <Metric
              label="市净率 PB · MRQ"
              value={multiple(valuation.pb_mrq)}
              note="最近报告期"
              accent="violet"
            />
            <Metric
              label="市销率 PS · TTM"
              value={multiple(valuation.ps_ttm)}
              note="滚动十二个月"
              accent="teal"
            />
            <Metric
              label="市现率 PCF · TTM"
              value={multiple(valuation.pcf_ttm)}
              note="滚动十二个月"
              accent="amber"
            />
          </div>
          <div className="research-cap-strip">
            <div>
              <span>总市值</span>
              <strong>{researchAmount(cap.total_market_cap_cny)}</strong>
            </div>
            <div>
              <span>流通市值</span>
              <strong>{researchAmount(cap.float_market_cap_cny)}</strong>
            </div>
            <p>
              市值来源日 <b>{cap.source_date || "尚未取得"}</b>
            </p>
          </div>
          <Notes>
            <p>
              估值元数据{" "}
              {valuation.source_timestamp_ms
                ? dateTime(valuation.source_timestamp_ms)
                : "尚未取得"}{" "}
              · 市盈率 PE / MRQ {multiple(valuation.pe_mrq)}。
            </p>
            <p>
              {valuation.scope || "估值材料尚未取得。"}{" "}
              负估值保留原值，不解释为低估；市值不视作行情截止日的历史市值。
            </p>
          </Notes>
        </div>
      </Section>

      <Section
        id="research-financials"
        number="02"
        title="财务与业绩"
        icon={<Wallet size={19} />}
        description={
          "最近已披露报告期 " +
          (latestIncome.report_period || "尚未取得") +
          " · 金额自动换算万元 / 亿元"
        }
      >
        <div className="research-section-body">
          <div className="research-metrics research-metrics--four">
            <Metric
              label="营业收入"
              value={researchAmount(latestIncome.operating_income)}
              note={latestIncome.report_period || "报告期待核验"}
            />
            <Metric
              label="归母净利润"
              value={researchAmount(latestIncome.parent_holder_net_profit)}
              note="归属于母公司股东"
              accent="violet"
            />
            <Metric
              label="基本每股收益"
              value={
                finite(latestIncome.basic_eps)
                  ? num(latestIncome.basic_eps) + " 元/股"
                  : "—"
              }
              note="沿用已披露报表"
              accent="teal"
            />
            <Metric
              label="经营净现金流"
              value={researchAmount(
                (statements.cashflow || []).find(
                  (row: Row) =>
                    row.report_period === latestIncome.report_period,
                )?.act_cash_flow_net,
              )}
              note="与上述财报期末一致"
              accent="amber"
            />
          </div>
          {!!metrics.length && (
            <div className="research-financial-ratios">
              {metrics.map((row: Row) => (
                <div key={String(row.field || row.name)}>
                  <span>{row.name}</span>
                  <strong
                    className={
                      row.field?.includes("yoy")
                        ? "research-change " + direction(row.value)
                        : ""
                    }
                  >
                    {finite(row.value)
                      ? row.field?.includes("yoy")
                        ? signed(row.value)
                        : num(row.value) + " " + (row.unit || "")
                      : "—"}
                  </strong>
                  <small>
                    报告期{" "}
                    {row.report_period
                      ? reportPeriod(row.report_period)
                      : "待核验"}
                  </small>
                </div>
              ))}
            </div>
          )}
          <div className="research-subheading">
            <h3>利润与业绩明细</h3>
            <span>已披露合并报表 · 可按表头排序</span>
          </div>
          <Table
            rows={income}
            pageSize={8}
            columns={[
              { key: "report_period", title: "报告期末" },
              {
                key: "published_at_ms",
                title: "披露日期",
                render: (value) => dateTime(value).split(" ")[0],
              },
              {
                key: "operating_income",
                title: "营业收入",
                render: researchAmount,
              },
              {
                key: "parent_holder_net_profit",
                title: "归母净利润",
                render: researchAmount,
              },
              { key: "net_profit", title: "净利润", render: researchAmount },
              {
                key: "basic_eps",
                title: "基本每股收益",
                render: (value) =>
                  finite(value) ? num(value) + " 元/股" : "—",
              },
            ]}
            emptyTitle="尚未取得可核验的利润表"
          />
          <div className="research-statements">
            <div>
              <div className="research-subheading">
                <h3>资产负债</h3>
                <span>按报告期</span>
              </div>
              <Table
                rows={statements.balance || []}
                pageSize={8}
                columns={[
                  { key: "report_period", title: "报告期末" },
                  {
                    key: "assets_total",
                    title: "总资产",
                    render: researchAmount,
                  },
                  {
                    key: "total_debt",
                    title: "总负债",
                    render: researchAmount,
                  },
                  {
                    key: "holder_equity_total",
                    title: "股东权益",
                    render: researchAmount,
                  },
                ]}
                emptyTitle="尚未取得可核验的资产负债表"
              />
            </div>
            <div>
              <div className="research-subheading">
                <h3>现金流</h3>
                <span>按报告期</span>
              </div>
              <Table
                rows={statements.cashflow || []}
                pageSize={8}
                columns={[
                  { key: "report_period", title: "报告期末" },
                  {
                    key: "act_cash_flow_net",
                    title: "经营净现金流",
                    render: researchAmount,
                  },
                  {
                    key: "invest_cash_flow_net",
                    title: "投资净现金流",
                    render: researchAmount,
                  },
                  {
                    key: "financing_cash_flow_net",
                    title: "筹资净现金流",
                    render: researchAmount,
                  },
                ]}
                emptyTitle="尚未取得可核验的现金流表"
              />
            </div>
          </div>
          <Notes>
            <p>
              {report.financials?.scope || "财务材料尚未取得。"}{" "}
              季度值沿用来源口径，不自行拆分单季度。金额换算为显示单位；零与缺失分别展示。
            </p>
          </Notes>
        </div>
      </Section>

      <Section
        id="research-holders"
        number="03"
        title="股东人数变化"
        icon={<Users size={19} />}
        description="红色表示较上期增加，绿色表示减少；仅说明人数变化，不代表利好或利空"
      >
        <div className="research-section-body">
          <div className="research-holder-summary">
            <div>
              <span>最新已披露股东人数</span>
              <strong>
                {latestHolder && finite(latestHolder.holder_count)
                  ? num(latestHolder.holder_count, 0)
                  : "—"}
                <small> 户</small>
              </strong>
              <p>统计截止 {latestHolder?.report_period || "尚未取得"}</p>
            </div>
            <div
              className={
                "research-holder-change " + direction(latestHolder?.delta)
              }
            >
              <span>较上期户数变化</span>
              <strong>{signed(latestHolder?.delta, " 户", 0)}</strong>
              <p>
                {latestHolder && finite(latestHolder.percent)
                  ? signed(latestHolder.percent) +
                    " · 相较 " +
                    latestHolder.previousDate
                  : latestHolder?.reason || "前期数据待核验"}
              </p>
            </div>
          </div>
          <Table
            rows={holderRows}
            pageSize={8}
            columns={[
              { key: "report_period", title: "统计截止日" },
              { key: "published_date", title: "披露日期" },
              {
                key: "holder_count",
                title: "股东户数",
                render: (value) =>
                  finite(value) ? num(value, 0) + " 户" : "—",
              },
              {
                key: "previous_holder_count",
                title: "上期户数",
                render: (value) =>
                  finite(value) ? num(value, 0) + " 户" : "—",
              },
              {
                key: "delta",
                title: "较上期增减",
                render: (value, row) => (
                  <span
                    className={"research-change " + direction(value)}
                    title={row.reason || "相较 " + row.previousDate}
                  >
                    {signed(value, " 户", 0)}
                  </span>
                ),
              },
              {
                key: "percent",
                title: "较上期变化",
                render: (value, row) => (
                  <span
                    className={"research-change " + direction(value)}
                    title={row.reason || "相较 " + row.previousDate}
                  >
                    {signed(value)}
                  </span>
                ),
              },
              {
                key: "period_market_cap_cny",
                title: "统计期市值",
                render: researchAmount,
              },
            ]}
            emptyTitle="尚未取得可核验的股东人数"
            emptyDescription="保留缺口，不按股本或行情推算户数。"
          />
          <Notes title="股东人数的前期口径与来源">
            <p>
              {report.shareholders?.coverage?.scope ||
                "最近已披露的股东人数，最多列示本次取得的8期；不是实时股东名册。"}{" "}
              变化按来源同一记录中的本期、上期户数和前期日期核对；没有前期、单位不明或比例冲突时显示“—”。
            </p>
            {holderRows.some((row) => row.reason) && (
              <ul>
                {holderRows
                  .filter((row) => row.reason)
                  .map((row) => (
                    <li key={row.report_period}>
                      {row.report_period}：{row.reason}
                    </li>
                  ))}
              </ul>
            )}
          </Notes>
        </div>
      </Section>

      <Section
        id="research-announcements"
        number="04"
        title="公告与披露事件"
        icon={<FileText size={19} />}
        description={
          "查询窗口 " +
          (report.material_window?.start || "待核验") +
          " ～ " +
          (report.material_window?.end || "待核验")
        }
      >
        <div className="research-section-body">
          <div className="research-announcement-summary">
            <div>
              <strong>
                {Array.isArray(announcements.items)
                  ? announcements.items.length
                  : "—"}
              </strong>
              <span>本次取得公告</span>
            </div>
            <div>
              <strong>
                {Array.isArray(announcements.items) &&
                Array.isArray(report.events)
                  ? report.events.length
                  : "—"}
              </strong>
              <span>匹配标题事件</span>
            </div>
            <Badge tone={complete ? "good" : "warn"}>
              {complete ? "请求窗口分页完整" : "公告覆盖待核验"}
            </Badge>
          </div>
          {!complete && (
            <p className="research-inline-warning">
              {announcements.coverage?.reason ||
                "来源未提供可核验的完整窗口；未列示不能解释为没有公告或风险事件。"}
            </p>
          )}
          <div className="research-subheading">
            <h3>公告明细</h3>
            <span>按本次已取得材料排序 · 每页10条</span>
          </div>
          <Table
            rows={announcements.items || []}
            columns={announcementColumns}
            sorting="local"
            pageSize={10}
            emptyTitle="本次没有取得可列示公告"
            emptyDescription="来源缺口不等于公司没有公告。"
          />
          <div className="research-subheading">
            <h3>减持、回购与其他披露事件</h3>
            <span>公告标题分类</span>
          </div>
          <Table
            rows={report.events || []}
            columns={announcementColumns}
            sorting="local"
            pageSize={10}
            emptyTitle="已取得公告中没有匹配的标题事件"
          />
          <Notes title="公告覆盖、事件分类与原文">
            <p>
              公告以官方详情及PDF为准。事件仅按标题关键词整理，计划、进展和完成公告分别保留；不将它们认定为实际交易结果。没有匹配记录不代表不存在减持、回购或监管事项。
            </p>
          </Notes>
        </div>
      </Section>

      <Section
        id="research-trends"
        number="05"
        title="多周期趋势观察"
        icon={<Activity size={19} />}
        description="已有日、周、月行情的描述性观察；走势图保留在本页行情区"
      >
        <div className="research-section-body research-trends-grid">
          {(report.trends || []).map((trend: Row) => (
            <article className="research-trend-card" key={trend.period}>
              <div className="research-trend-heading">
                <span>{trend.label}观察</span>
                <Badge>{trend.complete_bars ?? "—"} 根完整周期</Badge>
              </div>
              <strong className="research-trend-price">
                {finite(trend.observations?.close)
                  ? num(trend.observations.close)
                  : "—"}
                <small> 观察收盘</small>
              </strong>
              <p className="research-trend-date">
                指标截止 {trend.latest_complete_date || "尚无完整周期"}
              </p>
              <div className="research-trend-facts">
                {trend.facts?.length ? (
                  trend.facts.map((fact: string) => (
                    <span key={fact}>{fact}</span>
                  ))
                ) : (
                  <span className="muted">可比指标仍在预热</span>
                )}
              </div>
              <dl className="research-trend-key-values">
                {["ma5", "ma20", "volume_ratio"].map((field) => (
                  <div key={field}>
                    <dt>{fieldLabel(field)}</dt>
                    <dd>
                      <Cell field={field} value={trend.observations?.[field]} />
                    </dd>
                  </div>
                ))}
              </dl>
              <Notes title="全部指标与周期覆盖">
                <p>
                  样本 {trend.data_begin || "待核验"} ～{" "}
                  {trend.data_end || "待核验"} · {trend.sample_bars ?? "—"}{" "}
                  根聚合周期 · 连续预热 {trend.indicator_warmup_bars ?? "—"} 根
                  · 未复权。
                </p>
                <Table
                  rows={Object.entries(trend.observations || {})
                    .filter(([field]) => !isHiddenIndicatorField(field))
                    .map(([field, value]) => ({ field, value }))}
                  columns={[
                    {
                      key: "field",
                      title: "观察指标",
                      render: (value) => fieldLabel(value),
                    },
                    {
                      key: "value",
                      title: "实际值",
                      render: (value, row) => (
                        <Cell field={row.field} value={value} />
                      ),
                    },
                  ]}
                  pageSize={20}
                />
                {trend.current_period && trend.period !== "D" && (
                  <div className="research-period-preview">
                    <h4>
                      当前周期聚合预览
                      {trend.current_period.complete
                        ? ""
                        : " · 尚未完成或报价不完整"}
                    </h4>
                    <dl>
                      {[
                        ["date", "日期"],
                        ["open", "开盘"],
                        ["high", "最高"],
                        ["low", "最低"],
                        ["close", "收盘"],
                        ["volume", "成交量"],
                        ["amount", "成交额"],
                      ].map(([field, label]) => (
                        <div key={field}>
                          <dt>{label}</dt>
                          <dd>
                            {field === "date" ? (
                              textValue(trend.current_period[field])
                            ) : (
                              <Cell
                                field={field}
                                value={trend.current_period[field]}
                              />
                            )}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}
                {trend.warnings?.map((warning: string) => (
                  <p key={warning}>{warning}</p>
                ))}
              </Notes>
            </article>
          ))}
          {!report.trends?.length && (
            <p className="muted">本次报告未取得可核验的多周期观察。</p>
          )}
        </div>
      </Section>
    </div>
  );
}

export default function ResearchDetails(props: {
  report: Row;
  onMaterial: (material: Row) => void;
}) {
  return props.report.schema_version === 2 ? (
    <ResearchEvidenceDetails
      key={
        props.report.report_id ||
        props.report.job_id ||
        `${props.report.instrument?.instrument_id}-${props.report.research_as_of_ms}`
      }
      {...props}
      legacyDetails={<LegacyResearchDetails {...props} />}
    />
  ) : (
    <LegacyResearchDetails {...props} />
  );
}
