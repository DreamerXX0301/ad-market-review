import { Fragment, useState } from "react";
import { ChevronRight } from "lucide-react";
import type { Row } from "../../lib/api";
import { integer, num, percent, tone } from "../../lib/format";
import CopyStockCode from "../../components/CopyStockCode";
import AuctionCopyText from "./AuctionCopyText";
import AuctionClassifications from "./AuctionClassifications";
import {
  auctionCloseGroupsView,
  auctionCloseNumber,
} from "./auctionClosePresentation";
import "./AuctionCloseGroups.css";

export default function AuctionCloseGroups({
  date,
  view,
  topicView = null,
  selectedIds = null,
  onStock,
  chartDate,
}: {
  date: string;
  view: Row | null;
  topicView?: Row | null;
  selectedIds?: ReadonlySet<string> | null;
  onStock?: (id: string) => void;
  chartDate?: string;
}) {
  const groups = auctionCloseGroupsView(date, view, selectedIds);
  const scope = `${date}:${selectedIds ? [...selectedIds].sort().join(",") : "all"}`;
  const [expanded, setExpanded] = useState<{
    scope: string;
    values: Record<string, boolean>;
  }>({ scope: "", values: {} });
  const selection = !!selectedIds?.size;
  const isOpen = (group: Row) =>
    expanded.scope === scope && Object.hasOwn(expanded.values, group.key)
      ? expanded.values[group.key]
      : selection && group.items.length > 0;
  const toggle = (group: Row) => {
    if (!group.items.length) return;
    const next = !isOpen(group);
    setExpanded((previous) => ({
      scope,
      values: {
        ...(previous.scope === scope ? previous.values : {}),
        [group.key]: next,
      },
    }));
  };
  const price = (value: unknown) => {
    const number = auctionCloseNumber(value);
    return number !== null && number > 0 ? num(number) : "—";
  };
  if (!groups)
    return (
      <p className="auction-close-groups-empty">尚未取得所选日期的分组核对。</p>
    );
  if (!groups.length)
    return (
      <p className="auction-close-groups-empty">
        当前范围暂无可展示的盘前分组样本。
      </p>
    );
  return (
    <div className="auction-close-groups">
      <p className="auction-close-groups-note">
        沿用盘前分组，按竞价均幅排序；收盘结果不改变原分组。
        收盘涨幅相对来源昨收；竞价后变化相对竞价价。
        {selection ? " 当前为所选行业概念交集。" : " 点选一行查看股票。"}
      </p>
      <div className="auction-close-groups-scroll">
        <table className="auction-close-group-table">
          <colgroup>
            <col style={{ width: "22%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "15%" }} />
            <col style={{ width: "16%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "19%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>盘前分组</th>
              <th>股数</th>
              <th>竞价均幅</th>
              <th>收盘均涨幅</th>
              <th>竞价至收盘均变化</th>
              <th>有效覆盖</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => {
              const open = isOpen(group);
              const detailsId = `auction-close-group-${date}-${group.key}`;
              return (
                <Fragment key={group.key}>
                  <tr
                    className={`auction-close-group-row ${group.tone}${group.items.length ? " is-expandable" : ""}${open ? " is-open" : ""}`}
                    data-close-group={group.key}
                    onClick={() => toggle(group)}
                  >
                    <th scope="row" title={group.interpretation || group.title}>
                      <button
                        type="button"
                        className="auction-close-group-toggle"
                        disabled={!group.items.length}
                        aria-expanded={open}
                        aria-controls={
                          group.items.length ? detailsId : undefined
                        }
                      >
                        <ChevronRight
                          size={13}
                          className={open ? "is-open" : ""}
                        />
                        <span>{group.label}</span>
                        {group.key === "limit_up_open" &&
                          group.state === "reference" && <small>参考</small>}
                      </button>
                    </th>
                    <td>
                      {group.pending_limit ? (
                        <span className="muted">待核验</span>
                      ) : (
                        integer(group.count)
                      )}
                    </td>
                    <td className={tone(group.average_auction_pct)}>
                      {percent(group.average_auction_pct)}
                    </td>
                    <td className={tone(group.average_close_pct)}>
                      {percent(group.average_close_pct)}
                    </td>
                    <td className={tone(group.average_auction_to_close_pct)}>
                      {percent(group.average_auction_to_close_pct)}
                    </td>
                    <td className="auction-close-group-coverage">
                      {group.pending_limit ? (
                        "—"
                      ) : group.pending_close ? (
                        "待发布"
                      ) : (
                        <>
                          <span>
                            收盘 {group.close_valid_count}/{group.count}
                          </span>
                          <span>
                            变化 {group.valid_count}/{group.count}
                          </span>
                        </>
                      )}
                    </td>
                  </tr>
                  {open && group.items.length > 0 && (
                    <tr className="auction-close-group-detail-row">
                      <td colSpan={6}>
                        <div
                          className="auction-close-group-detail-scroll"
                          id={detailsId}
                        >
                          <table className="auction-close-group-detail-table">
                            <colgroup>
                              <col style={{ width: "28%" }} />
                              <col style={{ width: "11%" }} />
                              <col style={{ width: "11%" }} />
                              <col style={{ width: "14%" }} />
                              <col style={{ width: "9%" }} />
                              <col style={{ width: "9%" }} />
                              <col style={{ width: "18%" }} />
                            </colgroup>
                            <thead>
                              <tr>
                                <th>股票 / 代码</th>
                                <th>竞价涨幅</th>
                                <th>收盘涨幅</th>
                                <th>竞价至收盘</th>
                                <th>开盘价</th>
                                <th>收盘价</th>
                                <th>核对状态</th>
                              </tr>
                            </thead>
                            <tbody>
                              {group.items.map((row: Row, index: number) => (
                                <tr
                                  key={`${row.instrument_id}:${index}`}
                                  className={
                                    selection ? "is-associated" : undefined
                                  }
                                  data-associated={
                                    selection ? "true" : undefined
                                  }
                                >
                                  <td>
                                    <div className="auction-close-group-stock">
                                      <div className="auction-close-group-stock-name">
                                        <button
                                          type="button"
                                          className="auction-stock"
                                          disabled={!onStock || !chartDate}
                                          title={
                                            chartDate
                                              ? `K线截至 ${chartDate}`
                                              : "暂无可核验图表截止日"
                                          }
                                          onClick={() =>
                                            onStock?.(row.instrument_id)
                                          }
                                        >
                                          {row.name || row.instrument_id}
                                        </button>
                                        {row.name && (
                                          <AuctionCopyText
                                            text={row.name}
                                            label={`复制股票名称 ${row.name}`}
                                          />
                                        )}
                                      </div>
                                      <CopyStockCode
                                        instrumentId={row.instrument_id}
                                      />
                                      <AuctionClassifications
                                        view={topicView}
                                        date={date}
                                        instrumentId={row.instrument_id}
                                        compact
                                      />
                                    </div>
                                  </td>
                                  <td className={tone(row.display_auction_pct)}>
                                    {percent(row.display_auction_pct)}
                                  </td>
                                  <td
                                    className={tone(row.display_close_pct)}
                                    title={row.close_pct_reason}
                                  >
                                    {percent(row.display_close_pct)}
                                  </td>
                                  <td className={tone(row.display_change_pct)}>
                                    {percent(row.display_change_pct)}
                                  </td>
                                  <td>{price(row.open)}</td>
                                  <td>{price(row.display_close_price)}</td>
                                  <td className="auction-close-group-status">
                                    {group.pending_close
                                      ? "待发布"
                                      : row.reason ||
                                        (row.state === "observed"
                                          ? "同日价格已核验"
                                          : row.state === "mismatch"
                                            ? "开盘与竞价不一致"
                                            : "待核验")}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <p className="auction-close-group-detail-note">
                          缺失和开盘不一致样本保留事实，收盘均幅及竞价后变化只统计已核验有效项；价差不等于可实现收益。
                        </p>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
