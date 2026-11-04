import { memo, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { CheckCircle2, X, XCircle } from "lucide-react";
import { ApiPageActivity, useApi } from "./lib/api";
import type { Row } from "./lib/api";
import { lazyView } from "./components/LazyView";
import AppShell, { marketRoutes, routeTitles } from "./components/AppShell";
import { isCalendarDate } from "./components/marketDate";
import {
  completedReviewDate,
  isIntradayReview,
  marketViewDate,
  updateReviewDateLink,
} from "./components/marketViewDate";
import type { MarketDateChoice } from "./components/marketViewDate";
import { Empty, ErrorBox, ToastContext, Loading, Panel } from "./components/UI";
const StockDrawer = lazyView(() =>
  import("./components/Charts").then((m) => ({ default: m.StockDrawer })),
);
import type { ChartEventBaseline } from "./components/Charts";
import {
  stockSelectionForOpen,
  stepStockSelection,
  trackingEventBaseline,
} from "./components/stockNavigation";
import type { StockNavigation } from "./components/stockNavigation";
const MarketOverview = lazyView(() =>
  import("./features/market/Market").then((m) => ({
    default: m.MarketOverview,
  })),
);
const MarketPage = lazyView(() =>
  import("./features/market/Market").then((m) => ({ default: m.MarketPage })),
);
const DragonTiger = lazyView(() => import("./features/market/DragonTiger"));
const IntradayObservation = lazyView(
  () => import("./features/market/IntradayObservation"),
);
const NoonReview = lazyView(() => import("./features/market/NoonReview"));
const AuctionReview = lazyView(() => import("./features/market/AuctionReview"));
import { auctionInitialDate } from "./features/market/auctionPresentation";
import {
  noonCalendarDates,
  noonChartDate,
  noonInitialDate,
  noonSectorSelection,
} from "./features/market/noonPresentation";
import {
  noonCloseChartDate,
  noonCloseSectorSelection,
} from "./features/market/noonCloseNavigation";
const Topics = lazyView(() => import("./features/topics/Topics"));
const Strategies = lazyView(() => import("./features/strategies/Strategies"));
const Tracking = lazyView(() => import("./features/tracking/Tracking"));
const Watchlist = lazyView(() => import("./features/watchlist/Watchlist"));
const Zodiac = lazyView(() => import("./features/zodiac/Zodiac"));
const Backtests = lazyView(() => import("./features/backtests/Backtests"));
const DataCenter = lazyView(() => import("./features/data/DataCenter"));
const Sources = lazyView(() => import("./features/sources/Sources"));
const Research = lazyView(() => import("./features/research/Research"));
const AIHub = lazyView(() => import("./features/ai/AIHub"));
import { upgradeChatLocation } from "./features/ai/aiNavigation";
import ReadonlyPreviewBanner from "./components/ReadonlyPreviewBanner";
const SectorChartDialog = lazyView(
  () => import("./features/market/SectorChartDialog"),
);
const SectorMembersDialog = lazyView(
  () => import("./features/sector/SectorMembersDialog"),
);
const NoonSectorDialog = lazyView(
  () => import("./features/market/NoonSectorDialog"),
);
import { globalSectorSelection } from "./components/searchPresentation";
import { marketInstallationState } from "./components/marketInstallation";
import IncrementProgressDialog from "./components/IncrementProgressDialog";
type SectorSelection = NonNullable<ReturnType<typeof globalSectorSelection>> & {
  topic: {
    noon_scope_notice?: string;
    noon_snapshot_date?: string;
    noon_chart_as_of?: string;
    noon_members_snapshot_id?: string;
  };
};
// Opening a stock drawer keeps the active market list's inputs unchanged.
const MemoMarketPage = memo(MarketPage);
const currentRoute = () => {
  const upgraded = upgradeChatLocation(location.hash);
  if (upgraded !== location.hash)
    window.history.replaceState(window.history.state, "", upgraded);
  const hash = location.hash.slice(1).split("?")[0];
  if (hash === "flow") return "sectors";
  return routeTitles[hash] ? hash : "market";
};
function readInitialTheme() {
  try {
    return localStorage.getItem("shixu-theme") || "dark";
  } catch {
    return "dark";
  }
}
function persistTheme(theme: string) {
  try {
    localStorage.setItem("shixu-theme", theme);
  } catch {
    // Restricted browser storage leaves the current in-memory theme usable.
  }
}
// Keep form state while a page is hidden, without sending date changes through
// every previously visited market page and chart. Activation receives fresh props.
const RetainedPage = memo(
  function RetainedPage({
    children,
  }: {
    active: boolean;
    children: ReactNode;
  }) {
    return <>{children}</>;
  },
  (_previous, next) => !next.active,
);
export default function App() {
  const bootstrap = useApi<Row>("/bootstrap");
  const [visible, setVisible] = useState(() => !document.hidden);
  const [incrementBlocked, setIncrementBlocked] = useState(true);
  const [route, setRoute] = useState(currentRoute),
    [visited, setVisited] = useState<string[]>([currentRoute()]),
    [dateChoice, setDateChoice] = useState<MarketDateChoice>(() => {
      const day =
        auctionInitialDate(location.hash) ||
        noonInitialDate(location.hash.replace(/^#intraday(?=\?|$)/, "#noon"));
      return day ? { date: day, mode: "manual" } : null;
    }),
    [stock, setStock] = useState<{
      id: string;
      asOf?: string;
      noonSnapshotId?: string;
      intradaySnapshotId?: string;
      eventBaseline?: ChartEventBaseline;
      navigation?: StockNavigation;
    } | null>(null),
    [context, setContext] = useState<Record<string, Row>>({}),
    [sector, setSector] = useState<SectorSelection | null>(null),
    [theme, setTheme] = useState(readInitialTheme),
    [toast, setToast] = useState<{ message: string; kind: string } | null>(
      null,
    );
  const noonDates = useApi<Row>(
    marketRoutes.has(route) && route !== "auction" && visible
      ? "/market/noon/dates"
      : null,
  );
  const auctionDates = useApi<Row>(
    route === "auction" && visible ? "/market/auction/dates" : null,
  );
  const reviewDates = route === "auction" ? auctionDates : noonDates;
  const [knownToday, setKnownToday] = useState("");
  const responseToday =
    typeof reviewDates.data?.today === "string" &&
    isCalendarDate(reviewDates.data.today)
      ? reviewDates.data.today
      : "";
  const today = responseToday > knownToday ? responseToday : knownToday;
  useEffect(() => {
    if (responseToday)
      setKnownToday((previous) =>
        responseToday > previous ? responseToday : previous,
      );
    if (responseToday)
      setDateChoice((choice) =>
        choice?.mode === "result" && choice.date === responseToday
          ? null
          : choice,
      );
  }, [responseToday]);
  const dates = [...(bootstrap.data?.market_dates || [])].sort();
  const latestDate = bootstrap.data?.data_date || dates.at(-1) || "";
  const date = marketViewDate(route, dateChoice, latestDate, today);
  const noonPublishedClose = noonCloseChartDate(dates, date, date);
  const noonSectorDay = sector?.topic.noon_snapshot_date ?? "";
  const noonSectorPublishedClose = noonCloseChartDate(
    dates,
    noonSectorDay,
    noonSectorDay,
  );
  const reloadBootstrap = bootstrap.reload;
  useEffect(() => {
    if ((route === "noon" || route === "intraday") && visible) reloadBootstrap();
  }, [route, visible, reloadBootstrap]);
  const setDate = useCallback((day: string) => {
    if (!isCalendarDate(day)) return;
    setDateChoice({ date: day, mode: "manual" });
    const hash = updateReviewDateLink(location.hash, day);
    if (hash !== location.hash)
      window.history.replaceState(window.history.state, "", hash);
  }, []);
  const resetDate = useCallback(() => {
    setDateChoice(null);
    const hash = updateReviewDateLink(location.hash);
    if (hash !== location.hash)
      window.history.replaceState(window.history.state, "", hash);
  }, []);
  // The small date directory also lets a direct base-market visit distinguish
  // the latest published close from today, without a browser-local clock.
  useEffect(() => {
    if (!visible || !marketRoutes.has(route)) return;
    const timer = window.setInterval(reviewDates.reload, 60000);
    return () => window.clearInterval(timer);
  }, [visible, route, reviewDates.reload]);
  const notify = useCallback(
    (message: string, kind = "success") => setToast({ message, kind }),
    [],
  );
  const navigate = useCallback((next: string, ctx?: Row) => {
    if (next === "flow") next = "sectors";
    if (next === "ai" && currentRoute() === "ai" && !ctx) return;
    window.dispatchEvent(new Event("workbench-before-route"));
    if (ctx) setContext((v) => ({ ...v, [next]: ctx }));
    location.hash = next;
    setRoute(next);
    setVisited((v) => (v.includes(next) ? v : [...v, next]));
  }, []);
  useEffect(() => {
    const update = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    const f = () => {
      window.dispatchEvent(new Event("workbench-before-route"));
      const next = currentRoute();
      const day =
        auctionInitialDate(location.hash) ||
        noonInitialDate(location.hash.replace(/^#intraday(?=\?|$)/, "#noon"));
      if (day) setDate(day);
      setRoute(next);
      setVisited((v) => (v.includes(next) ? v : [...v, next]));
    };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, [setDate]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    persistTheme(theme);
    window.dispatchEvent(new Event("workbench-theme"));
  }, [theme]);
  useEffect(() => {
    document.title = `${routeTitles[route]} · 冒险的梦想家`;
  }, [route]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5500);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    window.dispatchEvent(
      Object.assign(new CustomEvent("workbench-route", { detail: route }), {
        navigation: true,
      }),
    );
  }, [route]);
  const shellDates =
    route === "noon" || route === "intraday"
      ? noonCalendarDates(dates, { ...noonDates.data, today })
      : route === "auction"
        ? noonCalendarDates(dates, { ...auctionDates.data, today })
        : dates;
  const noonChartAsOf = noonChartDate(dates, date);
  const openStock = useCallback(
    (id: string, navigation?: StockNavigation) => {
      if (isIntradayReview(route) && !noonChartAsOf) {
        notify("该观察日期之前没有已发布收盘行情，不能打开历史图表。", "error");
        return;
      }
      setStock(
        stockSelectionForOpen(
          id,
          navigation,
          isIntradayReview(route)
            ? noonChartAsOf || undefined
            : marketRoutes.has(route)
              ? date || undefined
              : undefined,
        ),
      );
    },
    [route, date, noonChartAsOf, notify],
  );
  const openTrackedStock = useCallback(
    (id: string, round: Row, navigation?: StockNavigation) => {
      setStock({
        ...stockSelectionForOpen(id, navigation),
        eventBaseline: trackingEventBaseline(round),
      });
    },
    [],
  );
  const openNoonCloseStock = (id: string, closeDay: string) => {
    const asOf = noonCloseChartDate(dates, date, closeDay);
    if (!asOf || route !== "noon") {
      notify(
        "该日收盘行情尚未发布或与当前回看日期不符，暂不能打开图表。",
        "error",
      );
      return;
    }
    setStock(stockSelectionForOpen(id, undefined, asOf));
  };
  const openSectorMemberStock = useCallback(
    (id: string, asOf: string, navigation?: StockNavigation) => {
      // A sector's index entry price must never become a member stock's baseline.
      setStock(stockSelectionForOpen(id, navigation, asOf));
    },
    [],
  );
  function page(key: string) {
    if (marketRoutes.has(key)) {
      const state = marketInstallationState(
        bootstrap.data,
        bootstrap.loading,
        bootstrap.error,
      );
      if (state === "loading")
        return (
          <Panel title="正在连接本机数据服务">
            <Loading />
          </Panel>
        );
      if (state === "empty")
        return (
          <Panel title={routeTitles[key] || "盘面研究"}>
            <Empty
              title="尚未初始化行情"
              description="配置数据源后，在数据中心手动初始化。完成并发布行情后，这里会显示真实盘面；当前没有启动同步任务。"
              action={
                <div className="button-group">
                  <button className="btn" onClick={() => navigate("sources")}>
                    配置数据源
                  </button>
                  <button
                    className="btn primary"
                    onClick={() => navigate("data")}
                  >
                    前往数据中心
                  </button>
                </div>
              }
            />
          </Panel>
        );
      if (state === "error" || state === "unavailable")
        return (
          <Panel title="暂时无法读取行情状态">
            <Empty
              title="行情状态尚未确认"
              description={
                state === "error"
                  ? "请检查上方API连接错误后重试，数据源与数据中心仍可从导航访问。"
                  : "数据服务尚未返回有效的行情日期，请重试读取状态。"
              }
              action={
                <button className="btn" onClick={bootstrap.reload}>
                  重新读取状态
                </button>
              }
            />
          </Panel>
        );
      if (isIntradayReview(key) && !date)
        return (
          <Panel title="正在核验今天的日期">
            {reviewDates.error ? (
              <ErrorBox
                message={reviewDates.error}
                retry={reviewDates.reload}
              />
            ) : reviewDates.loading || !reviewDates.data ? (
              <Loading />
            ) : (
              <ErrorBox
                message="服务尚未返回有效的当天日期，请重新读取。"
                retry={reviewDates.reload}
              />
            )}
          </Panel>
        );
      if (!isIntradayReview(key) && date && !dates.includes(date))
        return (
          <Panel title="该日盘后行情尚未发布">
            <Empty
              title={`${date} 尚无已发布的盘后行情`}
              description="当前共享观察日可能只有竞价或午评快照。盘前、午间报价不会替代收盘价，其他盘面不会悄悄回退到更早一天。"
              action={
                <div className="button-group">
                  <button className="btn" onClick={() => navigate("auction")}>
                    查看当日竞价
                  </button>
                  <button className="btn" onClick={() => navigate("noon")}>
                    查看当日午评
                  </button>
                  <button className="btn primary" onClick={resetDate}>
                    回到已发布盘后日
                  </button>
                </div>
              }
            />
          </Panel>
        );
    }
    switch (key) {
      case "auction":
        return (
          <AuctionReview
            date={date}
            onDate={resetDate}
            onDatesChanged={auctionDates.reload}
            onStock={openStock}
            chartDate={noonChartAsOf || undefined}
          />
        );
      case "intraday": {
        return (
          <IntradayObservation
            key={date}
            date={date}
            today={today}
            dateFixed={dateChoice !== null}
            onResetDate={resetDate}
            onStock={(id, selection) => {
              const slot = selection?.slot;
              // The close anchor confirms publication even if the global date
              // directory has not refreshed yet.
              const asOf = slot === "close" ? date : noonChartAsOf;
              if (!asOf) {
                notify("该时点之前没有已发布收盘行情。", "error");
                return;
              }
              const snapshotId = selection?.snapshotId;
              setStock({
                ...stockSelectionForOpen(id, undefined, asOf),
                noonSnapshotId: slot === "noon" ? snapshotId : undefined,
                intradaySnapshotId: snapshotId?.startsWith("intraday_")
                  ? snapshotId
                  : undefined,
              });
            }}
          />
        );
      }
      case "noon":
        return (
          <NoonReview
            date={date}
            onDate={resetDate}
            onDatesChanged={noonDates.reload}
            onStock={(id, noonSnapshotId) => {
              if (noonPublishedClose) {
                setStock(
                  stockSelectionForOpen(id, undefined, noonPublishedClose),
                );
                return;
              }
              if (!noonChartAsOf) {
                notify("午评日期之前没有已发布收盘行情。", "error");
                return;
              }
              setStock({ id, asOf: noonChartAsOf, noonSnapshotId });
            }}
            chartDate={noonPublishedClose || noonChartAsOf || undefined}
            onCloseStock={openNoonCloseStock}
            onCloseSector={(row, view, closeDay) => {
              const next = noonCloseSectorSelection(
                row,
                date,
                dates,
                closeDay,
                view,
              );
              if (!next) {
                notify(
                  "收盘回看缺少明确板块来源或同日已发布行情，暂不能打开详情。",
                  "error",
                );
                return;
              }
              setStock(null);
              setSector(next);
            }}
            onSector={(row, view) => {
              const next = noonSectorSelection(row, date, dates, view);
              if (!next) {
                notify(
                  "该板块缺少明确来源代码或午评之前的已发布行情，暂不能打开详情。",
                  "error",
                );
                return;
              }
              setStock(null);
              setSector(next);
            }}
          />
        );
      case "market":
      case "indices":
        return (
          <MarketOverview
            date={date}
            bootstrap={bootstrap.data}
            onStock={openStock}
            indicesOnly={key === "indices"}
          />
        );
      case "emotion":
      case "sectors":
      case "ladder":
      case "gainers":
      case "abnormal":
        return <MemoMarketPage route={key} date={date} onStock={openStock} />;
      case "dragon-tiger":
        return (
          <DragonTiger date={date} onStock={openStock} onNavigate={navigate} />
        );
      case "topics":
        return <Topics onStock={openStock} onNavigate={navigate} />;
      case "strategy":
        return (
          <Strategies
            onStock={openStock}
            onNavigate={navigate}
            dataDate={bootstrap.data?.data_date}
          />
        );
      case "tracking":
        return (
          <Tracking
            onStock={openTrackedStock}
            onMemberStock={openSectorMemberStock}
            initialTargetType={context.tracking?.target_type}
            initialStrategyId={context.tracking?.strategy_id}
          />
        );
      case "zodiac":
        return <Zodiac onStock={openStock} />;
      case "watchlist":
        return <Watchlist onStock={openStock} />;
      case "backtest":
        return (
          <Backtests
            onStock={openStock}
            initialStrategyId={context.backtest?.strategy_id}
            marketDates={dates}
            dataVersion={bootstrap.data?.market_snapshot_id}
          />
        );
      case "data":
        return (
          <DataCenter
            onPublished={bootstrap.reload}
            initialTaskId={context.data?.task_id}
          />
        );
      case "sources":
        return (
          <Sources
            initialTab={context.sources?.tab === "ai" ? "ai" : "market"}
          />
        );
      case "ai":
        return <AIHub onConfigure={() => navigate("sources", { tab: "ai" })} />;
      case "research":
        return (
          <Research
            initialInstrumentId={context.research?.instrument_id}
            onStock={(id, asOf) =>
              setStock(stockSelectionForOpen(id, undefined, asOf))
            }
          />
        );
      default:
        return null;
    }
  }
  return (
    <ApiPageActivity.Provider value={!incrementBlocked}>
      <ToastContext.Provider value={notify}>
        <AppShell
          route={route}
          onNavigate={navigate}
          date={date}
          onDate={setDate}
          onResetDate={resetDate}
          today={today}
          dateFixed={
            dateChoice?.mode === "manual" ||
            (isIntradayReview(route) && dateChoice !== null)
          }
          datePublished={dates.includes(date)}
          dates={shellDates}
          dataDate={bootstrap.data?.data_date}
          dataLoading={bootstrap.loading}
          dataError={!!bootstrap.error}
          theme={theme}
          onTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          onStock={(id) => setStock(stockSelectionForOpen(id))}
          onSector={(row) => {
            const next = globalSectorSelection(
              row,
              date,
              bootstrap.data?.data_date,
              marketRoutes.has(route),
            );
            if (!next) {
              notify("请先完成行情初始化，核验板块观察日期。", "error");
              return;
            }
            setStock(null);
            setSector(next);
          }}
        >
          <ReadonlyPreviewBanner
            enabled={import.meta.env.VITE_READONLY_PREVIEW === "1"}
          />
          {bootstrap.data?.instance_label && (
            <div className="readonly-preview-banner" role="status">
              <strong>{bootstrap.data.instance_label}</strong>
              <span>此环境可验证 AI 配置与会话，使用独立业务库。</span>
            </div>
          )}
          {bootstrap.error && (
            <ErrorBox
              message={"本机 API 暂未就绪：" + bootstrap.error}
              retry={bootstrap.reload}
            />
          )}{" "}
          {visited.map((key) => (
            <div className="workspace-page" key={key} hidden={key !== route}>
              <ApiPageActivity.Provider
                value={key === route && visible && !incrementBlocked}
              >
                <RetainedPage active={key === route}>{page(key)}</RetainedPage>
              </ApiPageActivity.Provider>
            </div>
          ))}
        </AppShell>
        {sector &&
          (sector.topic.noon_members_snapshot_id ? (
            <NoonSectorDialog
              key={`${sector.topic.noon_members_snapshot_id}:${sector.topic.topic_code}`}
              topic={sector.topic}
              snapshotId={sector.topic.noon_members_snapshot_id}
              initialView={sector.view}
              onClose={() => setSector(null)}
              closeChartDate={noonSectorPublishedClose || undefined}
              onStock={(id, asOf) => {
                setStock(
                  noonSectorPublishedClose
                    ? stockSelectionForOpen(
                        id,
                        undefined,
                        noonSectorPublishedClose,
                      )
                    : {
                        id,
                        asOf,
                        noonSnapshotId: sector.topic.noon_members_snapshot_id,
                      },
                );
              }}
            />
          ) : sector.view === "chart" ? (
            <SectorChartDialog
              topic={{
                topicCode: sector.topic.topic_code,
                name: sector.topic.name,
                proxy: false,
                notice:
                  sector.topic.noon_scope_notice ||
                  `当前目录中的${sector.topic.kind === "industry" ? "行业" : "概念"}板块；历史成分需单独核验`,
              }}
              date={sector.date}
              onClose={() => setSector(null)}
              onMembers={() => setSector({ ...sector, view: "members" })}
            />
          ) : (
            <SectorMembersDialog
              topic={sector.topic}
              date={sector.date}
              businessDate={bootstrap.data?.data_date}
              contextNotice={sector.topic.noon_scope_notice}
              initialScope={
                !sector.topic.noon_snapshot_date &&
                sector.date === bootstrap.data?.data_date
                  ? "latest"
                  : "historical"
              }
              onClose={() => setSector(null)}
              onChart={(asOf) =>
                setSector({
                  ...sector,
                  date:
                    sector.topic.noon_chart_as_of &&
                    (asOf || sector.date) > sector.topic.noon_chart_as_of
                      ? sector.topic.noon_chart_as_of
                      : asOf || sector.date,
                  view: "chart",
                })
              }
              onStock={(id, quoteDate, navigation) =>
                setStock(
                  stockSelectionForOpen(
                    id,
                    navigation,
                    sector.topic.noon_chart_as_of &&
                      quoteDate > sector.topic.noon_chart_as_of
                      ? sector.topic.noon_chart_as_of
                      : quoteDate,
                  ),
                )
              }
            />
          ))}
        {stock && (
          <StockDrawer
            id={stock.id}
            asOf={stock.asOf}
            noonSnapshotId={stock.noonSnapshotId}
            intradaySnapshotId={stock.intradaySnapshotId}
            eventBaseline={stock.eventBaseline}
            navigation={stock.navigation}
            onStep={(direction) =>
              setStock((current) =>
                current ? stepStockSelection(current, direction) : null,
              )
            }
            onClose={() => setStock(null)}
            onResearch={(id) => {
              setStock(null);
              navigate("research", { instrument_id: id });
            }}
          />
        )}
        <IncrementProgressDialog
          onBlockedChange={setIncrementBlocked}
          onAuctionFinished={(task) => {
            setDateChoice((choice) =>
              completedReviewDate(choice, task.target_date, today),
            );
            auctionDates.reload();
            window.dispatchEvent(new Event("workbench-auction-unlocked"));
            navigate("auction");
          }}
          onNoonFinished={(task) => {
            setDateChoice((choice) =>
              completedReviewDate(choice, task.target_date, today),
            );
            noonDates.reload();
            window.dispatchEvent(new Event("workbench-noon-unlocked"));
            navigate("noon");
          }}
          onFinished={() => {
            window.dispatchEvent(new Event("workbench-increment-unlocked"));
            bootstrap.reload();
            window.dispatchEvent(
              new CustomEvent("workbench-route", { detail: "data" }),
            );
          }}
        />
        {toast && (
          <div className={"toast " + toast.kind}>
            {toast.kind === "error" ? (
              <XCircle size={18} />
            ) : (
              <CheckCircle2 size={18} />
            )}
            <span>{toast.message}</span>
            <button
              className="icon-btn"
              aria-label="关闭消息"
              onClick={() => setToast(null)}
            >
              <X size={14} />
            </button>
          </div>
        )}
      </ToastContext.Provider>
    </ApiPageActivity.Provider>
  );
}
