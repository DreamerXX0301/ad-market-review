import { useState } from "react";
import { ArrowRight, RefreshCw } from "lucide-react";
import { Empty, ErrorBox, Loading } from "../../components/UI";
import type { Row } from "../../lib/api";
import { buildDailyMainlineDisplay } from "./dailyMainlinePresentation";
import type {
  MainlineDirection,
  MainlineSourceItem,
  SystemMainlines,
} from "./dailyMainlinePresentation";
import BoardTags from "./BoardTags";
import "./DailyMainlines.css";

function OriginalReason({ text }: { text: string }) {
  if (!text.trim())
    return <p className="daily-mainline-reason muted">来源暂未提供明确原因</p>;
  return (
    <div className="daily-mainline-reason">
      <p>{text.length > 80 ? `${text.slice(0, 80)}…` : text}</p>
      {text.length > 80 && (
        <details>
          <summary>查看原始原因</summary>
          <p>{text}</p>
        </details>
      )}
    </div>
  );
}
function SourceItem({
  item,
  date,
  snapshotId,
  onStock,
  badges = false,
}: {
  item: MainlineSourceItem;
  date: string;
  snapshotId: unknown;
  onStock: (id: string) => void;
  badges?: boolean;
}) {
  return (
    <li className="daily-mainline-source-item">
      <div className="daily-mainline-source-heading">
        <button
          className="daily-mainline-stock-name"
          onClick={() => onStock(item.stock.instrument_id)}
        >
          {item.stock.name || item.stock.instrument_id}
        </button>
        {item.height != null && (
          <span className="daily-mainline-known-height">{item.height}板</span>
        )}
      </div>
      {badges && (
        <BoardTags
          row={item.stock}
          date={date}
          snapshotId={snapshotId}
          height={false}
          volume={false}
        />
      )}
      <OriginalReason text={item.sourceReason} />
    </li>
  );
}
const matches = (item: MainlineSourceItem, query: string) =>
  [
    item.stock.name,
    item.stock.code,
    item.stock.instrument_id,
    item.sourceReason,
  ]
    .join(" ")
    .toLocaleLowerCase()
    .includes(query);
function visibleItems(group: MainlineDirection, query: string) {
  return !query || group.name.toLocaleLowerCase().includes(query)
    ? group.items
    : group.items.filter((item) => matches(item, query));
}

export default function DailyMainlines({
  date,
  rows,
  query = "",
  snapshotId,
  systemFocus,
  loading = false,
  error = "",
  onReload,
  onStock,
  onBrowseView,
}: {
  date: string;
  rows: Row[];
  query?: string;
  snapshotId?: unknown;
  systemFocus?: SystemMainlines;
  loading?: boolean;
  error?: string;
  onReload?: () => void;
  onStock: (id: string) => void;
  onBrowseView?: (view: "height" | "theme") => void;
}) {
  const plan = buildDailyMainlineDisplay(date, rows, systemFocus, snapshotId);
  const search = query.trim().toLocaleLowerCase();
  const focus = plan.groups
    .map((group) => ({ ...group, visible: visibleItems(group, search) }))
    .filter((group) => group.visible.length);
  const [selectedKey, setSelectedKey] = useState("");
  const selected =
    focus.find((group) => `${date}:${group.key}` === selectedKey) || focus[0];
  const others = plan.otherGroups
    .map((group) => ({ ...group, visible: visibleItems(group, search) }))
    .filter((group) => group.visible.length);
  const individuals = search
    ? plan.individualCues.filter((item) => matches(item, search))
    : plan.individualCues;
  const item = (value: MainlineSourceItem, badges = false) => (
    <SourceItem
      key={value.stock.instrument_id}
      item={value}
      date={date}
      snapshotId={snapshotId}
      onStock={onStock}
      badges={badges}
    />
  );
  return (
    <section className="daily-mainlines">
      <header className="daily-mainline-heading">
        <div>
          <h2>当日主线</h2>
          <p>{date} · 根据当日已发布来源自动归类</p>
        </div>
        {onReload && (
          <button className="btn small" onClick={onReload}>
            <RefreshCw size={13} />
            刷新
          </button>
        )}
      </header>
      <div className="daily-mainline-scope">
        <p>
          主线看重点方向与来源线索；完整高度结构看连板梯队，题材成员看概念关联。
        </p>
        {onBrowseView && (
          <div className="daily-mainline-navigation">
            <button
              className="btn small"
              onClick={() => onBrowseView("height")}
            >
              连板梯队
              <ArrowRight size={13} />
            </button>
            <button className="btn small" onClick={() => onBrowseView("theme")}>
              概念关联
              <ArrowRight size={13} />
            </button>
          </div>
        )}
      </div>
      {error && <ErrorBox message={error} retry={onReload} />}
      {loading ? (
        <Loading />
      ) : (
        <>
          <section
            className="daily-mainline-global-high"
            aria-label="全部确认样本的已核验最高板高"
          >
            <header>
              <h3>高标观察</h3>
              <span>
                {plan.globalHigh.height == null
                  ? "当前板高信息不足"
                  : `已核验最高 ${plan.globalHigh.height}板`}
              </span>
            </header>
            {plan.globalHigh.stocks.length > 0 && (
              <>
                <ul>
                  {plan.globalHigh.stocks.slice(0, 4).map((stock) => (
                    <li key={stock.instrument_id}>
                      <button
                        className="daily-mainline-stock-name"
                        onClick={() => onStock(stock.instrument_id)}
                      >
                        {stock.name || stock.instrument_id}
                      </button>
                      <BoardTags
                        row={stock}
                        date={date}
                        snapshotId={snapshotId}
                        height={false}
                        volume={false}
                      />
                      <OriginalReason
                        text={
                          typeof stock.reason === "string" ? stock.reason : ""
                        }
                      />
                    </li>
                  ))}
                </ul>
                {plan.globalHigh.stocks.length > 4 && (
                  <details>
                    <summary>
                      展开其余 {plan.globalHigh.stocks.length - 4} 只同高度样本
                    </summary>
                    <div className="daily-mainline-high-names">
                      {plan.globalHigh.stocks.slice(4).map((stock) => (
                        <button
                          key={stock.instrument_id}
                          className="daily-mainline-stock-name"
                          onClick={() => onStock(stock.instrument_id)}
                        >
                          {stock.name || stock.instrument_id}
                        </button>
                      ))}
                    </div>
                  </details>
                )}
              </>
            )}
            <p className="daily-mainline-caption">
              按全部同日确认样本观察，不受下方重点方向范围影响，不等同于龙头判断。
            </p>
          </section>
          {!plan.available ? (
            <Empty
              title="来源暂不可用"
              description="当日归类资料尚未就绪或与当前行情不一致，请刷新后查看。"
            />
          ) : (
            <>
              <div className="daily-mainline-summary">
                <span>
                  确认样本 <b>{plan.total}</b> 只
                </span>
                <span>
                  重点方向 <b>{plan.groups.length}</b> 个
                </span>
                <span>
                  有来源原因{" "}
                  <b>{plan.classifiedCount - plan.missingReasonCount}</b> 只
                </span>
              </div>
              {!!focus.length && (
                <div className="daily-mainline-focus-groups daily-mainline-comparison">
                  <div
                    className="daily-mainline-direction-list"
                    aria-label="主线方向对比"
                  >
                    <div className="daily-mainline-direction-head">
                      <span>方向 / 原样本</span>
                      <span>首板</span>
                      <span>2板</span>
                      <span>3板+</span>
                      <span>待核验</span>
                    </div>
                    {focus.map((group) => (
                      <button
                        key={group.key}
                        className={`daily-mainline-direction-row${selected?.key === group.key ? " is-selected" : ""}`}
                        aria-pressed={selected?.key === group.key}
                        onClick={() => setSelectedKey(`${date}:${group.key}`)}
                      >
                        <span>
                          <strong>{group.name}</strong>
                          <small>
                            {group.count} 只
                            {search && group.visible.length !== group.count
                              ? ` · 匹配 ${group.visible.length}`
                              : ""}
                          </small>
                          <i
                            style={{
                              width: `${(100 * group.count) / Math.max(1, ...focus.map((value) => value.count))}%`,
                            }}
                          />
                        </span>
                        <b>
                          {
                            group.items.filter((item) => item.height === 1)
                              .length
                          }
                        </b>
                        <b>
                          {
                            group.items.filter((item) => item.height === 2)
                              .length
                          }
                        </b>
                        <b>
                          {
                            group.items.filter(
                              (item) => item.height != null && item.height >= 3,
                            ).length
                          }
                        </b>
                        <b>
                          {
                            group.items.filter((item) => item.height == null)
                              .length
                          }
                        </b>
                      </button>
                    ))}
                    <p className="daily-mainline-caption">
                      左侧数量保留原方向统计；搜索只影响匹配名单。
                    </p>
                  </div>
                  {selected && (
                    <section
                      className="daily-mainline-focus"
                      key={`${date}:${selected.key}`}
                    >
                      <header>
                        <h3>{selected.name}</h3>
                        <span>
                          {selected.count} 只
                          {search && selected.visible.length !== selected.count
                            ? ` · 匹配 ${selected.visible.length} 只`
                            : ""}
                        </span>
                      </header>
                      <div className="daily-mainline-focus-body">
                        <p className="daily-mainline-group-height">
                          {selected.maxKnownHeight == null
                            ? "板高信息不足"
                            : `本方向已核验最高 ${selected.maxKnownHeight}板`}
                        </p>
                        <ul className="daily-mainline-representatives">
                          {[...selected.visible]
                            .sort((a, b) => (b.height || 0) - (a.height || 0))
                            .slice(0, 2)
                            .map((value) => item(value, true))}
                        </ul>
                        {selected.visible.length > 2 && (
                          <details className="daily-mainline-more-members" open>
                            <summary>
                              展开其余 {selected.visible.length - 2} 只
                            </summary>
                            <ul className="daily-mainline-source-list">
                              {[...selected.visible]
                                .sort(
                                  (a, b) => (b.height || 0) - (a.height || 0),
                                )
                                .slice(2)
                                .map((value) => item(value))}
                            </ul>
                          </details>
                        )}
                      </div>
                    </section>
                  )}
                </div>
              )}
              {!plan.groups.length && !search && (
                <p className="daily-mainline-caption">
                  当日来源尚未形成集中的重点方向，可继续查看下方方向与个股线索。
                </p>
              )}
              {others.length > 0 && (
                <section className="daily-mainline-other">
                  <header>
                    <h3>其他方向与事件线索</h3>
                    <span>按来源归类</span>
                  </header>
                  <div className="daily-mainline-other-grid">
                    {others.map((group) => (
                      <details key={group.key} open>
                        <summary>
                          <strong>{group.name}</strong>
                          <span>
                            {group.count} 只
                            {group.kind === "catalyst" ? " · 事件线索" : ""}
                            {search && group.visible.length !== group.count
                              ? ` · 匹配 ${group.visible.length} 只`
                              : ""}
                          </span>
                        </summary>
                        <ul className="daily-mainline-source-list">
                          {group.visible.map((value) => item(value))}
                        </ul>
                      </details>
                    ))}
                  </div>
                </section>
              )}
              {individuals.length > 0 && (
                <section className="daily-mainline-individual">
                  <header>
                    <h3>个股来源线索</h3>
                    <span>
                      {plan.individualCues.length} 只
                      {search ? ` · 匹配 ${individuals.length} 只` : ""}
                    </span>
                  </header>
                  <ul className="daily-mainline-source-list">
                    {individuals.slice(0, 5).map((value) => item(value))}
                  </ul>
                  {individuals.length > 5 && (
                    <details className="daily-mainline-more-cues" open>
                      <summary>
                        展开其余 {individuals.length - 5} 只个股线索
                      </summary>
                      <ul className="daily-mainline-source-list">
                        {individuals.slice(5).map((value) => item(value))}
                      </ul>
                    </details>
                  )}
                </section>
              )}
              {search &&
                !focus.length &&
                !others.length &&
                !individuals.length && (
                  <Empty
                    title="没有匹配的来源线索"
                    description="请调整股票名称、代码或方向关键词；上方统计仍为当日完整返回样本。"
                  />
                )}
              {plan.missingReasonCount > 0 && (
                <p className="daily-mainline-missing">
                  {plan.missingReasonCount} 只来源暂未提供明确原因。
                </p>
              )}
              {plan.sourceGapCount > 0 && (
                <p className="daily-mainline-caption">
                  另有 {plan.sourceGapCount} 条样本来源信息不完整，未纳入归类。
                </p>
              )}
              <p className="daily-mainline-caption">
                以上为当日留存来源的自动归类，不代表全市场完整覆盖，也不认证上涨主因；个股原始原因按来源保留。
              </p>
            </>
          )}
        </>
      )}
    </section>
  );
}
