import { useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  ExternalLink,
  RefreshCw,
  Star,
} from "lucide-react";
import { Badge, Empty, ErrorBox, Loading, Panel } from "../../components/UI";
import { useApi } from "../../lib/api";
import AIEntry from "../ai/AIEntry";
import {
  calendarImportance,
  classifyNews,
  newsFilters,
  selectFeaturedNews,
} from "./newsPresentation";
import "./NewsEvents.css";

const eventStatuses = {
  confirmed: { label: "已确认", tone: "good" },
  tentative: { label: "暂定", tone: "warn" },
  postponed: { label: "延期", tone: "warn" },
  cancelled: { label: "已取消", tone: "neutral" },
};
type Topic = { name: string; basis: string };
type Source = { source: string; url: string };
type Message = Source & {
  id: string;
  title: string;
  summary: string | null;
  published_at: string;
  first_seen_at: string | null;
  topics?: Topic[];
  symbols?: { symbol: string; name?: string; basis: string }[];
  event_id?: string | null;
};
type Stage = {
  status:
    "pending" | "missing" | "expired" | "complete" | "partial" | "unavailable";
  items: Message[];
};
type NewsView = { date: string; retention_days: number; stages: Stage[] };
type TimelineEntry = {
  key: string;
  item: Message;
  category: ReturnType<typeof classifyNews>;
  timestamp: number;
  time: string;
};
type NewsFilterKey = (typeof newsFilters)[number]["key"];
type CalendarEvent = Source & {
  id: string;
  title: string;
  event_date: string;
  status: keyof typeof eventStatuses;
  summary?: string | null;
  topics?: Topic[];
  published_at?: string | null;
  published_date?: string | null;
};
type CalendarView = {
  date: string;
  end_date: string;
  status: "available" | "unavailable";
  reason: string | null;
  events: CalendarEvent[];
};

const chinaTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function timeLabel(value: string, date?: string) {
  const timestamp = new Date(value);
  if (!Number.isFinite(timestamp.getTime())) return "时间未核验";
  const parts = Object.fromEntries(
    chinaTime.formatToParts(timestamp).map(({ type, value }) => [type, value]),
  );
  const day = `${parts.year}-${parts.month}-${parts.day}`;
  const clock = `${parts.hour}:${parts.minute}:${parts.second}`;
  return day === date ? clock : `${day} ${clock}`;
}

function SourceLink({ source }: { source: Source }) {
  if (!/^https:\/\/[^\s/]+/i.test(source.url)) return null;
  return (
    <a
      className="btn small"
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
    >
      {source.source}原文 <ExternalLink size={13} aria-hidden="true" />
    </a>
  );
}

function TopicEvidence({
  topics,
  sourceTags = false,
}: {
  topics?: Topic[];
  sourceTags?: boolean;
}) {
  if (!topics?.length) return <p>暂无已核验的题材关联。</p>;
  return (
    <ul
      className={
        sourceTags
          ? "news-events-evidence news-events-source-tags"
          : "news-events-evidence"
      }
    >
      {topics.map(({ name, basis }) => (
        <li key={`${name}:${basis}`}>
          <Badge>{name}</Badge>
          {(!sourceTags || basis !== "财联社来源标签") && <span>{basis}</span>}
        </li>
      ))}
    </ul>
  );
}

function ImportanceBadge({ label }: { label: string }) {
  return (
    <span className="news-events-importance">
      <Star size={12} aria-hidden="true" />
      {label}
    </span>
  );
}

function NewsLabels({ category }: { category: TimelineEntry["category"] }) {
  const { importance, move } = category;
  const DirectionIcon =
    move?.direction === "up" ? ArrowUpRight : ArrowDownRight;
  return (
    <span className="news-events-labels">
      {importance && <ImportanceBadge label={importance.label} />}
      <span className="news-events-category">{category.label}</span>
      {move && (
        <span className="news-events-move" data-news-move={move.direction}>
          <DirectionIcon size={13} aria-hidden="true" />
          {move.label}
        </span>
      )}
    </span>
  );
}

export default function NewsEvents({
  date,
  onStock,
}: {
  date: string;
  onStock?: (id: string) => void;
}) {
  const [tab, setTab] = useState<"news" | "calendar">("news");
  const [filter, setFilter] = useState<"featured" | "all">("featured");
  const [newsFilter, setNewsFilter] = useState<NewsFilterKey | "all">("all");
  const [importantOnly, setImportantOnly] = useState(false);
  const [selectedMessageKey, setSelectedMessageKey] = useState<string>();
  const [days, setDays] = useState<7 | 30>(30);
  const [eventId, setEventId] = useState<string>();
  const news = useApi<NewsView>(`/market/news-events?date=${date}`);
  const calendar = useApi<CalendarView>(
    tab === "calendar"
      ? `/market/news-events/calendar?date=${date}&days=${days}`
      : null,
  );
  const { timeline, featured } = useMemo(() => {
    const entries = new Map<string, TimelineEntry>();
    for (const { items } of news.data?.stages ?? []) {
      for (const item of items) {
        const key = JSON.stringify([item.source, item.id]);
        if (!entries.has(key)) {
          entries.set(key, {
            key,
            item,
            category: classifyNews(item),
            timestamp: Date.parse(item.published_at),
            time: timeLabel(item.published_at, date),
          });
        }
      }
    }
    const timeline = [...entries.values()].sort(
      (left, right) => right.timestamp - left.timestamp,
    );
    return { timeline, featured: selectFeaturedNews(timeline) };
  }, [news.data, date]);
  const { allInScope, featuredInScope } = useMemo(() => {
    if (newsFilter === "all" && !importantOnly) {
      return { allInScope: timeline, featuredInScope: featured };
    }
    const inScope = ({ category }: TimelineEntry) =>
      (newsFilter === "all" || category.filters.includes(newsFilter)) &&
      (!importantOnly || Boolean(category.importance));
    return {
      allInScope: timeline.filter(inScope),
      featuredInScope: featured.filter(inScope),
    };
  }, [timeline, featured, newsFilter, importantOnly]);
  const visibleMessages = filter === "featured" ? featuredInScope : allInScope;
  const canShowAllInScope = filter === "featured" && allInScope.length > 0;
  const selection =
    visibleMessages.find(({ key }) => key === selectedMessageKey) ||
    visibleMessages[0];
  const message = selection?.item;
  const partial = news.data?.stages.some(
    ({ status }) => status === "partial" || status === "unavailable",
  );
  const expired = news.data?.stages.some(({ status }) => status === "expired");
  const hasCoverage = news.data?.stages.some(
    ({ status }) => status === "complete",
  );
  const events = calendar.data?.events ?? [];
  const event = eventId ? events.find(({ id }) => id === eventId) : events[0];
  const eventImportance = event && calendarImportance(event);
  const relatedMessage =
    event && timeline.find(({ item }) => item.event_id === event.id);
  const activeRead = tab === "news" ? news : calendar;

  return (
    <div className="news-events">
      <header className="news-events-toolbar">
        <div className="segmented" role="group" aria-label="消息与事件视图">
          <button
            type="button"
            className={tab === "news" ? "active" : ""}
            aria-pressed={tab === "news"}
            onClick={() => setTab("news")}
          >
            消息时间轴
          </button>
          <button
            type="button"
            className={tab === "calendar" ? "active" : ""}
            aria-pressed={tab === "calendar"}
            onClick={() => setTab("calendar")}
          >
            事件日历
          </button>
        </div>
        <div className="news-events-context">
          <CalendarDays size={15} aria-hidden="true" />
          <time dateTime={date}>{date}</time>
          <span>北京时间</span>
          <button
            type="button"
            className="btn small"
            onClick={activeRead.reload}
            disabled={activeRead.loading}
          >
            <RefreshCw size={13} aria-hidden="true" /> 重新读取
          </button>
          {tab === "news" && !news.error && timeline.length > 0 && (
            <AIEntry kind="market" asOf={date} newsContext={{ kind: "day" }} />
          )}
        </div>
      </header>
      {tab === "news" ? (
        <>
          {news.error ? (
            <ErrorBox message={news.error} retry={news.reload} />
          ) : news.loading && !news.data ? (
            <Loading />
          ) : news.data ? (
            <div className="news-events-workspace">
              <Panel
                title="已收录消息"
                subtitle="按原文发布时间倒序"
                actions={
                  timeline.length > 0 ? (
                    <div
                      className="segmented news-events-filter"
                      role="group"
                      aria-label="消息筛选"
                    >
                      {(
                        [
                          ["featured", "精选", featuredInScope.length],
                          ["all", "全部", allInScope.length],
                        ] as const
                      ).map(([mode, label, count]) => (
                        <button
                          key={mode}
                          type="button"
                          className={filter === mode ? "active" : ""}
                          aria-pressed={filter === mode}
                          onClick={() => setFilter(mode)}
                        >
                          {label} <span>{count}</span>
                        </button>
                      ))}
                    </div>
                  ) : undefined
                }
              >
                {timeline.length > 0 && (
                  <>
                    <div className="news-events-category-toolbar">
                      <div
                        className="news-events-category-filters"
                        role="group"
                        aria-label="消息分类"
                      >
                        {(
                          [
                            { key: "all", label: "所有分类" },
                            ...newsFilters,
                          ] as const
                        ).map(({ key, label }) => (
                          <button
                            key={key}
                            type="button"
                            className="btn small"
                            aria-pressed={newsFilter === key}
                            onClick={() => setNewsFilter(key)}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                      <button
                        className="btn small news-events-importance-toggle"
                        type="button"
                        aria-pressed={importantOnly}
                        onClick={() => setImportantOnly((current) => !current)}
                      >
                        <Star size={13} aria-hidden="true" />
                        只看重要
                      </button>
                    </div>
                    {filter === "featured" &&
                      featuredInScope.length < allInScope.length && (
                        <p className="news-events-filter-note">
                          优先展示政策、公告和明显异动，完整时间轴见「全部」。
                        </p>
                      )}
                    {partial && (
                      <p className="news-events-reason">
                        部分来源或时段待核验。
                      </p>
                    )}
                  </>
                )}
                {visibleMessages.length > 0 ? (
                  <ul className="news-events-list news-events-timeline">
                    {visibleMessages.map(({ key, item, category, time }) => (
                      <li
                        key={key}
                        data-news-tone={category.tone}
                        data-news-move={category.move?.direction}
                        data-news-important={
                          category.importance ? "true" : undefined
                        }
                      >
                        <button
                          type="button"
                          aria-pressed={key === selection?.key}
                          onClick={() => setSelectedMessageKey(key)}
                        >
                          <span className="news-events-row news-events-meta">
                            <span className="news-events-secondary">
                              <time dateTime={item.published_at}>{time}</time>
                              {" · "}
                              {item.source}
                            </span>
                            <NewsLabels category={category} />
                          </span>
                          <strong>{item.title}</strong>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : timeline.length > 0 ? (
                  <Empty
                    title={
                      importantOnly
                        ? "当前筛选下暂无重要消息"
                        : filter === "featured"
                          ? "暂无精选消息"
                          : "此分类暂无消息"
                    }
                    description={`该日已收录 ${timeline.length} 条消息。`}
                    action={
                      <button
                        className="btn small"
                        type="button"
                        onClick={() =>
                          canShowAllInScope
                            ? setFilter("all")
                            : importantOnly
                              ? setImportantOnly(false)
                              : setNewsFilter("all")
                        }
                      >
                        {canShowAllInScope
                          ? newsFilter === "all"
                            ? "查看全部消息"
                            : "在全部中查看本类"
                          : importantOnly
                            ? "取消只看重要"
                            : "查看所有分类"}
                      </button>
                    }
                  />
                ) : (
                  <Empty
                    title={expired ? "已过留存期" : "暂无已收录消息"}
                    description={
                      expired
                        ? `消息保留最近 ${news.data.retention_days} 个自然日。`
                        : partial
                          ? "部分来源或时段待核验。"
                          : hasCoverage
                            ? "已核验的收录区间内暂无新增。"
                            : "所选日期暂无消息归档。"
                    }
                  />
                )}
              </Panel>
              {selection && message && (
                <Panel
                  className="news-events-timeline-detail"
                  title="消息详情"
                  subtitle={`${message.source} · 原文发布 ${selection.time}`}
                  actions={
                    <>
                      <SourceLink source={message} />
                      <AIEntry
                        kind="market"
                        asOf={date}
                        newsContext={{
                          kind: "news",
                          source: message.source,
                          id: message.id,
                        }}
                      />
                    </>
                  }
                >
                  <div className="news-events-detail" aria-live="polite">
                    <div>
                      <div
                        className="news-events-row news-events-detail-category"
                        data-news-tone={selection.category.tone}
                        data-news-move={selection.category.move?.direction}
                      >
                        <NewsLabels category={selection.category} />
                        {selection.category.move && (
                          <span className="news-events-secondary">
                            原文行情
                          </span>
                        )}
                      </div>
                      <h3>{message.title}</h3>
                      {selection.category.importance && (
                        <p className="news-events-importance-reason">
                          <strong>标记依据</strong>
                          {selection.category.importance.reason}
                        </p>
                      )}
                      {message.summary && <p>{message.summary}</p>}
                      {message.first_seen_at && (
                        <p className="news-events-secondary">
                          首次收录 {timeLabel(message.first_seen_at, date)}
                        </p>
                      )}
                    </div>
                    {!!message.topics?.length && (
                      <section>
                        <h3>来源标签</h3>
                        <TopicEvidence topics={message.topics} sourceTags />
                      </section>
                    )}
                    {!!message.symbols?.length && (
                      <section>
                        <h3>关联个股</h3>
                        <ul className="news-events-evidence">
                          {message.symbols.map(({ symbol, name, basis }) => (
                            <li key={`${symbol}:${basis}`}>
                              {onStock ? (
                                <button
                                  className="btn small"
                                  type="button"
                                  onClick={() => onStock(symbol)}
                                >
                                  {name ? `${name} ${symbol}` : symbol}
                                </button>
                              ) : (
                                <Badge>
                                  {name ? `${name} ${symbol}` : symbol}
                                </Badge>
                              )}
                              <span>{basis}</span>
                            </li>
                          ))}
                        </ul>
                      </section>
                    )}
                    {message.event_id && (
                      <section>
                        <button
                          type="button"
                          className="btn small"
                          onClick={() => {
                            setEventId(message.event_id || undefined);
                            setDays(30);
                            setTab("calendar");
                          }}
                        >
                          查看相关日程
                        </button>
                      </section>
                    )}
                  </div>
                </Panel>
              )}
            </div>
          ) : null}
        </>
      ) : (
        <>
          <div className="news-events-toolbar">
            <span className="news-events-secondary">
              从所选日期起往后看（含当天），按事件日期排序。
            </span>
            <div className="segmented" role="group" aria-label="事件日历范围">
              {([30, 7] as const).map((count) => (
                <button
                  key={count}
                  type="button"
                  className={days === count ? "active" : ""}
                  aria-pressed={days === count}
                  onClick={() => {
                    setDays(count);
                    setEventId(undefined);
                  }}
                >
                  未来 {count} 天
                </button>
              ))}
            </div>
          </div>
          {calendar.error ? (
            <ErrorBox message={calendar.error} retry={calendar.reload} />
          ) : calendar.loading && !calendar.data ? (
            <Loading />
          ) : (
            calendar.data && (
              <div className="news-events-workspace">
                <Panel
                  title="待关注日程"
                  subtitle={`${date} 至 ${calendar.data.end_date}`}
                  actions={
                    calendar.data.status === "available" ? (
                      <Badge>{events.length} 项</Badge>
                    ) : undefined
                  }
                >
                  {events.length ? (
                    <>
                      {calendar.data.reason && (
                        <p className="news-events-reason">
                          {calendar.data.reason}
                        </p>
                      )}
                      <ul className="news-events-list">
                        {events.map((item) => {
                          const importance = calendarImportance(item);
                          return (
                            <li
                              key={item.id}
                              data-news-important={
                                importance ? "true" : undefined
                              }
                            >
                              <button
                                type="button"
                                aria-pressed={item.id === event?.id}
                                onClick={() => setEventId(item.id)}
                              >
                                <span className="news-events-row">
                                  <time dateTime={item.event_date}>
                                    {item.event_date}
                                  </time>
                                  {importance && (
                                    <ImportanceBadge label={importance.label} />
                                  )}
                                  <Badge tone={eventStatuses[item.status].tone}>
                                    {eventStatuses[item.status].label}
                                  </Badge>
                                </span>
                                <strong>{item.title}</strong>
                                <span className="news-events-secondary">
                                  {item.source}
                                </span>
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </>
                  ) : (
                    <Empty
                      title={
                        calendar.data.status === "available"
                          ? "所选范围暂无已收录事件"
                          : "事件日历暂不可用"
                      }
                      description={
                        calendar.data.reason ||
                        (calendar.data.status === "available"
                          ? "试试扩大查看范围。"
                          : "当前暂无已核验的日历材料。")
                      }
                    />
                  )}
                </Panel>
                {event ? (
                  <Panel
                    title="事件详情"
                    subtitle={`${event.event_date} · ${eventStatuses[event.status].label} · ${event.source}`}
                    actions={
                      <>
                        <SourceLink source={event} />
                        <AIEntry
                          kind="market"
                          asOf={date}
                          newsContext={{
                            kind: "event",
                            source: event.source,
                            id: event.id,
                            days,
                          }}
                        />
                      </>
                    }
                  >
                    <div className="news-events-detail" aria-live="polite">
                      <div>
                        {eventImportance && (
                          <div className="news-events-detail-category">
                            <ImportanceBadge label={eventImportance.label} />
                          </div>
                        )}
                        <h3>{event.title}</h3>
                        {eventImportance && (
                          <p className="news-events-importance-reason">
                            <strong>标记依据</strong>
                            {eventImportance.reason}
                          </p>
                        )}
                        {event.summary && <p>{event.summary}</p>}
                        {(event.published_at || event.published_date) && (
                          <p className="news-events-secondary">
                            原文发布{" "}
                            {event.published_at
                              ? timeLabel(event.published_at, date)
                              : event.published_date}
                          </p>
                        )}
                      </div>
                      <section>
                        <h3>相关题材</h3>
                        <TopicEvidence topics={event.topics} />
                      </section>
                      {relatedMessage && (
                        <section>
                          <button
                            className="btn small"
                            type="button"
                            onClick={() => {
                              if (!featured.includes(relatedMessage)) {
                                setFilter("all");
                              }
                              if (!relatedMessage.category.importance) {
                                setImportantOnly(false);
                              }
                              if (
                                newsFilter !== "all" &&
                                !relatedMessage.category.filters.includes(
                                  newsFilter,
                                )
                              ) {
                                setNewsFilter("all");
                              }
                              setSelectedMessageKey(relatedMessage.key);
                              setTab("news");
                            }}
                          >
                            查看相关消息
                          </button>
                        </section>
                      )}
                    </div>
                  </Panel>
                ) : (
                  eventId && (
                    <Panel title="事件详情">
                      <Empty
                        title="当前范围未找到关联事件"
                        description="可从列表查看当前留存的其他日程。"
                      />
                    </Panel>
                  )
                )}
              </div>
            )
          )}
        </>
      )}
    </div>
  );
}
