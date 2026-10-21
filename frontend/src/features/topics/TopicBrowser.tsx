import { useEffect, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Link2,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { amount, num, percent, tone } from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel,
  Table,
  useToast,
  Warnings,
} from "../../components/UI";
import AssociationDialog from "./AssociationDialog";
import { topicKindLabel } from "./TopicDirectory";
import {
  HistoryReturnCell,
  MemberMetricCoverage,
  RecentLimitCell,
  YearLimitCountCell,
} from "./MemberHistoryCells";

function MemberTags({ names = [] }: { names?: string[] }) {
  return (
    <div className="topic-member-labels" title={names.join("、")}>
      <div className="tags">
        {names.slice(0, 3).map((name) => (
          <Badge key={name}>{name}</Badge>
        ))}
      </div>
      {names.length > 3 && (
        <details>
          <summary>展开其余 {names.length - 3} 项</summary>
          <div className="tags">
            {names.slice(3).map((name) => (
              <Badge key={name}>{name}</Badge>
            ))}
          </div>
        </details>
      )}
      {!names.length && <span className="muted">—</span>}
    </div>
  );
}

export default function TopicBrowser({
  topicIds,
  catalog,
  singleTopic,
  refreshVersion,
  businessDate,
  busy,
  onRemoveTopic,
  onClearTopics,
  onMembership,
  onStock,
  onChanged,
}: {
  topicIds: string[];
  catalog: Row[];
  singleTopic?: Row | null;
  refreshVersion: number;
  businessDate?: string;
  busy: string;
  onRemoveTopic: (id: string) => void;
  onClearTopics: () => void;
  onMembership: (
    topicId: string,
    instrumentId: string,
    action: string,
  ) => Promise<void>;
  onStock: (id: string) => void;
  onChanged: () => void;
}) {
  const [match, setMatch] = useState("any"),
    [query, setQuery] = useState(""),
    [debounced, setDebounced] = useState(""),
    [sortBy, setSortBy] = useState("change_pct"),
    [sortOrder, setSortOrder] = useState("desc"),
    [selectedStocks, setSelectedStocks] = useState<Map<string, Row>>(new Map()),
    [associate, setAssociate] = useState(false);
  const toast = useToast();
  const selectedKey = [...topicIds].sort().join("|");
  useEffect(() => {
    setSelectedStocks(new Map());
    setQuery("");
    setDebounced("");
  }, [selectedKey]);
  useEffect(() => setSelectedStocks(new Map()), [refreshVersion]);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const params = new URLSearchParams({
    match,
    query: debounced,
    sort_by: sortBy,
    sort_order: sortOrder,
  });
  [...topicIds].sort().forEach((id) => params.append("topic_ids", id));
  const result = useApi<Row>(
    topicIds.length ? "/topic-members?" + params : null,
    [refreshVersion],
  );
  const data = result.data;
  const rows = list(data);
  const selectedTopics = topicIds.map(
    (id) =>
      catalog.find((topic) => topic.topic_id === id) ||
      data?.selected_topics?.find((topic: Row) => topic.topic_id === id) || {
        topic_id: id,
        name: id,
      },
  );
  const single = topicIds.length === 1 ? singleTopic : null;
  function sort(field: string) {
    setSortOrder(sortBy === field && sortOrder === "desc" ? "asc" : "desc");
    setSortBy(field);
  }
  function sortTitle(field: string, name: string, explanation?: string) {
    const active = sortBy === field;
    const Icon = active
      ? sortOrder === "desc"
        ? ArrowDown
        : ArrowUp
      : ArrowUpDown;
    return (
      <button
        className={"topic-sort-heading " + (active ? "active" : "")}
        onClick={() => sort(field)}
        aria-label={`按${name}${active && sortOrder === "desc" ? "升序" : "降序"}排列`}
        title={
          (explanation ? explanation + "；" : "") +
          "对全部匹配股票排序，再分页，未知值始终排末"
        }
      >
        {name}
        <Icon size={12} />
      </button>
    );
  }
  function toggleStock(id: string) {
    const next = new Map(selectedStocks);
    if (next.has(id)) next.delete(id);
    else {
      if (next.size >= 100) {
        toast("一次最多选择100只股票，请先清空部分选择", "error");
        return;
      }
      const row = rows.find((item) => item.instrument_id === id);
      if (row) next.set(id, row);
    }
    setSelectedStocks(next);
  }
  const quote = (
    row: Row,
    value: unknown,
    formatter: (value: any) => string,
  ) => (
    <span
      title={
        row.quote_available === false
          ? "该业务日没有可核验行情，保持未知"
          : "行情日 " + (row.quote_date || data?.quote_date || "待核验")
      }
    >
      {row.quote_available === false ? "—" : formatter(value)}
    </span>
  );
  return (
    <>
      <Panel
        title="题材与行业联合查股"
        subtitle={`独立业务行情日 ${data?.business_date || businessDate || "待初始化"}${data?.quote_date && data.quote_date !== data.business_date ? " · 报价日 " + data.quote_date : ""} · 不受盘面观察日期影响`}
        actions={
          <button
            className="icon-btn"
            title="刷新成员行情"
            aria-label="刷新题材成员行情"
            onClick={result.reload}
            disabled={!topicIds.length}
          >
            <RefreshCw size={15} />
          </button>
        }
      >
        <div className="topic-selected-toolbar">
          <div className="topic-selected-chips" aria-label="联合查询已选分类">
            {!selectedTopics.length ? (
              <span className="muted">从左侧选择题材或行业，最多20项。</span>
            ) : (
              selectedTopics.map((topic) => (
                <button
                  key={topic.topic_id}
                  className="topic-selection-chip"
                  onClick={() => onRemoveTopic(topic.topic_id)}
                  title={"移除" + topic.name}
                >
                  {topic.name}
                  <small>
                    {topicKindLabel(topic)}
                    {topic.archived ? " · 已归档" : ""}
                  </small>
                  <X size={12} />
                </button>
              ))
            )}
          </div>
          {!!selectedTopics.length && (
            <button className="btn small" onClick={onClearTopics}>
              清空分类
            </button>
          )}
        </div>
        {topicIds.length > 1 && (
          <div className="topic-union-controls">
            <div className="segmented">
              <button
                className={match === "any" ? "active" : ""}
                onClick={() => setMatch("any")}
              >
                任一分类（并集）
              </button>
              <button
                className={match === "all" ? "active" : ""}
                onClick={() => setMatch("all")}
              >
                同时满足（交集）
              </button>
            </div>
            <span>多选仅联合浏览；单选一个分类可维护其成员。</span>
          </div>
        )}
        {!!topicIds.length && (
          <div className="topic-member-toolbar">
            <input
              type="search"
              aria-label="搜索联合成员股票"
              placeholder="搜索结果中的股票名称或代码"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Badge>{data?.total ?? "—"} 只匹配</Badge>
            <span className="muted">
              跨页 / 筛选保留已选 {selectedStocks.size} / 100 只
            </span>
            <button
              className="btn small"
              disabled={!rows.length || result.loading}
              onClick={() =>
                setSelectedStocks(
                  new Map(
                    rows.slice(0, 100).map((row) => [row.instrument_id, row]),
                  ),
                )
              }
            >
              改选排序前{Math.min(rows.length, 100)}只
            </button>
            {!!selectedStocks.size && (
              <button
                className="btn small"
                onClick={() => setSelectedStocks(new Map())}
              >
                清空股票选择
              </button>
            )}
            <button
              className="btn primary small"
              disabled={!selectedStocks.size}
              onClick={() => setAssociate(true)}
            >
              <Link2 size={13} />
              关联多个题材 / 行业
            </button>
          </div>
        )}
        {data && data.members_complete === false && (
          <div className="topic-members-incomplete">
            <Badge tone="warn">成员范围未完整</Badge>
            仅展示已知成员；没有结果不能解释为这些分类确无交集。
          </div>
        )}
        {!result.loading && !!topicIds.length && (
          <MemberMetricCoverage coverage={data?.metric_coverage} />
        )}
        <Warnings items={data?.warnings} />
        {!topicIds.length ? (
          <Empty
            title="选择一个或多个分类"
            description="点击名称单选；勾选可跨搜索、跨类型组合多个题材与行业，选择并集或交集查股。"
          />
        ) : result.error ? (
          <ErrorBox message={result.error} retry={result.reload} />
        ) : result.loading ? (
          <Loading />
        ) : (
          <Table
            key={selectedKey + match + debounced + sortBy + sortOrder}
            rows={rows}
            pageSize={25}
            rowKey="instrument_id"
            selection={new Set(selectedStocks.keys())}
            onSelect={toggleStock}
            onStock={onStock}
            columns={[
              {
                key: "name",
                title: (
                  <div className="topic-name-sort">
                    {sortTitle("name", "股票")}
                    {sortTitle("code", "代码")}
                  </div>
                ),
              },
              {
                key: "change_pct",
                title: sortTitle("change_pct", "最新涨幅"),
                align: "right",
                render: (value, row) => (
                  <span
                    className={
                      row.quote_available === false ? "muted" : tone(value)
                    }
                  >
                    {quote(row, value, percent)}
                  </span>
                ),
              },
              {
                key: "return_ytd_pct",
                title: sortTitle(
                  "return_ytd_pct",
                  "年内涨幅",
                  "按服务端核验的年内基准与业务日收盘端点计算，公司行动口径不足时保持未知",
                ),
                align: "right",
                render: (value, row) => (
                  <HistoryReturnCell
                    value={value}
                    reason={row.return_ytd_reason}
                    start={row.return_ytd_coverage?.start}
                    end={row.return_ytd_coverage?.end}
                    priceBasis={row.return_ytd_coverage?.price_basis}
                    missingSessions={row.return_ytd_coverage?.missing_sessions}
                  />
                ),
              },
              {
                key: "change_30d_pct",
                title: sortTitle(
                  "change_30d_pct",
                  "近30交易日涨幅",
                  "精确T与T-30市场交易日端点，区间缺报价另列提示，不使用最近31条报价替代",
                ),
                align: "right",
                render: (value, row) => (
                  <HistoryReturnCell
                    value={value}
                    reason={row.change_30d_reason}
                    start={row.change_30d_coverage?.start}
                    end={row.change_30d_coverage?.end}
                    priceBasis={row.change_30d_coverage?.price_basis}
                    missingSessions={row.change_30d_coverage?.missing_sessions}
                  />
                ),
              },
              {
                key: "limit_up_ytd",
                title: sortTitle(
                  "limit_up_ytd",
                  "年内涨停",
                  "只比较精确已核验次数；覆盖不足时显示已确认下界，不与精确值混排",
                ),
                align: "right",
                render: (_, row) => <YearLimitCountCell row={row} />,
              },
              {
                key: "last_limit_up_date",
                title: (
                  <div className="topic-recent-sort">
                    {sortTitle(
                      "last_limit_up_date",
                      "最近涨停",
                      "来源历史有缺口时只显示最近已确认事件，不推断不存在更近事件",
                    )}
                    {sortTitle(
                      "sessions_since_limit_up",
                      "距今",
                      "按当前业务日交易日历计算，非自然日",
                    )}
                  </div>
                ),
                render: (_, row) => <RecentLimitCell row={row} />,
              },
              {
                key: "close",
                title: sortTitle("close", "收盘"),
                align: "right",
                render: (value, row) => quote(row, value, num),
              },
              {
                key: "amount",
                title: sortTitle("amount", "成交额"),
                align: "right",
                render: (value, row) => quote(row, value, amount),
              },
              {
                key: "turnover_rate",
                title: sortTitle("turnover_rate", "换手率"),
                align: "right",
                render: (value, row) => quote(row, value, percent),
              },
              {
                key: "topics",
                title: "题材",
                render: (value) => <MemberTags names={value || []} />,
              },
              {
                key: "industries",
                title: "行业",
                render: (value) => <MemberTags names={value || []} />,
              },
              ...(single
                ? [
                    {
                      key: "origin",
                      title: "归属依据",
                      render: (value: any, row: Row) => (
                        <Badge>
                          {value === "manual"
                            ? row.provider_member
                              ? "人工保留"
                              : "人工添加"
                            : value === "provider"
                              ? "来源成员"
                              : "来源未知"}
                        </Badge>
                      ),
                    },
                    {
                      key: "actions",
                      title: "成员操作",
                      render: (_: any, row: Row) => (
                        <div className="button-group">
                          {single.origin === "provider" &&
                            row.manual_override && (
                              <button
                                className="btn small"
                                disabled={!!busy || !!single.archived}
                                title="撤销人工修改，重新采用数据源归属"
                                onClick={() =>
                                  onMembership(
                                    single.topic_id,
                                    row.instrument_id,
                                    "reset_to_provider",
                                  )
                                }
                              >
                                撤销人工修改
                              </button>
                            )}
                          <button
                            className="icon-btn danger-text"
                            disabled={!!busy || !!single.archived}
                            title={
                              single.origin === "provider"
                                ? "人工排除：后续同步不自动加回"
                                : "移除此成员"
                            }
                            aria-label={"移除成员" + (row.name || row.code)}
                            onClick={() =>
                              onMembership(
                                single.topic_id,
                                row.instrument_id,
                                "exclude",
                              )
                            }
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ),
                    },
                  ]
                : []),
            ]}
            emptyTitle={
              debounced
                ? "没有匹配股票"
                : match === "all" && topicIds.length > 1
                  ? "当前已知成员没有共同股票"
                  : "当前没有已留存成员"
            }
            emptyDescription={
              data?.members_complete === false
                ? "成员采集仍有缺口，不能将空结果当作完整排除依据。"
                : "可调整分类组合或搜索条件，也可单选分类添加成员。"
            }
          />
        )}
        {!!topicIds.length && (
          <div className="panel-footnote">
            表头可切换升降序；先对全部匹配结果排序，再按每页25只展示，未知值排末。历史涨幅与涨停信息描述当前成员股票本身，不代表这些股票在历史事件发生时已属于当前题材或行业。最近事件有缺口时标为“最近已确认”，距今按交易日计算；勾选只用于批量追加归属。
          </div>
        )}
      </Panel>
      {associate && (
        <AssociationDialog
          instruments={[...selectedStocks.values()]}
          topics={catalog}
          onClose={() => setAssociate(false)}
          onSaved={() => {
            setSelectedStocks(new Map());
            result.reload();
            onChanged();
          }}
        />
      )}
    </>
  );
}
