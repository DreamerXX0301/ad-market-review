import type { ReactNode } from "react";
import {
  Activity,
  ArrowUpRight,
  Building2,
  FileCheck2,
  FileText,
  Network,
  Wallet,
} from "lucide-react";
import type { Row } from "../../lib/api";
import { amount, dateTime, num, percent, textValue } from "../../lib/format";
import { Badge, Panel, Table } from "../../components/UI";
import { CandleChart } from "../../components/Charts";
import CopyStockCode from "../../components/CopyStockCode";
import {
  researchClaim,
  researchEvidenceRefs,
  researchRows,
  researchState,
  researchUrl,
  scrollResearchSection,
} from "./researchEvidencePresentation";

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const valueText = (value: unknown) => (finite(value) ? num(value) : "—");
const listText = (value: unknown) => (typeof value === "string" ? value : "");
const sections = [
  ["overview", "调研总览"],
  ["company", "公司与产业链"],
  ["valuation", "财务与机构观点"],
  ["news", "事件与风险"],
  ["technical", "趋势与位置"],
  ["evidence", "证据与核查"],
];
function OriginalLink({
  url,
  children = "原文",
}: {
  url: unknown;
  children?: ReactNode;
}) {
  const href = researchUrl(url);
  return href ? (
    <a href={href} target="_blank" rel="noreferrer">
      {children} <ArrowUpRight size={12} />
    </a>
  ) : null;
}
function Block({
  name,
  title,
  subtitle,
  icon,
  children,
}: {
  name: string;
  title: string;
  subtitle: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={"research-v2-" + name} className="research-section-anchor">
      <Panel
        className="research-section"
        title={
          <span className="research-section-title">
            <span className="research-section-icon">{icon}</span>
            {title}
            <span className="research-section-number">
              {String(sections.findIndex(([id]) => id === name) + 1).padStart(
                2,
                "0",
              )}
            </span>
          </span>
        }
        subtitle={subtitle}
      >
        <div className="research-section-body">{children}</div>
      </Panel>
    </section>
  );
}
function Unknown({ children }: { children: ReactNode }) {
  return <p className="research-evidence-empty">{children}</p>;
}
export default function ResearchEvidenceDetails({
  report,
  onMaterial,
  legacyDetails,
}: {
  report: Row;
  onMaterial: (row: Row) => void;
  legacyDetails: ReactNode;
}) {
  const instrument = report.instrument || {};
  const evidence = researchRows(report.evidence);
  const profile = report.company_profile || {};
  const chain = report.industry_chain || {};
  const technical = report.technical_assessment || {};
  const periods = researchRows(technical.periods);
  const statements = report.financials?.statements || {};
  const income =
    researchRows(statements.income).sort((a, b) =>
      String(b.report_period).localeCompare(String(a.report_period)),
    )[0] || {};
  const overview = report.overview || {};
  const coverage = researchRows(report.source_coverage);
  const claims = researchRows(report.claims);
  const news = researchRows(report.news?.items);
  const institutions = report.institution_views || {};
  function Refs({ ids }: { ids: unknown }) {
    const refs = researchEvidenceRefs(ids, evidence);
    return refs.length ? (
      <span className="research-evidence-refs">
        {refs.map((ref) => (
          <a
            key={ref.id}
            href={"#research-evidence-" + ref.index}
            onClick={(event) =>
              scrollResearchSection(event, "research-evidence-" + ref.index)
            }
            title={evidence[ref.index].title}
          >
            证据 {ref.index + 1}
          </a>
        ))}
      </span>
    ) : null;
  }
  return (
    <div className="research-report-content research-evidence-report">
      <nav className="research-report-nav" aria-label="六区调研报告章节">
        {sections.map(([id, label]) => (
          <a
            key={id}
            href={"#research-v2-" + id}
            onClick={(event) =>
              scrollResearchSection(event, "research-v2-" + id)
            }
          >
            {label}
          </a>
        ))}
      </nav>
      <Block
        name="overview"
        title="调研总览"
        subtitle="事实、来源表述与待验证线索分开阅读"
        icon={<FileCheck2 size={19} />}
      >
        <div className="research-evidence-hero">
          <div>
            <span className="research-eyebrow">证据型个股调研</span>
            <h2>
              {instrument.name || instrument.instrument_id || "公司研究"}{" "}
              <CopyStockCode instrumentId={instrument.instrument_id} />
            </h2>
            <p>
              {listText(overview.summary) || "本次材料尚不足以形成调研摘要。"}
            </p>
          </div>
          <div className="research-evidence-score">
            <strong>{evidence.length}</strong>
            <span>份可追溯材料</span>
          </div>
        </div>
        <div className="research-evidence-meta">
          <span>行情截止 {report.data_cutoff || "待核验"}</span>
          <span>材料查询 {dateTime(report.research_as_of_ms)}</span>
          <span>缓存到期 {dateTime(report.cache?.expires_at_ms)}</span>
        </div>
        <div className="research-evidence-highlights">
          {researchRows(overview.highlights).map((item, index) => (
            <p key={index}>
              {listText(item.text)} <Refs ids={item.evidence_ids} />
            </p>
          ))}
        </div>
        {!!claims.length && (
          <div className="research-claim-list">
            {claims.slice(0, 6).map((claim, index) => (
              <div key={claim.claim_id || index}>
                <Badge>{researchClaim(claim.kind)}</Badge>
                <p>
                  {listText(claim.text)} <Refs ids={claim.evidence_ids} />
                </p>
              </div>
            ))}
          </div>
        )}
        <p className="research-evidence-caption">
          {listText(overview.notice) ||
            "当前手动调研，材料日期与行情截止分别核对。"}{" "}
          七日访问缓存不会刷新材料，需要最新信息时手动重新运行。
        </p>
      </Block>
      <Block
        name="company"
        title="公司与产业链"
        subtitle="主营构成与已披露关系；题材标签不作为客户或供货证明"
        icon={<Network size={19} />}
      >
        <div className="research-evidence-heading">
          <h3>公司做什么</h3>
          <Badge>{researchState(profile.state)}</Badge>
        </div>
        <p className="research-business-summary">
          {listText(profile.main_business) || "主营业务待可核验材料补充"}{" "}
          <Refs ids={profile.evidence_ids} />
        </p>
        {profile.business_scope && <p>{listText(profile.business_scope)}</p>}
        {profile.reason && <Unknown>{listText(profile.reason)}</Unknown>}
        {!!researchRows(profile.items).length && (
          <Table
            rows={researchRows(profile.items)}
            pageSize={6}
            columns={[
              { key: "name", title: "构成项目" },
              { key: "category", title: "类型" },
              { key: "report_period", title: "报告期" },
              {
                key: "revenue_cny",
                title: "收入",
                render: (v) => (finite(v) ? amount(v) : "—"),
              },
              {
                key: "evidence_ids",
                title: "依据",
                render: (v) => <Refs ids={v} />,
              },
            ]}
          />
        )}
        <div className="research-evidence-heading">
          <h3>产业链关系证据</h3>
          <Badge>{researchState(chain.state)}</Badge>
        </div>
        {researchRows(chain.items).length ? (
          <div className="research-chain-list">
            {researchRows(chain.items).map((item, index) => (
              <article key={index}>
                <div>
                  <span>{listText(item.subject)}</span>
                  <span className="research-chain-arrow">→</span>
                  <strong>{listText(item.object)}</strong>
                  <Badge>{researchState(item.state)}</Badge>
                </div>
                <p>{listText(item.description)}</p>
                <small>{item.report_period || "披露期待核验"}</small>
                <Refs ids={item.evidence_ids} />
              </article>
            ))}
          </div>
        ) : (
          <Unknown>本次未取得可引用的上下游、客户或供应商关系。</Unknown>
        )}
        {chain.reason && (
          <p className="research-evidence-caption">{listText(chain.reason)}</p>
        )}
      </Block>
      <Block
        name="valuation"
        title="财务、估值与机构观点"
        subtitle="已披露财报、当前估值与机构预测分别列示"
        icon={<Wallet size={19} />}
      >
        <div className="research-metrics research-metrics--four">
          {[
            [
              "营业收入",
              finite(income.operating_income)
                ? amount(income.operating_income)
                : "—",
              income.report_period || "报告期待核验",
            ],
            [
              "归母净利润",
              finite(income.parent_holder_net_profit)
                ? amount(income.parent_holder_net_profit)
                : "—",
              income.report_period || "报告期待核验",
            ],
            [
              "市盈率 PE / TTM",
              valueText(report.valuation?.pe_ttm),
              "来源当前快照",
            ],
            ["市净率 PB", valueText(report.valuation?.pb_mrq), "负值保留原值"],
          ].map(([label, value, note]) => (
            <div className="research-metric" key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
              <small>{note}</small>
            </div>
          ))}
        </div>
        <div className="research-evidence-heading">
          <h3>机构预测、评级与目标价</h3>
          <Badge>{researchState(institutions.state)}</Badge>
        </div>
        {researchRows(institutions.items).length ? (
          <Table
            rows={researchRows(institutions.items)}
            columns={[
              { key: "institution", title: "机构" },
              { key: "report_date", title: "报告日" },
              { key: "forecast_period", title: "预测期" },
              { key: "rating", title: "评级" },
              { key: "target_price", title: "来源目标价", render: valueText },
              { key: "currency", title: "币种" },
              {
                key: "url",
                title: "依据",
                render: (v) => <OriginalLink url={v} />,
              },
            ]}
          />
        ) : (
          <Unknown>
            {listText(institutions.reason) ||
              "本次未取得有明确机构、报告日和预测期的研报样本，不生成目标价或一致预期。"}
          </Unknown>
        )}
        <div className="research-source-links">
          {researchRows(institutions.links).map((link, index) => (
            <OriginalLink key={index} url={link.url}>
              {link.title || "原文入口"}
            </OriginalLink>
          ))}
        </div>
        <details className="research-report-fold">
          <summary>完整财务、估值、股东及原始材料明细</summary>
          {legacyDetails}
        </details>
      </Block>
      <Block
        name="news"
        title="事件线索与风险"
        subtitle={
          listText(report.news?.scope) ||
          "实际取得的公告与已获许可材料，不代表完整全网新闻"
        }
        icon={<FileText size={19} />}
      >
        {news.length ? (
          <div className="research-event-timeline">
            {news.slice(0, 30).map((event, index) => (
              <article key={event.id || index}>
                <div className="research-event-date">
                  <time>
                    {event.published_date ||
                      dateTime(event.published_at_ms).split(" ")[0]}
                  </time>
                  <Badge>{listText(event.event_type) || "披露线索"}</Badge>
                </div>
                <div>
                  <h3>{listText(event.title)}</h3>
                  <div className="research-event-labels">
                    <Badge>标题线索</Badge>
                    <Badge>{listText(event.status) || "待阅读原文"}</Badge>
                    <Badge>
                      {listText(event.impact_label) || "影响方向待核验"}
                    </Badge>
                  </div>
                  <p>{listText(event.summary)}</p>
                  <Refs ids={event.evidence_ids} />
                  <OriginalLink url={event.url} />
                  {Array.isArray(event.questions) && (
                    <details>
                      <summary>需要核对什么</summary>
                      <ul>
                        {event.questions.map((question: unknown, i: number) => (
                          <li key={i}>{listText(question)}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Unknown>
            本次没有取得可列示事件材料；不能据此认定没有新闻或风险。
          </Unknown>
        )}
      </Block>
      <Block
        name="technical"
        title="日周月趋势与位置"
        subtitle="未复权 · 严格市场交易日 · 周月只用完整可比周期"
        icon={<Activity size={19} />}
      >
        {technical.summary && (
          <p className="research-business-summary">
            {typeof technical.summary === "string"
              ? technical.summary
              : listText(technical.summary.text)}
          </p>
        )}
        <div className="research-trends-grid">
          {periods.map((period) => (
            <article key={period.period} className="research-trend-card">
              <div className="research-trend-heading">
                <span>{period.label || period.period}</span>
                <Badge tone={period.state === "unknown" ? "warn" : "neutral"}>
                  {period.state_label || researchState(period.state)}
                </Badge>
              </div>
              <strong className="research-trend-price">
                {valueText(period.observations?.close)}{" "}
                <small>完整周期收盘</small>
              </strong>
              <p className="research-trend-date">
                确认数据截至 {period.observed_date || "待核验"}
              </p>
              <p className="research-evidence-caption">
                偏强条件逐项核对 · 非综合评分
              </p>
              <div className="research-trend-rule-list">
                {researchRows(period.rules).map((rule, i) => (
                  <div key={i}>
                    <span>{listText(rule.label)}</span>
                    <b>
                      {rule.met === true
                        ? "满足"
                        : rule.met === false
                          ? "未满足"
                          : "待核验"}
                    </b>
                  </div>
                ))}
              </div>
              <dl className="research-trend-key-values">
                {[
                  [
                    "MACD DIF / DEA",
                    valueText(period.observations?.macd_dif) +
                      " / " +
                      valueText(period.observations?.macd_dea),
                  ],
                  [
                    "K / D",
                    valueText(period.observations?.kdj_k) +
                      " / " +
                      valueText(period.observations?.kdj_d),
                  ],
                  ...(period.volume
                    ? [
                        [
                          "量 / 前20日均量",
                          finite(period.volume.ratio_vs_previous20)
                            ? num(period.volume.ratio_vs_previous20) + " 倍"
                            : "—",
                        ],
                      ]
                    : []),
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              {period.volume?.reason && (
                <p className="research-evidence-caption">
                  {listText(period.volume.reason)}
                </p>
              )}
              <div className="research-technical-levels">
                {[
                  ["support", "支撑参考"],
                  ["resistance", "压力参考"],
                ].map(([key, label]) => {
                  const level = period.levels?.[key];
                  return (
                    <div key={key}>
                      <span>{label}</span>
                      <strong>{valueText(level?.price)}</strong>
                      <small>
                        {finite(level?.distance_pct)
                          ? percent(level.distance_pct) + " 距离"
                          : "距离待核验"}
                      </small>
                      {level && (
                        <small>
                          形成 {level.formed_at} · 确认 {level.confirmed_at}
                        </small>
                      )}
                    </div>
                  );
                })}
              </div>
              <p className="research-evidence-caption">
                距离基准：{period.levels?.reference_date || "待核验"} 日收盘{" "}
                {valueText(period.levels?.reference_close)}。
              </p>
              {period.levels?.invalidation && (
                <details className="research-notes">
                  <summary>结构失效观察</summary>
                  <p>{listText(period.levels.invalidation.rule)}</p>
                  <p>{listText(period.levels.invalidation.scope)}</p>
                </details>
              )}
              <p className="research-evidence-caption">
                {listText(period.levels?.reason) ||
                  "技术位是已确认转折的参考距离，不代表盈利目标。"}
              </p>
              {period.current_period &&
                period.current_period.complete !== true && (
                  <div className="research-period-preview">
                    形成中 / 不完整周期：
                    {period.current_period.date || "日期待核验"}，收盘{" "}
                    {valueText(period.current_period.close)}；不纳入已确认强弱。
                  </div>
                )}
              {period.reason && <Unknown>{listText(period.reason)}</Unknown>}
              {Array.isArray(period.warnings) &&
                period.warnings
                  .filter((warning: unknown) => warning !== period.reason)
                  .map((warning: unknown, i: number) => (
                    <p key={i} className="research-evidence-caption">
                      {listText(warning)}
                    </p>
                  ))}
            </article>
          ))}
        </div>
        {!periods.length && (
          <Unknown>本报告未留存完整趋势评估，不从当前行情回填。</Unknown>
        )}
        <div className="research-evidence-heading">
          <h3>
            相对 {technical.relative_strength?.benchmark?.name || "上证指数"}
          </h3>
          <span>个股收益 − 基准收益，单位：百分点</span>
        </div>
        <Table
          rows={researchRows(technical.relative_strength?.windows)}
          columns={[
            {
              key: "market_sessions",
              title: "市场交易日窗口",
              render: (v) => (finite(v) ? v + "日" : "—"),
            },
            { key: "start_date", title: "起点" },
            { key: "end_date", title: "终点" },
            {
              key: "stock_return_pct",
              title: "个股收益",
              render: (value) => percent(value),
            },
            {
              key: "benchmark_return_pct",
              title: "基准收益",
              render: (value) => percent(value),
            },
            {
              key: "excess_return_pp",
              title: "超额百分点",
              render: (v) => (finite(v) ? num(v) + " pp" : "—"),
            },
            {
              key: "reason",
              title: "范围与缺口",
              render: (v, row) => v || researchState(row.state),
            },
          ]}
          emptyTitle="相对指数数据尚未取得"
        />
        {technical.relative_strength?.industry?.reason && (
          <p className="research-evidence-caption">
            行业对比：{listText(technical.relative_strength.industry.reason)}
          </p>
        )}
        <div className="research-space-grid">
          {[
            ["technical", "技术参考距离"],
            ["institutional_target", "机构目标空间"],
            ["valuation_scenarios", "估值情景"],
          ].map(([key, label]) => {
            const item = technical.space_assessment?.[key];
            return (
              <div key={key}>
                <h4>{label}</h4>
                <Badge>{researchState(item?.state)}</Badge>
                <p>
                  {listText(item?.reason) ||
                    listText(item?.notice) ||
                    "本次未取得可比较的依据"}
                </p>
              </div>
            );
          })}
        </div>
        <div className="research-frozen-chart">
          <div className="research-evidence-heading">
            <h3>报告冻结行情</h3>
            <span>仅切换本报告已留存周期；不读取当前行情</span>
          </div>
          <CandleChart
            instrumentId={instrument.instrument_id || ""}
            instrumentKind="stock"
            asOf={report.data_cutoff}
            height={650}
            showStockSummary={false}
            frozenChartsByPeriod={technical.frozen_charts_by_period || {}}
          />
        </div>
      </Block>
      <Block
        name="evidence"
        title="证据与待核查清单"
        subtitle="材料首次发布时间、报告期和采集时间分别核对"
        icon={<Building2 size={19} />}
      >
        <div className="research-coverage-grid">
          {coverage.map((source, index) => (
            <article key={source.source_id || index}>
              <div>
                <strong>{source.title || source.source_id}</strong>
                <Badge>{researchState(source.state)}</Badge>
              </div>
              <p>
                {source.reason ||
                  (finite(source.accepted_count)
                    ? "本次取得 " + source.accepted_count + " 份材料"
                    : "覆盖待核验")}
              </p>
            </article>
          ))}
        </div>
        <div className="research-evidence-heading">
          <h3>下一步核查</h3>
          <span>保留尚未解决的问题</span>
        </div>
        <ol className="research-question-list">
          {researchRows(report.unresolved_questions).map((question, index) => (
            <li key={index}>
              <strong>{listText(question.question)}</strong>
              <p>{listText(question.reason)}</p>
              <Refs ids={question.evidence_ids} />
            </li>
          ))}
        </ol>
        <div className="research-evidence-heading">
          <h3>证据原文与留存字段</h3>
          <span>{evidence.length} 份 · 非独立转载不重复计数</span>
        </div>
        <div className="research-evidence-list">
          {evidence.map((item, index) => (
            <article
              key={item.evidence_id || index}
              id={"research-evidence-" + index}
            >
              <div className="research-evidence-heading">
                <h4>
                  <span>#{index + 1}</span> {item.title || "材料标题待核验"}
                </h4>
                <Badge>
                  {item.official_document === true ? "官方披露" : "来源材料"}
                </Badge>
              </div>
              <dl>
                <div>
                  <dt>来源 / 层级</dt>
                  <dd>
                    {item.source || "待核验"} / {item.tier || "待核验"}
                  </dd>
                </div>
                <div>
                  <dt>发布时间</dt>
                  <dd>{dateTime(item.published_at_ms)}</dd>
                </div>
                <div>
                  <dt>报告期</dt>
                  <dd>{item.report_period || "来源未提供"}</dd>
                </div>
                <div>
                  <dt>采集时间</dt>
                  <dd>{dateTime(item.fetched_at_ms)}</dd>
                </div>
              </dl>
              <p className="research-evidence-caption">
                {listText(item.identity_basis)}
              </p>
              <div className="research-source-links">
                <OriginalLink url={item.url} />
                <button
                  className="link"
                  onClick={() =>
                    onMaterial({ ...item, data: item.data || item })
                  }
                >
                  查看留存字段
                </button>
              </div>
            </article>
          ))}
        </div>
        {!evidence.length && (
          <Unknown>本次没有取得可引用材料，所有结论保持待核验。</Unknown>
        )}
        <div className="research-source-links">
          {researchRows(report.external_links).map((link, index) => (
            <span key={index}>
              <OriginalLink url={link.url}>
                {link.title || "外部入口"}
              </OriginalLink>
              <small>
                {link.state === "external_only"
                  ? "仅外链"
                  : researchState(link.state)}
              </small>
            </span>
          ))}
        </div>
      </Block>
    </div>
  );
}
