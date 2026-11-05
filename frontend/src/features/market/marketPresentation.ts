import type { Row } from "../../lib/api";

export type MarketSort = { key: string; order: "asc" | "desc" };

const finiteCount = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
const finiteMetric = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

/** A board's own dated quote and its historical membership are independent. */
export function sectorQuoteSummary(
  data: Row | null | undefined,
  kind: string,
  level = "all",
) {
  const loaded = Array.isArray(data?.items);
  const kindRows: Row[] = loaded
    ? data!.items.filter(
        (row: Row) =>
          (row.kind === "industry" || row.category === "行业"
            ? "industry"
            : "concept") === kind,
      )
    : [];
  const rows = kindRows.filter(
    (row) =>
      level === "all" ||
      String(row.classification_level ?? row.level) === level,
  );
  const catalogCount =
    level === "all"
      ? (finiteCount(
          data?.summary?.[
            kind === "industry" ? "industry_count" : "concept_count"
          ],
        ) ?? (loaded ? rows.length : null))
      : loaded
        ? rows.length
        : null;
  const explicitQuotes =
    rows.length > 0 &&
    rows.every((row) => typeof row.quote_available === "boolean");
  const quotedCount =
    (level === "all"
      ? finiteCount(
          data?.summary?.[
            kind === "industry" ? "quoted_industries" : "quoted_concepts"
          ],
        )
      : null) ??
    (loaded && explicitQuotes
      ? rows.filter((row) => row.quote_available === true).length
      : loaded && catalogCount === 0
        ? 0
        : null);
  const explicitMembers =
    rows.length > 0 &&
    rows.every((row) => typeof row.members_available === "boolean");
  const membersAvailable =
    loaded && explicitMembers
      ? rows.filter((row) => row.members_available === true).length
      : loaded &&
          (catalogCount === 0 ||
            data?.membership_coverage?.selected_date_available === false)
        ? 0
        : null;
  const membershipNotice =
    membersAvailable === 0 && catalogCount !== 0
      ? "该日历史成分未留存；板块自身日行情可独立展示，成员数量与涨停家数保持未知。"
      : membersAvailable !== null && membersAvailable < rows.length
        ? "部分分类没有该日历史成分；仅展示已留存成员，不用当前成分回填过去。"
        : "历史成分与板块行情分别留存；行情可用不代表历史成分完整。";
  return {
    catalogCount,
    quotedCount,
    membersAvailable,
    membershipNotice,
    rows,
    up: loaded
      ? rows.filter((row) => finiteMetric(row.change_pct) && row.change_pct > 0)
          .length
      : null,
    down: loaded
      ? rows.filter((row) => finiteMetric(row.change_pct) && row.change_pct < 0)
          .length
      : null,
    unknown: loaded
      ? rows.filter((row) => !finiteMetric(row.change_pct)).length
      : null,
    catalogDate:
      [
        ...new Set(
          kindRows
            .map((row) => row.catalog_observed_date)
            .filter((value) => typeof value === "string" && value),
        ),
      ]
        .sort()
        .join("、") || null,
    sourceIndexCount:
      loaded && explicitQuotes
        ? rows.filter(
            (row) =>
              row.quote_available === true &&
              row.quote_basis === "provider_index",
          ).length
        : null,
  };
}

/** Keep industry funds visible while a separate concept quote list is selected. */
export function industryFundsSummary(data: Row | null | undefined) {
  const rows: Row[] = Array.isArray(data?.items) ? data.items : [];
  const loaded = Array.isArray(data?.items);
  return {
    available: data?.summary?.net_flow_available === true,
    observed:
      finiteCount(data?.coverage?.observed_count) ??
      (loaded
        ? rows.filter((row) => finiteMetric(row.net_inflow)).length
        : null),
    total:
      finiteCount(data?.coverage?.selected_count) ??
      finiteCount(data?.coverage?.directory_count),
    days:
      finiteCount(data?.summary?.history_observed_days) ??
      (Array.isArray(data?.history)
        ? new Set(data!.history.map((row: Row) => row.date).filter(Boolean))
            .size
        : null),
  };
}

/** A short reading of the returned phase; raw thresholds stay in its details. */
export function emotionStageSummary(phase: string): string {
  return (
    (
      {
        冰点: "上涨股票较少，市场广度处于低位。",
        高潮: "市场广度较强，来源事件满足高潮观察条件。",
        修复: "市场广度从前日低位回升。",
        退潮: "市场广度从前日高位回落。",
        普涨: "上涨股票较多，市场广度较强。",
        分化: "涨跌表现分化，尚未出现更鲜明的阶段特征。",
        待核验: "可比资料不足，暂不判断盘面阶段。",
      } as Record<string, string>
    )[phase] || "当前阶段以已留存数据为依据，详细口径可展开查看。"
  );
}

/** Exact CNY difference only; never reconstruct it from a rounded percentage. */
export function amountChangeText(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value))
    return "较昨日增减待核验";
  const amount = (Math.abs(value) / 1e8).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return value === 0
    ? "较昨日持平 · 0.00亿元"
    : `较昨日${value > 0 ? "放量" : "缩量"} ${amount}亿元`;
}

/** Filter the full returned membership set, before ordering or pagination. */
export function filterMarketMembers(rows: Row[], query: string, board: string) {
  const search = query.trim().toLowerCase();
  const known = ["main", "chinext", "star", "beijing"];
  return rows.filter(
    (row) =>
      (board === "all" ||
        (board === "unknown"
          ? !known.includes(row.board)
          : row.board === board)) &&
      (!search ||
        `${row.name || ""} ${row.code || ""} ${row.instrument_id || ""}`
          .toLowerCase()
          .includes(search)),
  );
}

/** Dated membership lookup, with an explicit cross-provider proxy request. */
export function topicMembersRequest(topic: Row, date: string): string | null {
  if (!date) return null;
  const parameters = new URLSearchParams({ date });
  if (topic.member_lookup && typeof topic.member_lookup === "object") {
    for (const field of ["topic_id", "source_code", "source_name", "kind"]) {
      const value = topic.member_lookup[field];
      if (typeof value === "string" && value) parameters.set(field, value);
    }
    if (parameters.has("topic_id") || parameters.has("source_code"))
      return "/market/topic-members?" + parameters;
  }
  const code = topic.topic_code || topic.source_code || topic.topic_id;
  if (typeof code === "string" && /^BK\d+$/i.test(code)) {
    parameters.set("source_code", code);
    parameters.set("source_name", topic.name || "");
    parameters.set("kind", topic.kind || "industry");
  } else if (typeof topic.topic_id === "string" && topic.topic_id)
    parameters.set("topic_id", topic.topic_id);
  else return null;
  return "/market/topic-members?" + parameters;
}

/** Coverage belongs to this API page; never borrow the global quote window. */
export function describeDataCoverage(input: unknown) {
  const data =
    input && typeof input === "object" && !Array.isArray(input)
      ? (input as Row)
      : null;
  const text = (value: unknown) =>
    typeof value === "string" && value.trim() ? value.trim() : null;
  const date = (value: unknown) => {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
      return null;
    const parsed = new Date(value + "T00:00:00Z");
    return Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
      ? value
      : null;
  };
  const start = date(data?.start),
    end = date(data?.end);
  const known =
    !!data &&
    [data.label, data.basis, data.notice, start, end].some(
      (value) => text(value) !== null,
    );
  const inconsistent = !!start && !!end && start > end;
  const range = !known
    ? "覆盖信息待核验"
    : inconsistent
      ? "覆盖边界不一致，待核验"
      : start && end
        ? start === end
          ? "已留存 " + start
          : "已留存 " + start + " → " + end
        : start
          ? "起点 " + start + " · 截止待核验"
          : end
            ? "起点待核验 · 截至 " + end
            : "暂无已确认留存日期";
  const dates = Array.isArray(data?.available_dates)
    ? [
        ...new Set(
          data.available_dates
            .map(date)
            .filter((value): value is string => value !== null),
        ),
      ].sort()
    : [];
  return {
    known,
    label: text(data?.label) || "本页数据",
    range,
    basis: text(data?.basis),
    notice:
      text(data?.notice) ||
      (!known
        ? "本页尚未返回可核验的覆盖范围，不采用全局行情年限代替。"
        : null),
    selectedAvailable:
      typeof data?.selected_date_available === "boolean"
        ? data.selected_date_available
        : null,
    partial: data?.partial === true || inconsistent,
    dates,
  };
}

const numericFields = new Set([
  "rank",
  "value",
  "close",
  "change_pct",
  "auction_pct",
  "auction_amount",
  "change_30d_pct",
  "return_ytd_pct",
  "return_mtd_pct",
  "open_change_pct",
  "change_pp",
  "amount_delta",
  "limit_up_1y",
  "sessions_since_limit_up",
  "amount",
  "net_inflow",
  "net_inflow_pct",
  "turnover_rate",
  "volume_ratio",
  "board_count",
  "limit_up_ytd",
  "limit_up_count",
  "observed_count",
  "member_count",
  "break_count",
  "open_times",
  "seal_money",
  "seal_cv",
  "snapshot_count",
  "previous_board_count",
  "limit_down_count",
  "up_count",
  "up_ratio",
  "down_count",
  "broken_count",
  "_sector_activity_count",
  "_sector_up_ratio",
  "_sector_limit_sort",
  "_sector_broken_sort",
  "_sector_broken_rate_sort",
  "_sector_touch_sort",
  "_sector_broken_up_sort",
  "drawdown_from_high_pct",
]);

function textForSort(value: unknown): string | null {
  if (value == null || value === "") return null;
  if (Array.isArray(value)) {
    const labels = value
      .map((item) => textForSort(item))
      .filter((item): item is string => item != null)
      .sort();
    return labels.length ? labels.join("、") : null;
  }
  if (typeof value === "object") {
    const item = value as Row;
    return textForSort(
      item.name ?? item.label ?? item.topic_name ?? item.topic_id,
    );
  }
  return String(value);
}

/** Compare original values before pagination; unknowns stay last both ways. */
export function sortMarketRows(rows: Row[], sort: MarketSort | null): Row[] {
  if (!sort) return rows;
  const numeric = numericFields.has(sort.key);
  const valueFor = (row: Row) => {
    const value = row[sort.key];
    return numeric
      ? typeof value === "number" && Number.isFinite(value)
        ? value
        : null
      : textForSort(value);
  };
  const collator = new Intl.Collator("zh-CN", {
    numeric: true,
    sensitivity: "base",
  });
  return rows
    .map((row, index) => ({ row, index, value: valueFor(row) }))
    .sort((left, right) => {
      if (left.value == null)
        return right.value == null ? left.index - right.index : 1;
      if (right.value == null) return -1;
      const compared = numeric
        ? Number(left.value) - Number(right.value)
        : collator.compare(String(left.value), String(right.value));
      return (
        compared * (sort.order === "asc" ? 1 : -1) || left.index - right.index
      );
    })
    .map((item) => item.row);
}

/** Input must already be normalized CNY; this formats, never guesses source units. */
export function marketMoney(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  const [divisor, unit] =
    magnitude >= 1e12
      ? [1e12, "万亿元"]
      : magnitude >= 1e8
        ? [1e8, "亿元"]
        : magnitude >= 1e4
          ? [1e4, "万元"]
          : [1, "元"];
  return (
    (value / Number(divisor)).toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }) + unit
  );
}

export const marketLabels: Record<string, string> = {
  name: "名称",
  topics: "题材",
  board_count: "连板高度",
  first_limit_time: "首次封板",
  last_limit_time: "最后封板",
  break_count: "开板次数",
  observed_count: "已观察成员",
  member_count: "成员总数",
  quote_date: "行情日期",
  quoted_concepts: "已报价概念",
  quoted_industries: "已报价行业",
  index_quote_count: "来源板块行情",
  calculation: "计算口径",
  membership_observed_date: "成员采集日",
  event_type: "异动标签",
  start_date: "开始日期",
  end_date: "结束日期",
  reason: "依据说明",
  source: "来源",
  technical_observation: "技术观察",
  risk_labels: "监管风险标签",
  cycle: "盘面阶段（代理）",
  breadth_state: "上涨家数分层",
  comparable_count: "可比样本家数",
  comparable_coverage_pct: "样本可比率 (%)",
  temperature_delta: "温度较前日变化",
  core_role: "人工核心标记",
  seal_money: "封单金额",
  open_times: "开板次数",
  unknown_count: "区间收益待核验",
  universe_count: "证券范围",
  with_intermediate_gaps: "区间内缺报价",
  history_observed_days: "已留存资金交易日",
  basis: "观察口径",
  seal_cv: "封单变异系数",
  snapshot_count: "快照样本数",
  net_inflow_pct: "净流入占比",
  board: "上市板块",
  return_ytd_pct: "年内涨幅",
  return_mtd_pct: "本月涨幅",
  last_limit_up_date: "最近涨停日期",
  value: "数值",
  description: "说明",
};

export const marketExplanations: Record<string, string> = {
  quoted_concepts:
    "已留存所选交易日自身行情的当前目录概念数量；来源板块指数不依赖历史成员档案，不证明当前目录就是历史完整概念目录。",
  quoted_industries:
    "已留存所选交易日自身行情的当前目录行业数量；行业包含不同层级时按所选层级观察，成员缺失不会被解释为板块行情不存在。",
  quote_date:
    "该行实际板块行情的交易日；来源指数行情与成员样本计算分开标明，不用当前快照冒充历史日行情。",
  board:
    "按已识别证券代码与交易所分类用于本页筛选；主板、创业板、科创板和北交所分别展示，未知不归主板。此分类不证明历史身份或历史成员归属。",
  return_ytd_pct:
    "截至所选盘面日，使用上一年末可核验收盘基准计算；有公司行动时仅使用已核验连续价格口径。缺失基准保持未知，不用年初第一条报价替代。",
  return_mtd_pct:
    "本月以来(MTD)，截至所选盘面日相对上一月末可核验基准计算；与近30个交易日涨幅不同。缺基准、报价或公司行动证据时保持未知。",
  last_limit_up_date:
    "截至所选盘面日的最近已核验收盘涨停事件。距所选日按交易日历计算；事件后存在覆盖缺口时标最近已确认，不断言一定是最近一次。",
  temperature:
    "范围0—100。计算为上涨家数 ÷（上涨＋下跌＋平盘的已留存可比样本家数）×100。无法确认涨跌的未知样本不进入分母，缺报价另列提示。这是市场广度的描述性读数，不是对后续涨跌的预测。",
  cycle:
    "按自定义规则依次判断待核验、冰点、高潮、修复、退潮、普涨、分化。冰点需温度≤15且上涨<500；高潮另需完整来源池涨停≥80、跌停≤5；修复/退潮需同口径前一交易日的15个百分点变化。完整规则见下方说明。它是盘面代理，不能当作完整心理情绪周期或买卖信号。",
  breadth_state:
    "仅按上涨家数分层：≥3000为普涨，<500为普跌，其余分化；与包含相邻日变化的盘面阶段分别展示。",
  comparable_count:
    "能相对前一市场交易日比较收盘价的沪深样本家数。未知、公司行动不可比与缺报价不补作平盘。",
  comparable_coverage_pct:
    "可比家数/应有范围估算；应有范围因上市日期未知而不可确认时，以已观察样本为分母。低于80%不判阶段。这不是全市场无缺口覆盖率。",
  temperature_delta:
    "当前温度减前一完整市场交易日温度，单位为百分点；只有同口径且可比率足够时才计算。没有前日不使用更早日期代替。",
  breadth:
    "只统计所选盘面日期、已留存且能与前一交易日价格比较的A股样本；不等同全市场无缺口覆盖。未知涨跌和缺报价单列，停牌未核实的部分不擅自归类。",
  reference_lines:
    "500家和3000家是当前约定的市场广度观察参考线，不是随股票总数自动调整的比例，也不是交易规则。",
  up: "所选交易日相对前一交易日价格可比且收盘上涨的已留存股票家数；不可比或缺报价的股票不计入。",
  down: "所选交易日相对前一交易日价格可比且收盘下跌的已留存股票家数；不可比或缺报价的股票不计入。",
  flat: "所选交易日相对前一交易日价格可比且收盘持平的已留存股票家数。未知状态不补作平盘。",
  unknown:
    "已留存报价但无法确认当日涨跌状态的样本。公司行动、前收盘缺失或数据可比性不足都可能导致未知，未知不能当作平盘或停牌。",
  covered_stocks:
    "可以按精确T与T-30市场交易日端点计算区间涨幅的证券数量。区间中间有缺报价时仍保留已核验端点收益，并另列缺口；不能把它理解为全年全部行情完整。",
  unknown_count:
    "T或T-30端点缺失、公司行动未核验等原因导致区间收益未知的证券数。这些股票仍可搜索，不被静默移出证券范围。",
  universe_count:
    "当前返回证券范围中的股票数量，包括区间收益待核验的股票；不等同每只股票都有完整历史报价。",
  with_intermediate_gaps:
    "端点可核验但区间中间缺少一个或多个市场交易日报价的股票数。可能是停牌或缺数，当前不擅自认定原因。",
  concept_count:
    "当前供应商概念目录的分类数量；目录已导入不等于每个概念的成员档案都已同步完整。",
  industry_count:
    "当前供应商行业目录的分类数量，可能包含不同层级；不能把各层级混合后的数量当作一级行业数。",
  members_synced:
    "当前已留存成员数据的分类组数；每组的完整性仍需单独核验，不等同全市场题材历史完整。",
  history_observed_days:
    "观察日及其之前已留存行业资金记录所覆盖的不同交易日数。跨交易日历史不是全天盘中资金轮动时序。",
  change_30d_pct:
    "以观察日T和T-30的精确市场交易日收盘端点计算涨幅。区间有已核验公司行动时采用本地事件前复权价格；中间缺报价另列提示。未知端点不使用最近31条报价替代。",
  limit_up_ytd:
    "按年初至观察日逐日已核验来源涨停池计数。完整覆盖时显示精确次数；未完整时只展示已确认下界。排序只比较精确次数，下界与未知项均置于末尾。",
  change_pct:
    "所选盘面日的真实涨跌幅。未知或价格不可比时保持缺失；不会把空值改成0%。",
  turnover_rate:
    "换手率按数据源已核验字段展示，单位为百分比。它与以人民币计量的成交额不是同一个字段；缺少流通股本或可靠字段时保持未知。",
  seal_money:
    "人民币封单金额，基础数值单位为元；单元格按元、万元、亿元或万亿元展示，并可查看原始金额。这是来源收盘池的单次快照，不能据此证明封单全天稳定。",
  seal_cv:
    "同一明确采样窗口内，封单金额标准差与均值的比值，用于描述波动。需要连续快照、采样间隔及覆盖率；当前没有连续序列时不显示伪造排名。",
  net_inflow:
    "供应商按成交单大小等规则分类计算的资金净流入，单位人民币元。它不能直接等同机构账户净买入、真实持仓变化或新增资金；红为正、绿为负。",
  net_inflow_pct:
    "供应商资金分类口径下的主力净流入占比，单位为百分比；不是股票涨跌幅，也不代表机构持仓比例。",
  amount:
    "人民币成交额，基础数值单位为元，页面自动显示万元、亿元或万亿元。成交额衡量成交活跃度，不能直接推断净流入或机构进攻方向。",
  core_role:
    "龙头或核心地位需要明确研究依据和人工评级。未标注表示当前没有可核验评级，不能由连板高度或成交额自动认定。",
  volume_ratio:
    "当日成交量与前一可比交易日成交量之比。不是同花顺盘中量比；前日缺报价或存在未处理公司行动时保持未知。",
  first_limit_time:
    "来源当日首次封住涨停的时间；缺失时保持未知，不使用最后封板时间替代。时间先后不等于龙头地位。",
  last_limit_time:
    "来源当日最后一次封住涨停的时间；与首次封板和开板次数结合观察。没有连续盘口时不据此计算封单稳定性。",
  break_count:
    "来源记录的当日开板次数；接口没有返回时保留未知，未知不视为0次。",
  board_count:
    "来源当日连续涨停高度；不是年内累计次数，也不能单独判断板块龙头。",
  limit_up_count:
    "当前来源池或已观察成员中的涨停数量，受来源和成员覆盖限制；成员不完整时不能当作全题材完整数。",
  observed_count:
    "当前已返回并能参与该行计算的样本数量；需要与成员总数和计算口径一起看。",
  member_count:
    "来源成员目录的范围；不能由它推断每个成员都有当日报价，也不代表历史成分已留存。",
  calculation:
    "说明该行使用来源板块指数、当前成员等权样本或分类资金数据；不同口径不能当成同一个指数比较。",
  membership_observed_date:
    "成员采集日期；当前成员只能作为当前研究范围，不能冒充过去交易日的真实题材归属。",
  event_type:
    "供应商异动或监管监控标签；未附交易所原文时，不等同正式监管函或处罚。",
  source:
    "具体来源与口径；已留存数据不代表实时数据，公开补充也不自动保证全量覆盖。",
  technical_observation:
    "基于已留存报价与可比指标的描述，包括量能和金叉；KDJ金叉指K线上穿D线，缺预热或不可比时保持未知。",
  risk_labels:
    "已留存来源监控标签，仅限明确采集日期；没有标签不证明没有监管风险。",
};
