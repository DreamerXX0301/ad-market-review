import {
  Activity,
  ArrowLeftRight,
  BarChart3,
  BookOpen,
  Boxes,
  CalendarDays,
  ChartCandlestick,
  ChevronRight,
  Database,
  FlaskConical,
  Layers3,
  Menu,
  MessageSquare,
  Moon,
  Network,
  PanelLeftClose,
  Settings2,
  Search,
  ShieldAlert,
  Sun,
  Sunrise,
  Star,
  Target,
  TrendingUp,
  X,
} from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Row } from "../lib/api";
import InstrumentSearch from "./InstrumentSearch";
import MarketDatePicker from "./MarketDatePicker";
import MarketDateNotice from "./MarketDateNotice";
import ActiveTaskBanner from "./ActiveTaskBanner";
import AuthorContact from "./AuthorContact";
import { Badge } from "./UI";
import "./Brand.css";
import "./AppShell.css";
import LineAlertNotice from "../features/watchlist/LineAlertNotice";
export const navigation = [
  {
    label: "盘面研究",
    items: [
      { id: "market", name: "盘面总览", icon: BarChart3 },
      {
        id: "auction",
        name: "盘前竞价",
        icon: Sunrise,
      },
      { id: "intraday", name: "盘中观察", icon: TrendingUp },
      { id: "noon", name: "午评快照", icon: Sun },
      { id: "indices", name: "指数行情", icon: ChartCandlestick },
      { id: "emotion", name: "市场情绪", icon: Activity },
      { id: "sectors", name: "行业与概念", icon: Layers3 },
      { id: "ladder", name: "涨停梯队", icon: Boxes },
      { id: "dragon-tiger", name: "龙虎榜", icon: ArrowLeftRight },
      { id: "gainers", name: "30日涨幅榜", icon: TrendingUp },
      { id: "abnormal", name: "异动与监管", icon: ShieldAlert },
    ],
  },
  {
    label: "自定义研究",
    items: [
      { id: "topics", name: "题材库", icon: Network },
      { id: "strategy", name: "策略管理", icon: Settings2 },
      { id: "tracking", name: "策略跟踪", icon: Target },
      { id: "watchlist", name: "我的自选", icon: Star },
      { id: "zodiac", name: "生肖跟踪", icon: CalendarDays },
      { id: "research", name: "个股研究", icon: BookOpen },
      { id: "ai", name: "AI 助手", icon: MessageSquare },
      { id: "backtest", name: "策略回测", icon: FlaskConical },
    ],
  },
  {
    label: "管理",
    items: [
      { id: "data", name: "数据中心", icon: Database },
      { id: "sources", name: "数据源", icon: Settings2 },
    ],
  },
];
export const marketRoutes = new Set(navigation[0].items.map((x) => x.id));
export const routeTitles = Object.fromEntries(
  navigation.flatMap((g) => g.items.map((x) => [x.id, x.name])),
);
export default function AppShell({
  route,
  onNavigate,
  date,
  onDate,
  onResetDate,
  today,
  dateFixed,
  datePublished,
  dates,
  dataDate,
  dataLoading = false,
  dataError = false,
  theme,
  onTheme,
  onStock,
  onSector,
  children,
}: {
  route: string;
  onNavigate: (route: string) => void;
  date: string;
  onDate: (date: string) => void;
  onResetDate: () => void;
  today: string;
  dateFixed: boolean;
  datePublished: boolean;
  dates: string[];
  dataDate?: string;
  dataLoading?: boolean;
  dataError?: boolean;
  theme: string;
  onTheme: () => void;
  onStock: (id: string) => void;
  onSector: (row: Row) => void;
  children: ReactNode;
}) {
  const mainRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    // Routes keep their business state; the document viewport starts at the page header.
    window.scrollTo(0, 0);
    mainRef.current?.scrollTo(0, 0);
  }, [route]);
  const [mobileOpen, setMobileOpen] = useState(false),
    [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const isMarket = marketRoutes.has(route);
  const workspace = isMarket
    ? "market"
    : ["data", "sources"].includes(route)
      ? "data"
      : route === "research" || route === "ai"
        ? "research"
        : "strategy";
  const currentNavigation = navigation
    .flatMap((group) => group.items)
    .find((item) => item.id === route);
  const PageIcon = currentNavigation?.icon || BarChart3;
  return (
    <div className="app-layout" data-workspace={workspace} data-route={route}>
      <button
        className={"mobile-overlay " + (mobileOpen ? "visible" : "")}
        aria-label="关闭导航"
        onClick={() => setMobileOpen(false)}
      />
      <aside className={"sidebar " + (mobileOpen ? "mobile-open" : "")}>
        <a
          className="brand brand--dreamer"
          href="#market"
          aria-label="冒险的梦想家 · 返回盘面总览"
          onClick={() => onNavigate("market")}
        >
          <div className="brand-mark">
            <svg width="25" height="25" viewBox="0 0 25 25" aria-hidden="true">
              <path
                d="M3 18V12M9 21V7M15 17V3M21 13V6"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
              />
            </svg>
          </div>
          <div className="brand-copy">
            <strong>冒险的梦想家</strong>
            <span>行情 · 策略 · 研究</span>
          </div>
        </a>
        <div className="sidebar-workspace">
          <span className="status-dot good" />
          本地研究工作台<span className="local-tag">本地</span>
        </div>
        <nav>
          {navigation.map((group) => (
            <div
              className="nav-group"
              key={group.label}
              data-tone={
                group.label === "盘面研究"
                  ? "market"
                  : group.label === "管理"
                    ? "data"
                    : "strategy"
              }
            >
              <div className="nav-group-title">{group.label}</div>
              {group.items.map((item) => (
                <button
                  className={route === item.id ? "nav-item active" : "nav-item"}
                  key={item.id}
                  aria-current={route === item.id ? "page" : undefined}
                  onClick={() => {
                    onNavigate(item.id);
                    setMobileOpen(false);
                  }}
                >
                  <item.icon size={16} />
                  <span>{item.name}</span>
                  {item.id === "backtest" && (
                    <span className="nav-new">研究</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div>
            <span className="status-dot good" />
            本机数据服务
          </div>
          <p>盘后可自动或手动更新</p>
          <AuthorContact />
          <button className="theme-toggle" onClick={onTheme}>
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            <span>{theme === "dark" ? "切换浅色" : "切换深色"}</span>
            <kbd>◐</kbd>
          </button>
        </div>
      </aside>
      <main className="app-main" ref={mainRef}>
        <header className="topbar">
          <div className="topbar-title">
            <button
              className="icon-btn mobile-menu"
              aria-label="打开导航"
              onClick={() => setMobileOpen(true)}
            >
              <Menu size={20} />
            </button>
            <div className="page-heading-icon">
              <PageIcon size={23} strokeWidth={1.7} />
            </div>
            <div>
              <div className="breadcrumb dreamer-breadcrumb">
                <span className="breadcrumb-product">冒险的梦想家</span>
                <ChevronRight size={11} />{" "}
                {isMarket
                  ? "盘面研究"
                  : route === "data" || route === "sources"
                    ? "数据管理"
                    : "自定义研究"}
              </div>
              <h1>{routeTitles[route] || "盘面总览"}</h1>
            </div>
          </div>
          <div className="topbar-controls">
            <button
              className="icon-btn mobile-search-toggle"
              aria-label="搜索股票、指数、行业和概念"
              onClick={() => setMobileSearchOpen((v) => !v)}
            >
              <Search size={17} />
            </button>
            <InstrumentSearch
              compact
              includeSectors
              placeholder="名称 / 代码 / 拼音 / 首字母"
              onSelect={(id) => onStock(id)}
              onSector={onSector}
            />
            {isMarket ? (
              <MarketDatePicker
                value={date}
                dates={dates}
                onChange={onDate}
                onLatest={onResetDate}
                fixed={dateFixed}
              />
            ) : route === "ai" ? (
              <div className="current-business-date">股票研究 · 行情复盘</div>
            ) : (
              <div className="current-business-date">
                <span className={`status-dot${dataDate ? " good" : ""}`} />
                行情截止{" "}
                {dataDate ||
                  (dataLoading
                    ? "读取中…"
                    : dataError
                      ? "读取失败"
                      : "未初始化")}
              </div>
            )}
            <LineAlertNotice businessDate={dataDate} onNavigate={onNavigate} />
            <button className="avatar" title="本机个人研究空间">
              本
            </button>
          </div>
        </header>
        {mobileSearchOpen && (
          <div className="mobile-global-search">
            <InstrumentSearch
              includeSectors
              placeholder="名称 / 代码 / 拼音 / 首字母"
              onSector={(row) => {
                setMobileSearchOpen(false);
                onSector(row);
              }}
              onSelect={(id) => {
                setMobileSearchOpen(false);
                onStock(id);
              }}
            />
            <button
              className="icon-btn"
              aria-label="关闭搜索"
              onClick={() => setMobileSearchOpen(false)}
            >
              <X size={18} />
            </button>
          </div>
        )}
        <div className="page-container">
          <ActiveTaskBanner onNavigate={onNavigate} />
          {isMarket && route !== "intraday" && (
            <MarketDateNotice
              date={date}
              today={today}
              latest={dataDate || ""}
              intraday={route === "noon" || route === "auction"}
              fixed={dateFixed}
              published={datePublished}
              onReset={onResetDate}
            />
          )}
          {children}
          {route !== "ai" && (
            <footer className="workspace-footer dreamer-footer">
              <span>
                <strong>冒险的梦想家</strong>
                <span className="footer-product-description">
                  {" "}
                  · 本地 A股研究工作台
                </span>
              </span>
              <span>
                数据来自已发布快照 · 未知数据不补零 · 研究结果不代表交易指令
              </span>
            </footer>
          )}
        </div>
      </main>
    </div>
  );
}
