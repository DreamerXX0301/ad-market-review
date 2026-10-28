import { memo, useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { CheckCircle2, X, XCircle } from "lucide-react";
import { ApiPageActivity, useApi } from "./lib/api";
import type { Row } from "./lib/api";
import AppShell, { marketRoutes, routeTitles } from "./components/AppShell";
import { Empty, ErrorBox, ToastContext, Loading, Panel } from "./components/UI";
import { StockDrawer } from "./components/Charts";
import type { ChartEventBaseline } from "./components/Charts";
import {
  stockSelectionForOpen,
  stepStockSelection,
  trackingEventBaseline,
} from "./components/stockNavigation";
import type { StockNavigation } from "./components/stockNavigation";
import { MarketOverview, MarketPage } from "./features/market/Market";
import DragonTiger from "./features/market/DragonTiger";
import NoonReview from "./features/market/NoonReview";
import AuctionReview from "./features/market/AuctionReview";
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
import Topics from "./features/topics/Topics";
import Strategies from "./features/strategies/Strategies";
import Tracking from "./features/tracking/Tracking";
import Watchlist from "./features/watchlist/Watchlist";
import Backtests from "./features/backtests/Backtests";
import DataCenter from "./features/data/DataCenter";
import Sources from "./features/sources/Sources";
import Research from "./features/research/Research";
import ReadonlyPreviewBanner from "./components/ReadonlyPreviewBanner";
import SectorChartDialog from "./features/market/SectorChartDialog";
import SectorMembersDialog from "./features/sector/SectorMembersDialog";
import { globalSectorSelection } from "./components/searchPresentation";
import { marketInstallationState } from "./components/marketInstallation";
import IncrementProgressDialog from "./components/IncrementProgressDialog";
type SectorSelection = NonNullable<ReturnType<typeof globalSectorSelection>> & {
  topic: {
    noon_scope_notice?: string;
    noon_snapshot_date?: string;
    noon_chart_as_of?: string;
  };
};
// Opening a stock drawer keeps the active market list's inputs unchanged.
const MemoMarketPage = memo(MarketPage);
const currentRoute = () => {
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
    [date, setDate] = useState(
      () => auctionInitialDate(location.hash) || noonInitialDate(location.hash),
    ),
    [stock, setStock] = useState<{
      id: string;
      asOf?: string;
      eventBaseline?: ChartEventBaseline;
      navigation?: StockNavigation;
    } | null>(null),
    [context, setContext] = useState<Record<string, Row>>({}),
    [sector, setSector] = useState<SectorSelection | null>(null),
    [theme, setTheme] = useState(readInitialTheme),
    [toast, setToast] = useState<{ message: string; kind: string } | null>(
      null,
    );
  const previousPublishedDate = useRef<string | undefined>(undefined);
  const noonDates = useApi<Row>(route === "noon" ? "/market/noon/dates" : null);
  const auctionDates = useApi<Row>(
    route === "auction" ? "/market/auction/dates" : null,
  );
  const notify = useCallback(
    (message: string, kind = "success") => setToast({ message, kind }),
    [],
  );
  const navigate = useCallback((next: string, ctx?: Row) => {
    if (next === "flow") next = "sectors";
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
      const next = currentRoute();
      setRoute(next);
      setVisited((v) => (v.includes(next) ? v : [...v, next]));
    };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    persistTheme(theme);
    window.dispatchEvent(new Event("workbench-theme"));
  }, [theme]);
  useEffect(() => {
    if (!bootstrap.data) return;
    const latest =
      bootstrap.data.data_date || bootstrap.data.market_dates?.at(-1) || "";
    const previous = previousPublishedDate.current;
    previousPublishedDate.current = latest;
    // Follow a newly published latest day only when the user was already viewing latest.
    setDate((current) => (!current || current === previous ? latest : current));
  }, [bootstrap.data]);
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
  const dates = [...(bootstrap.data?.market_dates || [])].sort();
  const shellDates =
    route === "noon"
      ? noonCalendarDates(dates, noonDates.data)
      : route === "auction"
        ? noonCalendarDates(dates, auctionDates.data)
        : dates;
  const noonChartAsOf = noonChartDate(dates, date);
  const openStock = useCallback(
    (id: string, navigation?: StockNavigation) => {
      if (["noon", "auction"].includes(route) && !noonChartAsOf) {
        notify("该观察日期之前没有已发布收盘行情，不能打开历史图表。", "error");
        return;
      }
      setStock(
        stockSelectionForOpen(
          id,
          navigation,
          ["noon", "auction"].includes(route)
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
      if (key !== "noon" && key !== "auction" && date && !dates.includes(date))
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
                  <button
                    className="btn primary"
                    onClick={() =>
                      bootstrap.data?.data_date &&
                      setDate(bootstrap.data.data_date)
                    }
                  >
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
            onDate={setDate}
            onDatesChanged={auctionDates.reload}
            onStock={openStock}
            chartDate={noonChartAsOf || undefined}
          />
        );
      case "noon":
        return (
          <NoonReview
            date={date}
            onDate={setDate}
            onDatesChanged={noonDates.reload}
            onStock={openStock}
            chartDate={noonChartAsOf || undefined}
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
        return <Sources />;
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
          dates={shellDates}
          dataDate={bootstrap.data?.data_date}
          dataLoading={bootstrap.loading}
          dataError={!!bootstrap.error}
          theme={theme}
          onTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          onStock={openStock}
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
          (sector.view === "chart" ? (
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
          onNoonFinished={(task) => {
            if (typeof task.target_date === "string") setDate(task.target_date);
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
