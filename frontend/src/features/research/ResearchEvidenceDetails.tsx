import { useEffect, useRef, useState, type ReactNode } from "react";
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
  ResearchFinancialColorGuide,
  ResearchFinancialValue,
} from "./ResearchFinancialValue";
import {
  researchClaim,
  groupResearchQuestions,
  researchEvidenceKind,
  researchEvidenceKinds,
  researchEvidencePage,
  researchEvidenceRefs,
  researchRows,
  researchState,
  researchUrl,
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
  active,
  children,
}: {
  name: string;
  title: string;
  subtitle: string;
  icon: ReactNode;
  active: string;
  children: ReactNode;
}) {
  return (
    <section
      id={"research-v2-" + name}
      className="research-section-anchor"
      hidden={active !== name}
      role="tabpanel"
      aria-labelledby={"research-tab-" + name}
      tabIndex={-1}
    >
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
  const [active, setActive] = useState("overview");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [dateState, setDateState] = useState("all");
  const [page, setPage] = useState(0);
  const [newsExpanded, setNewsExpanded] = useState(false);
  const [pendingEvidence, setPendingEvidence] = useState<number | null>(null);
  const readerRef = useRef<HTMLDivElement>(null);
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
  const questions = groupResearchQuestions(report.unresolved_questions);
  const evidencePage = researchEvidencePage(evidence, {
    query,
    kind,
    dateState,
    page,
  });
  useEffect(() => {
    if (pendingEvidence === null) return;
    const element = document.getElementById(
      "research-evidence-" + pendingEvidence,
    );
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
    element?.focus({ preventScroll: true });
    setPendingEvidence(null);
  }, [pendingEvidence, active, evidencePage.page]);
  function selectSection(name: string) {
    setActive(name);
    if (readerRef.current && readerRef.current.getBoundingClientRect().top < 72)
      readerRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function openEvidence(index: number) {
    setActive("evidence");
    setQuery("");
    setKind("all");
    setDateState("all");
    setPage(Math.floor(index / 8));
    setPendingEvidence(index);
  }
  function Refs({ ids }: { ids: unknown }) {
    const refs = researchEvidenceRefs(ids, evidence);
    return refs.length ? (
      <span className="research-evidence-refs">
        {refs.map((ref) => (
          <a
            key={ref.id}
            href={"#research-evidence-" + ref.index}
            onClick={(event) => {
              event.preventDefault();
              openEvidence(ref.index);
            }}
            title={evidence[ref.index].title}
          >
            证据 {ref.index + 1}
          </a>
        ))}
      </span>
    ) : null;
  }
  return (
    <div className="research-reader research-evidence-report" ref={readerRef}>
      <nav className="research-reader-nav" aria-label="个股调研章节">
        <span className="research-reader-nav-title">报告目录</span>
        <div role="tablist" aria-label="调研内容">
          {sections.map(([id, label], index) => (
            <button
              type="button"
              key={id}
              role="tab"
              id={"research-tab-" + id}
              aria-controls={"research-v2-" + id}
              aria-selected={active === id}
              tabIndex={active === id ? 0 : -1}
              onClick={() => selectSection(id)}
              onKeyDown={(event) => {
                const next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? sections.length - 1
                      : ["ArrowRight", "ArrowDown"].includes(event.key)
                        ? (index + 1) % sections.length
                        : ["ArrowLeft", "ArrowUp"].includes(event.key)
                          ? (index + sections.length - 1) % sections.length
                          : null;
                if (next === null) return;
                event.preventDefault();
                selectSection(sections[next][0]);
                document
                  .getElementById("research-tab-" + sections[next][0])
                  ?.focus();
              }}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              {label}
              {id === "evidence" && <small>{evidence.length}</small>}
            </button>
          ))}
        </div>
        <p>先看总览，按需查阅材料</p>
      </nav>
      <div className="research-reader-main">
        <Block
          active={active}
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
              <span>条留存证据</span>
            </div>
          </div>
          <div className="research-evidence-meta">
            <span>行情截止 {report.data_cutoff || "待核验"}</span>
            <span>材料查询 {dateTime(report.research_as_of_ms)}</span>
            <span>缓存到期 {dateTime(report.cache?.expires_at_ms)}</span>
          </div>
          <div className="research-overview-grid">
            {[
              [
                "company",
                "公司业务",
                listText(profile.main_business) ||
                  "主营构成本次未取得，待原始材料核验",
              ],
              [
                "valuation",
                "财务快览",
                finite(income.operating_income)
                  ? `营业收入 ${amount(income.operating_income)} · ${income.report_period || "报告期待核验"}`
                  : "营业收入与报告期本次未取得",
              ],
              [
                "news",
                "近期事件",
                news[0]?.title || "本次未取得事件材料，不能据此认定没有风险",
              ],
              [
                "technical",
                "日周月观察",
                periods.length
                  ? periods
                      .map(
                        (period) =>
                          `${period.label || period.period}：${period.state_label || researchState(period.state)}`,
                      )
                      .join(" · ")
                  : "报告未留存趋势评估",
              ],
            ].map(([id, title, description]) => (
              <button type="button" key={id} onClick={() => selectSection(id)}>
                <span>
                  {title} <ArrowUpRight size={13} />
                </span>
                <p>{description}</p>
              </button>
            ))}
          </div>
          <div className="research-evidence-highlights">
            {researchRows(overview.highlights)
              .slice(0, 3)
              .map((item, index) => (
                <p key={index}>
                  {listText(item.text)} <Refs ids={item.evidence_ids} />
                </p>
              ))}
          </div>
          {!!claims.length && (
            <details className="research-report-fold">
              <summary>摘要依据与其他线索 · {claims.length} 条</summary>
              <div className="research-claim-list">
                {claims.map((claim, index) => (
                  <div key={claim.claim_id || index}>
                    <Badge>{researchClaim(claim.kind)}</Badge>
                    <p>
                      {listText(claim.text)} <Refs ids={claim.evidence_ids} />
                    </p>
                  </div>
                ))}
              </div>
            </details>
          )}
          <p className="research-evidence-caption">
            {listText(overview.notice) ||
              "当前手动调研，材料日期与行情截止分别核对。"}{" "}
            七日访问缓存不会刷新材料，需要最新信息时手动重新运行。
          </p>
        </Block>
        <Block
          active={active}
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
            <p className="research-evidence-caption">
              {listText(chain.reason)}
            </p>
          )}
        </Block>
        <Block
          active={active}
          name="valuation"
          title="财务、估值与机构观点"
          subtitle="已披露财报、当前估值与机构预测分别列示"
          icon={<Wallet size={19} />}
        >
          <div className="research-metrics research-metrics--four">
            {[
              {
                label: "营业收入",
                value: (
                  <ResearchFinancialValue
                    rows={researchRows(statements.income)}
                    row={income}
                    field="operating_income"
                  />
                ),
                note: income.report_period || "报告期待核验",
              },
              {
                label: "归母净利润",
                value: (
                  <ResearchFinancialValue
                    rows={researchRows(statements.income)}
                    row={income}
                    field="parent_holder_net_profit"
                  />
                ),
                note: income.report_period || "报告期待核验",
              },
              {
                label: "市盈率 PE / TTM",
                value: valueText(report.valuation?.pe_ttm),
                note: "来源当前快照",
              },
              {
                label: "市净率 PB",
                value: valueText(report.valuation?.pb_mrq),
                note: "负值保留原值",
              },
            ].map(({ label, value, note }) => (
              <div className="research-metric" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
                <small>{note}</small>
              </div>
            ))}
          </div>
          <ResearchFinancialColorGuide />
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
          active={active}
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
              {(newsExpanded ? news : news.slice(0, 5)).map((event, index) => (
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
                          {event.questions.map(
                            (question: unknown, i: number) => (
                              <li key={i}>{listText(question)}</li>
                            ),
                          )}
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
          {news.length > 5 && (
            <button
              className="btn small research-show-more"
              type="button"
              onClick={() => setNewsExpanded(!newsExpanded)}
            >
              {newsExpanded ? "收起事件" : `查看其余 ${news.length - 5} 条事件`}
            </button>
          )}
        </Block>
        <Block
          active={active}
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
                      {valueText(period.current_period.close)}
                      ；不纳入已确认强弱。
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
            {active === "technical" && (
              <CandleChart
                instrumentId={instrument.instrument_id || ""}
                instrumentKind="stock"
                asOf={report.data_cutoff}
                height={650}
                showStockSummary={false}
                frozenChartsByPeriod={technical.frozen_charts_by_period || {}}
              />
            )}
          </div>
        </Block>
        <Block
          active={active}
          name="evidence"
          title="证据与待核查清单"
          subtitle="材料首次发布时间、报告期和采集时间分别核对"
          icon={<Building2 size={19} />}
        >
          <p className="research-evidence-caption">
            {evidence.length} 条留存证据 · {questions.length}{" "}
            项待核查问题。按类型或关键词找到依据，完整留存字段按需打开。
          </p>
          <details className="research-report-fold">
            <summary>
              来源覆盖 · {coverage.length} 类来源，查看取得范围与真实缺口
            </summary>
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
          </details>
          <details className="research-report-fold">
            <summary>待核查问题 · {questions.length} 项</summary>
            <ol className="research-question-list">
              {questions.map((question, index) => (
                <li key={index}>
                  <strong>{listText(question.question)}</strong>
                  {question.count > 1 && (
                    <small> · 合并 {question.count} 条相同问题</small>
                  )}
                  <p>{listText(question.reason)}</p>
                  <Refs ids={question.evidence_ids} />
                </li>
              ))}
            </ol>
          </details>
          <div className="research-evidence-heading">
            <h3>证据原文与留存字段</h3>
            <span>
              {evidencePage.total} / {evidence.length} 条 ·
              条目数不等于独立文档数
            </span>
          </div>
          <div className="research-evidence-filters">
            <label>
              <span>搜索证据</span>
              <input
                type="search"
                value={query}
                placeholder="标题、来源、报告期"
                onChange={(event) => {
                  setQuery(event.target.value);
                  setPage(0);
                }}
              />
            </label>
            <label>
              <span>材料类型</span>
              <select
                value={kind}
                onChange={(event) => {
                  setKind(event.target.value);
                  setPage(0);
                }}
              >
                {researchEvidenceKinds.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>发布时间</span>
              <select
                value={dateState}
                onChange={(event) => {
                  setDateState(event.target.value);
                  setPage(0);
                }}
              >
                <option value="all">全部日期状态</option>
                <option value="dated">已提供发布时间</option>
                <option value="undated">发布时间待核验</option>
              </select>
            </label>
            {(query || kind !== "all" || dateState !== "all") && (
              <button
                className="link"
                type="button"
                onClick={() => {
                  setQuery("");
                  setKind("all");
                  setDateState("all");
                  setPage(0);
                }}
              >
                清除筛选
              </button>
            )}
          </div>
          <div className="research-evidence-list">
            {evidencePage.rows.map(({ item, index }) => (
              <article
                key={item.evidence_id || index}
                id={"research-evidence-" + index}
                tabIndex={-1}
              >
                <div className="research-evidence-heading">
                  <h4>
                    <span>#{index + 1}</span> {item.title || "材料标题待核验"}
                  </h4>
                  <Badge>
                    {researchEvidenceKinds.find(
                      ([key]) => key === researchEvidenceKind(item.kind),
                    )?.[1] || "其他材料"}
                  </Badge>
                </div>
                <div className="research-evidence-row-meta">
                  <span>{item.source || "来源待核验"}</span>
                  <span>
                    {item.report_period
                      ? `报告期 ${item.report_period}`
                      : "报告期未提供"}
                  </span>
                  <span>
                    {finite(item.published_at_ms) && item.published_at_ms > 0
                      ? `发布 ${dateTime(item.published_at_ms)}`
                      : "发布时间待核验"}
                  </span>
                </div>
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
                <details className="research-evidence-metadata">
                  <summary>查看来源层级、采集日期与核验口径</summary>
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
                  <Badge>
                    {item.official_document === true ? "官方披露" : "来源字段"}
                  </Badge>
                </details>
              </article>
            ))}
          </div>
          {!!evidence.length && !evidencePage.total && (
            <Unknown>没有符合筛选的留存证据。可清除筛选查看全部材料。</Unknown>
          )}
          {evidencePage.pages > 1 && (
            <div className="research-evidence-pagination">
              <span>
                每页 8 条 · 第 {evidencePage.page + 1} / {evidencePage.pages} 页
              </span>
              <button
                className="btn small"
                type="button"
                disabled={evidencePage.page === 0}
                onClick={() => setPage(evidencePage.page - 1)}
              >
                上一页
              </button>
              <button
                className="btn small"
                type="button"
                disabled={evidencePage.page + 1 >= evidencePage.pages}
                onClick={() => setPage(evidencePage.page + 1)}
              >
                下一页
              </button>
            </div>
          )}
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
    </div>
  );
}
