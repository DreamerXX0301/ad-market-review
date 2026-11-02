/** Local GET API/React audit for an isolated data copy. Reading a research report
 * renews its cache TTL and retention pin; this is not a no-write business audit.
 * Never configures or verifies source credentials or creates user tasks. */
import fs from "node:fs/promises";
const base = process.env.SHIXU_API_BASE;
if (!base)
  throw new Error(
    "请用 SHIXU_API_BASE 显式指定隔离验收实例的 /api/v1 地址；完整审计包含会更新研究报告缓存的 GET，不默认访问正式服务。",
  );
const checks = [];
function check(name, condition, detail = "") {
  checks.push({ name, passed: !!condition, ...(detail ? { detail } : {}) });
}
async function get(path) {
  const r = await fetch(base + path, { signal: AbortSignal.timeout(60000) });
  check("HTTP " + path, r.ok, "status " + r.status);
  if (!r.ok) throw new Error(path + " returned " + r.status);
  return r.json();
}
const numeric = (v) => typeof v === "number" && Number.isFinite(v),
  nullable = (v) => v === null || numeric(v),
  isDate = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);
function page(name, d) {
  check(
    name + " list envelope",
    Array.isArray(d.items) && numeric(d.total) && d.total >= d.items.length,
  );
}
function coverage(name, d) {
  const c = d.data_coverage;
  check(
    name + " explicit module coverage",
    c &&
      typeof c.label === "string" &&
      typeof c.basis === "string" &&
      typeof c.notice === "string" &&
      (c.start === null || isDate(c.start)) &&
      (c.end === null || isDate(c.end)),
  );
  if (Array.isArray(c?.available_dates))
    check(
      name + " sparse covered dates are explicit",
      c.available_dates.every(isDate) &&
        new Set(c.available_dates).size === c.available_dates.length,
    );
}
function validLineAlert(event, asOf) {
  return (
    event &&
    typeof event.event_id === "string" &&
    typeof event.instrument_id === "string" &&
    typeof event.annotation_id === "string" &&
    Number.isSafeInteger(event.annotation_revision) &&
    event.annotation_revision >= 1 &&
    ["support", "resistance"].includes(event.line_type) &&
    [
      "support_reclaim",
      "support_breakdown",
      "pressure_close_breakout",
      "pressure_touch",
    ].includes(event.rule) &&
    isDate(event.trade_date) &&
    event.trade_date <= asOf &&
    numeric(event.line_price) &&
    event.line_price > 0 &&
    numeric(event.comparison_line_price) &&
    event.comparison_line_price > 0 &&
    ["none", "forward_event_affine"].includes(event.price_basis) &&
    (event.anchor_date === null || isDate(event.anchor_date)) &&
    ["open", "high", "low", "close", "previous_close"].every((key) =>
      nullable(event[key]),
    ) &&
    ["triggered", "invalidated"].includes(event.state) &&
    typeof event.title === "string" &&
    typeof event.reason === "string" &&
    typeof event.added_to_watchlist === "boolean" &&
    [
      "added",
      "already_present",
      "disabled",
      "capacity_reached",
      "not_eligible",
      "missing_current_quote",
    ].includes(event.watchlist_add_state) &&
    numeric(event.created_at_ms) &&
    (event.rule !== "support_breakdown" ||
      (event.state === "invalidated" && event.added_to_watchlist === false))
  );
}
try {
  const boot = await get("/bootstrap");
  check(
    "bootstrap date and calendar",
    isDate(boot.data_date) &&
      boot.market_dates.includes(boot.data_date) &&
      new Set(boot.market_dates).size === boot.market_dates.length,
  );
  check(
    "data summary numeric",
    ["stock_count", "row_count", "storage_bytes"].every((k) =>
      numeric(boot.data_summary[k]),
    ),
  );
  const date = boot.data_date;
  const auctionAutomation = await get("/market/auction/automation");
  check("auction service automation has explicit authorization and revision", auctionAutomation.version === "auction-automation-v1" && Number.isSafeInteger(auctionAutomation.revision) && auctionAutomation.revision >= 0 && ["off", "once", "daily"].includes(auctionAutomation.mode) && typeof auctionAutomation.enabled === "boolean");
  check("auction service automation is independent of historical view", isDate(auctionAutomation.today) && auctionAutomation.timezone === "Asia/Shanghai" && auctionAutomation.start_time === "09:25:30" && auctionAutomation.end_time === "09:30:00" && typeof auctionAutomation.worker?.alive === "boolean");
  const auctionDates = await get("/market/auction/dates");
  check("auction retained dates and explicit today", Array.isArray(auctionDates.dates) && auctionDates.dates.every(isDate) && isDate(auctionDates.today));
  const auction = await get(`/market/auction?date=${auctionDates.today}`);
  check("auction local read date and manual window", auction.date === auctionDates.today && typeof auction.can_capture === "boolean" && typeof auction.capture_window?.allowed === "boolean" && auction.capture_window?.timezone === "Asia/Shanghai");
  check("auction snapshot identity and public evidence metadata", auction.snapshot === null || (auction.snapshot.date === auction.date && auction.snapshot.payload?.date === auction.date && auction.snapshot.payload?.version === "auction-snapshot-v1" && !Object.hasOwn(auction.snapshot.payload, "evidence")));
  const overview = await get("/market/overview?date=" + date);
  const watchlist = await get("/watchlist");
  page("personal watchlist", watchlist);
  check("watchlist current business day", watchlist.business_date === date);
  check(
    "watchlist per-stock latest line alert is explicit, historical and nullable",
    watchlist.items.every(
      (row) =>
        Object.hasOwn(row, "latest_line_alert") &&
        (row.latest_line_alert === null ||
          (validLineAlert(row.latest_line_alert, date) &&
            row.latest_line_alert.instrument_id === row.instrument_id)),
    ),
  );
  const lineAlerts = await get(
    `/watchlist/line-alerts?as_of=${date}&limit=100`,
  );
  page("line alerts", lineAlerts);
  check(
    "line alert observation day isolation",
    lineAlerts.business_date === date &&
      lineAlerts.items.every((event) => validLineAlert(event, date)),
  );
  check(
    "line alert configured rules and unknown evidence are explicit",
    Number.isSafeInteger(lineAlerts.rules_total) &&
      lineAlerts.rules_total >= 0 &&
      Array.isArray(lineAlerts.unknowns) &&
      lineAlerts.unknowns.every(
        (row) =>
          typeof row.instrument_id === "string" &&
          typeof row.annotation_id === "string" &&
          typeof row.reason === "string",
      ),
  );
  check(
    "line alert persisted check receipt is nullable",
    lineAlerts.latest_check === null ||
      (typeof lineAlerts.latest_check?.job_id === "string" &&
        numeric(lineAlerts.latest_check.checked_at_ms) &&
        ["checked_rules", "trigger_count", "unknown_count"].every(
          (key) =>
            Number.isSafeInteger(lineAlerts.latest_check[key]) &&
            lineAlerts.latest_check[key] >= 0,
        ) &&
        typeof lineAlerts.latest_check.market_snapshot_id === "string"),
  );
  check(
    "line alert receipt marks stale rules or published inputs explicitly",
    lineAlerts.latest_check === null ||
      (typeof lineAlerts.latest_check.stale === "boolean" &&
        typeof lineAlerts.latest_check.is_current === "boolean" &&
        lineAlerts.latest_check.stale !== lineAlerts.latest_check.is_current &&
        (!lineAlerts.latest_check.stale ||
          typeof lineAlerts.latest_check.stale_reason === "string")),
  );
  const annotationInstrument =
    watchlist.items[0]?.instrument_id || overview.top_amount[0]?.instrument_id;
  if (annotationInstrument) {
    const annotations = await get(
      `/instruments/${encodeURIComponent(annotationInstrument)}/annotations`,
    );
    page("chart annotations", annotations);
    check(
      "chart annotation instrument scope",
      annotations.instrument_id === annotationInstrument &&
        annotations.items.every(
          (line) => line.instrument_id === annotationInstrument,
        ),
    );
    const allowedModes = {
      reference: [],
      support: ["support_reclaim", "support_breakdown"],
      resistance: ["pressure_close_breakout", "pressure_touch"],
    };
    check(
      "chart annotations have explicit line types, reminder rules and immutable revisions",
      annotations.items.every(
        (line) =>
          Object.hasOwn(allowedModes, line.line_type) &&
          Array.isArray(line.alert_modes) &&
          new Set(line.alert_modes).size === line.alert_modes.length &&
          line.alert_modes.every((mode) =>
            allowedModes[line.line_type].includes(mode),
          ) &&
          typeof line.auto_watchlist === "boolean" &&
          Number.isSafeInteger(line.revision) &&
          line.revision >= 1 &&
          (line.line_type !== "reference" ||
            (line.alert_modes.length === 0 && line.auto_watchlist === false)),
      ),
    );
  }
  coverage("overview", overview);
  check("market overview date isolation", overview.date === date);
  check("four indices", overview.indices.length === 4);
  check(
    "Shanghai Shenzhen Beijing amount",
    numeric(overview.total_amount) && overview.total_amount > 0,
  );
  check(
    "Shanghai Shenzhen Beijing turnover change is signed CNY or explicit unknown",
    nullable(overview.amount_change) &&
      (overview.amount_previous_date === null ||
        isDate(overview.amount_previous_date)) &&
      (numeric(overview.amount_change) ||
        typeof overview.amount_change_reason === "string"),
  );
  check(
    "three-exchange amount reconciles and keeps Beijing separate",
    numeric(overview.bj_amount) &&
      numeric(overview.shsz_total_amount) &&
      Math.abs(
        overview.total_amount - overview.shsz_total_amount - overview.bj_amount,
      ) < 0.02 &&
      /沪深京/.test(overview.amount_scope),
  );
  if (overview.amount_previous_date && numeric(overview.amount_change)) {
    const previousOverview = await get(
      "/market/overview?date=" + overview.amount_previous_date,
    );
    check(
      "turnover currency change reconciles to adjacent day totals",
      numeric(previousOverview.total_amount) &&
        Math.abs(
          overview.amount_change -
            (overview.total_amount - previousOverview.total_amount),
        ) < 0.02,
    );
  }
  check(
    "breadth explicit coverage",
    numeric(overview.breadth.up) &&
      numeric(overview.breadth.down) &&
      typeof overview.breadth.scope === "string",
  );
  check(
    "market stock identifiers",
    overview.top_amount.every(
      (r) => typeof r.instrument_id === "string" && typeof r.name === "string",
    ),
  );
  for (const route of [
    "emotion",
    "sectors",
    "ladder",
    "gainers",
    "abnormal",
    "flow",
  ]) {
    const d = await get("/market/" + route + "?date=" + date + "&limit=1000");
    page(route, d);
    coverage(route, d);
    check(route + " scoped date", d.date === date);
    if (route === "sectors") {
      for (const kind of ["concept", "industry"]) {
        const topic = d.items.find(
          (item) => item.kind === kind && item.topic_id,
        );
        if (!topic) continue;
        const requestedMembers = new URLSearchParams({
          date,
          topic_id: topic.topic_id,
        });
        const members = await get("/market/topic-members?" + requestedMembers);
        const membersUnavailable = members.match_type === "unavailable";
        check(
          "dated " + kind + " members keep date and identity",
          members.date === date &&
            requestedMembers.get("topic_id") ===
              topic.member_lookup?.topic_id &&
            topic.member_lookup?.kind === kind &&
            (membersUnavailable
              ? members.resolved_topic_id === null && members.topic === null
              : members.match_type === "source_topic_id" &&
                members.resolved_topic_id === topic.topic_id &&
                members.topic?.topic_id === topic.topic_id &&
                members.topic?.kind === kind),
          JSON.stringify({
            requested_topic_id: requestedMembers.get("topic_id"),
            resolved_topic_id: members.resolved_topic_id,
            match_type: members.match_type,
          }),
        );
        page("dated " + kind + " members", members);
        coverage("dated " + kind + " members", members);
        if (membersUnavailable)
          check(
            "dated " + kind + " missing historical members remain unavailable",
            topic.members_available === false &&
              topic.membership_match_type === "unavailable" &&
              typeof topic.membership_unavailable_reason === "string" &&
              topic.membership_unavailable_reason.trim().length > 0 &&
              members.members_complete === false &&
              members.items.length === 0 &&
              members.total === 0 &&
              members.membership_source === null &&
              members.membership_observed_date === null &&
              typeof members.unavailable_reason === "string" &&
              members.unavailable_reason.trim().length > 0 &&
              members.quote_coverage?.known === 0 &&
              members.quote_coverage?.unknown === 0 &&
              members.quote_coverage?.total === 0 &&
              members.quote_coverage?.complete === false &&
              (members.membership_snapshot_day === null ||
                (isDate(members.membership_snapshot_day) &&
                  members.membership_snapshot_day <= date)),
            members.unavailable_reason,
          );
        check(
          "dated " + kind + " member boards and calendar metrics",
          members.items.every(
            (row) =>
              ["main", "chinext", "star", "beijing", "unknown"].includes(
                row.board,
              ) &&
              typeof row.board_label === "string" &&
              [
                "return_ytd_pct",
                "return_mtd_pct",
                "limit_up_ytd",
                "sessions_since_limit_up",
              ].every(
                (field) => Object.hasOwn(row, field) && nullable(row[field]),
              ) &&
              (row.last_limit_up_date === null ||
                isDate(row.last_limit_up_date)),
          ),
        );
        check(
          "dated " +
            kind +
            " turnover is same-day percentage or explained unknown",
          members.items.every(
            (row) =>
              row.turnover_rate_unit === "%" &&
              (numeric(row.turnover_rate)
                ? row.turnover_rate_date === date &&
                  Boolean(row.turnover_rate_source)
                : typeof row.turnover_rate_reason === "string"),
          ),
        );
        const values = members.items
          .filter((row) => numeric(row.change_pct))
          .map((row) => row.change_pct);
        check(
          "dated " + kind + " members default change descending",
          values.every((value, index) => !index || values[index - 1] >= value),
        );
        const firstUnknown = members.items.findIndex(
          (row) => row.change_pct == null,
        );
        check(
          "dated " + kind + " members unknown last",
          firstUnknown < 0 ||
            members.items
              .slice(firstUnknown)
              .every((row) => row.change_pct == null),
        );
        check(
          "dated " + kind + " members do not use another quote date",
          members.items.every(
            (row) => !row.quote_available || row.quote_date === date,
          ),
        );
      }
    }
    if (route === "flow")
      check(
        "intraday missing remains explicit",
        typeof d.summary?.intraday_sequence_available === "boolean" &&
          (d.summary.intraday_sequence_available ||
            d.warnings?.some((w) => /全天|盘中/.test(w))),
      );
    if (route === "ladder")
      check(
        "prior high position feedback distinct",
        !d.high_position_feedback ||
          d.high_position_feedback.every(
            (r) =>
              numeric(r.previous_board_count) && r.previous_board_date < date,
          ),
      );
  }
  for (const period of ["D", "W", "M"]) {
    const d = await get(
      "/instruments/000001.SH/chart?period=" + period + "&as_of=" + date,
    );
    check(
      "chart " + period + " unit and identity",
      d.instrument.instrument_id === "000001.SH" &&
        Boolean(d.price_basis) &&
        Boolean(d.volume_unit) &&
        Boolean(d.amount_unit),
    );
    check(
      "chart " + period + " cutoff",
      d.bars.length > 0 && d.bars.every((b) => b.date <= date),
    );
    check(
      "chart " + period + " OHLC and nullable indicators",
      d.bars.every(
        (b) =>
          ["open", "high", "low", "close", "volume"].every((k) =>
            numeric(b[k]),
          ) &&
          [
            "ma5",
            "ma10",
            "ma20",
            "ma30",
            "ma60",
            "ma250",
            "macd_dif",
            "macd_dea",
            "macd_hist",
            "kdj_k",
            "kdj_d",
            "kdj_j",
          ].every((k) => nullable(b[k])),
      ),
    );
    if (period === "D")
      check(
        "real Shanghai breadth field",
        d.bars.some((b) => numeric(b.advancers)) &&
          typeof d.breadth_scope === "string",
      );
  }
  const found = await get("/instruments?query=600519&limit=12");
  page("global instrument search", found);
  check(
    "canonical security id",
    found.items.some((r) => r.instrument_id === "600519.SH"),
  );
  const historical = boot.market_dates.at(-8);
  const detail = await get("/instruments/600519.SH?as_of=" + historical);
  check("historical drawer date", detail.data_date === historical);
  check(
    "historical drawer omits current classifications",
    detail.topics.length === 0 && detail.risk_labels.length === 0,
  );
  const topics = await get("/topics");
  page("topics", topics);
  check(
    "topic unknown member counts distinguished",
    topics.items.every(
      (t) => t.member_count === null || numeric(t.member_count),
    ),
  );
  const selectableTopics = topics.items.filter(
    (row) => !row.archived && row.member_count > 0,
  );
  const firstConcept = selectableTopics.find((row) => row.kind === "concept");
  const firstIndustry = selectableTopics.find((row) => row.kind === "industry");
  if (firstConcept && firstIndustry) {
    const topicPath = (
      ids,
      match = "any",
      order = "desc",
      query = "",
      sortBy = "change_pct",
    ) => {
      const params = new URLSearchParams({
        match,
        sort_by: sortBy,
        sort_order: order,
        query,
      });
      ids.forEach((id) => params.append("topic_ids", id));
      return "/topic-members?" + params;
    };
    const first = await get(topicPath([firstConcept.topic_id]));
    const second = await get(topicPath([firstIndustry.topic_id]));
    const combined = await get(
      topicPath([firstConcept.topic_id, firstIndustry.topic_id]),
    );
    const intersection = await get(
      topicPath([firstConcept.topic_id, firstIndustry.topic_id], "all"),
    );
    const ascending = await get(
      topicPath([firstConcept.topic_id, firstIndustry.topic_id], "any", "asc"),
    );
    const ids = (rows) => new Set(rows.map((row) => row.instrument_id));
    const a = ids(first.items),
      b = ids(second.items),
      union = ids(combined.items),
      both = ids(intersection.items);
    check(
      "topic members independent business date",
      combined.business_date === date && combined.quote_date === date,
    );
    check(
      "topic member full union without duplicates",
      union.size === combined.total &&
        union.size === new Set([...a, ...b]).size &&
        [...a, ...b].every((id) => union.has(id)),
    );
    check(
      "topic and industry intersection",
      both.size === [...a].filter((id) => b.has(id)).length &&
        [...both].every((id) => a.has(id) && b.has(id)),
    );
    for (const [order, payload] of [
      ["desc", combined],
      ["asc", ascending],
    ]) {
      const values = payload.items
        .filter((row) => numeric(row.change_pct))
        .map((row) => row.change_pct);
      check(
        "topic member " + order + " full-set order",
        values.every(
          (v, i) =>
            !i || (order === "desc" ? values[i - 1] >= v : values[i - 1] <= v),
        ),
      );
      const firstUnknown = payload.items.findIndex(
        (row) => row.change_pct == null,
      );
      check(
        "topic member " + order + " unknown-last",
        firstUnknown < 0 ||
          payload.items
            .slice(firstUnknown)
            .every((row) => row.change_pct == null),
      );
    }
    check(
      "topic member quote and association shape",
      combined.items.every(
        (row) =>
          Array.isArray(row.topics) &&
          Array.isArray(row.industries) &&
          Array.isArray(row.matched_topics) &&
          typeof row.quote_available === "boolean" &&
          (!row.quote_available || row.quote_date === date) &&
          (row.quote_available ||
            [row.close, row.change_pct, row.amount, row.turnover_rate].every(
              (value) => value === null,
            )),
      ),
    );
    check(
      "topic member historical fields are explicit",
      combined.items.every(
        (row) =>
          [
            "return_ytd_pct",
            "change_30d_pct",
            "limit_up_ytd",
            "sessions_since_limit_up",
          ].every(
            (field) => Object.hasOwn(row, field) && nullable(row[field]),
          ) &&
          Object.hasOwn(row, "last_limit_up_date") &&
          (row.last_limit_up_date === null || isDate(row.last_limit_up_date)),
      ),
    );
    check(
      "topic latest limit date uses business trading calendar",
      combined.items.every(
        (row) =>
          row.last_limit_up_date === null ||
          (row.last_limit_up_date <= date &&
            (row.sessions_since_limit_up === null ||
              row.sessions_since_limit_up ===
                boot.market_dates.indexOf(date) -
                  boot.market_dates.indexOf(row.last_limit_up_date))),
      ),
    );
    for (const [field, order] of [
      ["return_ytd_pct", "desc"],
      ["return_ytd_pct", "asc"],
      ["change_30d_pct", "desc"],
      ["limit_up_ytd", "desc"],
      ["last_limit_up_date", "desc"],
      ["sessions_since_limit_up", "asc"],
    ]) {
      const payload = await get(
        topicPath(
          [firstConcept.topic_id, firstIndustry.topic_id],
          "any",
          order,
          "",
          field,
        ),
      );
      const values = payload.items
        .filter((row) => row[field] !== null)
        .map((row) => row[field]);
      check(
        "topic historical sort " + field + " " + order,
        values.every(
          (value, i) =>
            !i ||
            (order === "desc"
              ? values[i - 1] >= value
              : values[i - 1] <= value),
        ),
      );
      const firstUnknown = payload.items.findIndex(
        (row) => row[field] === null,
      );
      check(
        "topic historical unknown last " + field + " " + order,
        firstUnknown < 0 ||
          payload.items.slice(firstUnknown).every((row) => row[field] === null),
      );
    }
    if (combined.items.length) {
      const chosen = combined.items[0];
      const searched = await get(
        topicPath(
          [firstConcept.topic_id, firstIndustry.topic_id],
          "any",
          "desc",
          chosen.code,
        ),
      );
      check(
        "topic member search includes exact security",
        searched.items.some(
          (row) => row.instrument_id === chosen.instrument_id,
        ),
      );
    }
  }
  const strategies = await get("/strategies");
  page("strategies", strategies);
  const fields = await get("/strategy-fields");
  page("strategy fields", fields);
  check(
    "right hand field builder types",
    fields.items.every(
      (f) =>
        typeof f.field === "string" &&
        (["number", "boolean", "topic", "enum_set"].includes(f.type) ||
          (f.field === "boll_signal" &&
            f.type === "boll_config" &&
            f.constant_only === true &&
            f.signal_version === "boll-signal-v1" &&
            JSON.stringify(f.operators) === JSON.stringify(["matches"]))) &&
        Array.isArray(f.operators) &&
        (f.type !== "enum_set" ||
          (Array.isArray(f.choices) && f.choices.length > 0)),
    ),
  );
  check(
    "board and ST universe fields",
    fields.items.some((f) => f.field === "board" && f.type === "enum_set") &&
      fields.items.some((f) => f.field === "is_st" && f.type === "boolean"),
  );
  check(
    "today and previous-market-day limit-up boolean conditions",
    ["limit_up", "prev_limit_up"].every((name) =>
      fields.items.some(
        (field) => field.field === name && field.type === "boolean",
      ),
    ),
  );
  const yearlyLimitFields = fields.items.filter(
    (field) => field.field === "limit_up_count_1y",
  );
  const yearlyLimitCount = yearlyLimitFields[0];
  check(
    "yearly limit-up count has one numeric field",
    yearlyLimitFields.length === 1 && yearlyLimitCount?.type === "number",
  );
  check(
    "yearly limit-up count accepts nonnegative integer constants",
    yearlyLimitCount?.integer === true &&
      yearlyLimitCount.min === 0 &&
      yearlyLimitCount.step === 1 &&
      yearlyLimitCount.constant_only === true,
  );
  const yearlyLimitOperators = [">=", ">", "==", "<=", "<", "!="];
  check(
    "yearly limit-up count supports six numeric comparisons",
    Array.isArray(yearlyLimitCount?.operators) &&
      yearlyLimitCount.operators.length === yearlyLimitOperators.length &&
      new Set(yearlyLimitCount.operators).size ===
        yearlyLimitOperators.length &&
      yearlyLimitOperators.every((operator) =>
        yearlyLimitCount.operators.includes(operator),
      ),
  );
  check(
    "yearly limit-up count exposes yesterday cutoff in builder metadata",
    yearlyLimitCount?.label === "截至昨日近一年涨停次数" &&
      yearlyLimitCount.category === "数值比较" &&
      yearlyLimitCount.hidden_in_builder === false,
  );
  const bullish = fields.items.filter(
    (field) => field.field === "bullish_ma_alignment",
  );
  check(
    "bullish alignment is a visible boolean trend condition",
    bullish.length === 1 &&
      bullish[0].type === "boolean" &&
      bullish[0].category === "趋势状态" &&
      bullish[0].hidden_in_builder === false &&
      bullish[0].operators.join(",") === "==,!=",
  );
  const breakout = fields.items.find(
    (field) => field.field === "close_breakout_high",
  );
  check(
    "daily high breakout accepts a bounded integer window",
    breakout?.type === "number" &&
      breakout.integer === true &&
      breakout.min === 1 &&
      breakout.max === 750 &&
      breakout.parameter_kind === "lookback_sessions" &&
      breakout.operators.join(",") === "new_high",
  );
  const entryHigh = fields.items.find(
    (field) => field.field === "close_above_entry_high",
  );
  check(
    "entry high alert is a boolean tracking-only condition",
    entryHigh?.type === "boolean" &&
      entryHigh.tracking_only === true &&
      entryHigh.alerts_only === true,
  );
  for (const topN of [100, 200, 300, 500, 1000]) {
    const ranked = await get(
      "/market/gainers?" +
        new URLSearchParams({
          date,
          top_n: String(topN),
          page: "1",
          page_size: "25",
        }),
    );
    check(
      "30d top " + topN + " is a ranked universe before pagination",
      ranked.summary?.top_n === topN &&
        ranked.total <= topN &&
        ranked.items.every((row) => numeric(row.rank) && row.rank <= topN),
    );
  }
  const namedGainer = await get(
    "/market/gainers?date=" + date + "&query=600825&page=1&page_size=25",
  );
  check(
    "rank search retains Xinhua Media",
    namedGainer.items.some((r) => r.instrument_id === "600825.SH"),
  );
  check(
    "rank server pagination envelope",
    namedGainer.page === 1 &&
      namedGainer.page_size === 25 &&
      namedGainer.pages >= 1,
  );
  check(
    "rank endpoint or explicit unknown",
    namedGainer.items.every(
      (r) =>
        isDate(r.return_30d_start) &&
        isDate(r.return_30d_end) &&
        (numeric(r.return_30d) || typeof r.return_30d_reason === "string"),
    ),
  );
  const basisChart = await get(
    "/instruments/601900.SH/chart?period=D&as_of=" +
      date +
      "&price_basis=forward",
  );
  check(
    "explicit chart basis contract",
    basisChart.requested_price_basis === "forward" &&
      ["none", "forward_event_affine"].includes(basisChart.price_basis) &&
      typeof basisChart.adjustment?.usable === "boolean",
  );
  check(
    "chart gap diagnostics",
    Array.isArray(basisChart.indicator_gaps) &&
      basisChart.indicator_gaps.every(
        (g) =>
          isDate(g.start_date) &&
          isDate(g.end_date) &&
          typeof g.reason === "string",
      ),
  );
  const waveChart = await get(
    "/instruments/000001.SH/chart?period=D&as_of=" + date,
  );
  check(
    "candidate waves confirmation cutoff",
    waveChart.waves?.method ===
      "confirmed-fractal-price-skeleton-v3-continuous-runs" &&
      waveChart.waves.points.every(
        (p) => p.date <= p.confirmed_at && p.confirmed_at <= date,
      ),
  );
  check(
    "index limit events not applicable",
    waveChart.limit_event_coverage?.applicable === false &&
      waveChart.limit_events?.length === 0,
  );
  const limitRaw = await get(
    "/instruments/600825.SH/chart?period=D&as_of=" + date,
  );
  const limitForward = await get(
    "/instruments/600825.SH/chart?period=D&as_of=" +
      date +
      "&price_basis=forward",
  );
  const limitWeekly = await get(
    "/instruments/600825.SH/chart?period=W&as_of=" + date,
  );
  const downChart = await get(
    "/instruments/603999.SH/chart?period=D&as_of=" + date,
  );
  for (const [name, chart] of [
    ["up", limitRaw],
    ["down", downChart],
  ]) {
    check(
      "daily limit " + name + " tri-state and evidence",
      chart.bars.every(
        (bar) =>
          [bar.limit_up_state, bar.limit_down_state].every(
            (value) => value === null || typeof value === "boolean",
          ) && Array.isArray(bar.limit_events),
      ),
    );
    check(
      "daily limit " + name + " verified cutoff",
      chart.limit_events.every(
        (event) =>
          isDate(event.date) &&
          event.date <= date &&
          event.basis === "verified-source-close-pool" &&
          ["limit_up", "limit_down"].includes(event.kind) &&
          chart.bars.some(
            (bar) =>
              bar.date === event.date &&
              bar.limit_events.some((own) => own.kind === event.kind),
          ),
      ),
    );
    check(
      "daily limit " + name + " coverage does not infer identity",
      chart.limit_event_coverage?.price_validation_basis ===
        "same-date-unadjusted-daily-close" &&
        chart.limit_event_coverage.historical_identity_inferred === false,
    );
  }
  const eventKeys = (chart) =>
    chart.limit_events
      .map((event) => event.date + ":" + event.kind)
      .sort()
      .join("|");
  check(
    "forward display preserves raw closing-limit events",
    eventKeys(limitRaw) === eventKeys(limitForward),
  );
  check(
    "weekly limit facts are daily summaries",
    limitWeekly.bars.every(
      (bar) =>
        bar.limit_up_state === null &&
        bar.limit_down_state === null &&
        bar.limit_event_summary?.scope === "weekly_daily_events",
    ) &&
      limitWeekly.limit_events.every(
        (event) => event.date <= event.bar_date && event.bar_date <= date,
      ),
  );
  if (strategies.items.length) {
    const s = strategies.items[0];
    const automation = await get(
      "/strategies/" + s.strategy_id + "/automation",
    );
    check(
      "post-sync booleans",
      typeof automation.refresh_after_sync === "boolean" &&
        typeof automation.auto_track === "boolean" &&
        (!automation.auto_track || automation.refresh_after_sync),
    );
    const runs = await get("/strategy-runs?strategy_id=" + s.strategy_id);
    page("strategy history", runs);
    if (runs.items.length) {
      check(
        "strategy history count shape",
        numeric(runs.items[0].counts?.matched) ||
          numeric(runs.items[0].matched_count),
      );
      const run = await get("/strategy-runs/" + runs.items[0].run_id);
      check(
        "candidate totals match persisted decisions",
        run.items.length ===
          run.matched_count + run.unmatched_count + run.unknown_count,
      );
    }
  }
  const tracking = await get("/tracking-rounds?status=all");
  page("tracking", tracking);
  check("tracking independent current date", tracking.business_date === date);
  check(
    "tracking frozen entry types",
    tracking.items.every(
      (r) =>
        isDate(r.entry_business_date) &&
        numeric(r.entry_price) &&
        r.entry_price > 0,
    ),
  );
  const events = await get("/tracking-events?event_type=alert&limit=200");
  page("trigger list", events);
  const research = await get("/research/jobs");
  page("research history", research);
  if (research.items.length) {
    const r = await get("/research/jobs/" + research.items[0].report_id);
    check(
      "research persisted report shape",
      isDate(r.data_cutoff) &&
        Array.isArray(r.sections) &&
        Array.isArray(r.materials) &&
        Array.isArray(r.warnings) &&
        typeof r.instrument.instrument_id === "string",
    );
  }
  const backtests = await get("/backtests");
  page("backtest history", backtests);
  const complete = backtests.items.find((r) => r.status === "completed");
  if (complete) {
    const r = await get("/backtests/" + complete.backtest_id);
    check(
      "seven backtest metric types",
      [
        "total_return",
        "annual_return",
        "max_drawdown",
        "win_rate",
        "payoff_ratio",
        "trade_count",
        "final_equity",
      ].every((k) => nullable(r.stats[k])),
    );
    check(
      "backtest record categories",
      ["orders", "fills", "trades", "positions", "equity"].every((k) =>
        Array.isArray(r[k]),
      ),
    );
    check(
      "equity reconciliation",
      Math.abs(r.equity.at(-1).equity - r.stats.final_equity) < 0.0001,
    );
    check(
      "final holdings filter matches account",
      r.positions.filter((p) => p.date === r.end_date).length ===
        r.stats.holding_count,
    );
    check("closed trades count", r.trades.length === r.stats.trade_count);
  }
  const data = await get("/data/status");
  const listingStatus = await get("/data/listing-status");
  const identities = listingStatus.identities;
  check(
    "listing identity catalog and applicable denominator are separate",
    listingStatus.business_date === date &&
      typeof listingStatus.summary_is_current === "boolean" &&
      identities &&
      [
        "catalog_count",
        "stock_count",
        "listed_count",
        "known_count",
        "pending_listing_count",
        "unknown_count",
      ].every(
        (key) => Number.isSafeInteger(identities[key]) && identities[key] >= 0,
      ) &&
      identities.catalog_count === identities.stock_count &&
      identities.listed_count === identities.known_count &&
      identities.listed_count +
        identities.pending_listing_count +
        identities.unknown_count ===
        identities.catalog_count,
  );
  check(
    "pending identities carry explicit source or user evidence, unknowns stay separate",
    Array.isArray(identities?.pending_listing) &&
      identities.pending_listing.length === identities.pending_listing_count &&
      identities.pending_listing.every(
        (row) =>
          row.listing_status === "pending" &&
          ["source_list_date", "manual_user_confirmation"].includes(
            row.evidence_kind,
          ) &&
          typeof row.source === "string" &&
          typeof row.reason === "string" &&
          typeof row.can_edit === "boolean" &&
          row.reference &&
          typeof row.reference === "object",
      ) &&
      Array.isArray(identities?.unknown) &&
      identities.unknown.length === identities.unknown_count &&
      identities.unknown.every((row) =>
        ["unknown", "quoted_unknown_date"].includes(row.listing_status),
      ),
  );
  const gapSource = await get("/data/quote-gap-source");
  check(
    "quote-gap source check is a small read-only plan, not an implicit task",
    typeof gapSource.available === "boolean" &&
      (gapSource.available
        ? typeof gapSource.plan_id === "string" &&
          typeof gapSource.diagnosis_id === "string" &&
          typeof gapSource.snapshot_id === "string" &&
          Number.isSafeInteger(gapSource.stock_count) &&
          Number.isSafeInteger(gapSource.missing_sessions) &&
          typeof gapSource.is_current === "boolean"
        : typeof gapSource.reason === "string"),
  );
  check(
    "saved source evidence separates subset scope and retains publication",
    gapSource.evidence === null ||
      (gapSource.is_current === true &&
        [
          "planned_stocks",
          "checked_stocks",
          "failed_stocks",
          "checked_sessions",
          "fillable_sessions",
          "source_absent_sessions",
          "overlap_difference_sessions",
        ].every(
          (key) =>
            Number.isSafeInteger(gapSource.evidence?.[key]) &&
            gapSource.evidence[key] >= 0,
        ) &&
        gapSource.evidence.checked_stocks <=
          gapSource.evidence.planned_stocks &&
        gapSource.evidence.publication_changed === false &&
        gapSource.evidence.fillable_sessions +
          gapSource.evidence.source_absent_sessions ===
          gapSource.evidence.checked_sessions),
  );
  check(
    "data center status shape",
    isDate(data.business_date) &&
      Array.isArray(data.datasets) &&
      numeric(data.summary.row_count),
  );
  const tasks = await get("/tasks");
  const activity = await get("/tasks/activity");
  check(
    "compact task activity exposes display-only operation for cross-page completion",
    Array.isArray(activity.items) &&
      numeric(activity.active_count) &&
      numeric(activity.observed_at_ms) &&
      activity.items.every(
        (task) =>
          Object.hasOwn(task, "operation") &&
          (task.operation === null || typeof task.operation === "string"),
      ),
  );
  page("task history", tasks);
  check(
    "progress is percentage points",
    tasks.items.every(
      (t) =>
        numeric(t.progress) &&
        t.progress >= 0 &&
        t.progress <= 100 &&
        Array.isArray(t.logs),
    ),
  );
  const asc = await get(
    `/market/gainers?date=${date}&sort_by=change_pct&sort_order=asc&page_size=25`,
  );
  const next = await get(
    `/market/gainers?date=${date}&sort_by=change_pct&sort_order=asc&page_size=25&page=2`,
  );
  const desc = await get(
    `/market/gainers?date=${date}&sort_by=change_pct&sort_order=desc&page_size=25`,
  );
  const ascending = asc.items.filter((r) => numeric(r.change_pct));
  const descending = desc.items.filter((r) => numeric(r.change_pct));
  check(
    "whole-market ascending sort",
    asc.sort_order === "asc" &&
      typeof asc.sort_scope === "string" &&
      ascending.every(
        (r, i) => !i || ascending[i - 1].change_pct <= r.change_pct,
      ),
  );
  check(
    "whole-market descending sort",
    desc.sort_order === "desc" &&
      descending.every(
        (r, i) => !i || descending[i - 1].change_pct >= r.change_pct,
      ),
  );
  check(
    "sort then page boundary",
    next.page === 2 &&
      asc.total === next.total &&
      (!ascending.length ||
        !numeric(next.items[0]?.change_pct) ||
        ascending.at(-1).change_pct <= next.items[0].change_pct),
  );
  check(
    "sync and opt-in automatic configuration",
    typeof data.sync_configuration?.ready === "boolean" &&
      typeof data.automatic_schedule === "boolean",
  );
  const automatic = await get("/settings/automatic-tasks");
  check(
    "automatic settings match data status and preserve explicit activation",
    automatic.timezone === "Asia/Shanghai" &&
      Number.isSafeInteger(automatic.revision) &&
      automatic.revision >= 0 &&
      [automatic.noon, automatic.after_close].every(
        (item) =>
          typeof item?.enabled === "boolean" &&
          typeof item.time === "string" &&
          /^\d{2}:\d{2}$/.test(item.time),
      ) &&
      data.automatic_schedule ===
        (automatic.noon.enabled || automatic.after_close.enabled),
  );
  const sources = await get("/sources");
  page("data sources", sources);
  const fuyao = sources.items.find((s) => s.source_id === "fuyao");
  const publicSource = sources.items.find(
    (s) => s.source_id === "tickflow-public",
  );
  const tickflow = sources.items.find((s) => s.source_id === "tickflow");
  check(
    "Fuyao registered free attributes",
    fuyao?.billing_mode === "registered_free" &&
      fuyao?.requires_key === true &&
      fuyao?.sync_supported === true,
  );
  check(
    "public free without key",
    publicSource?.billing_mode === "public_free" &&
      publicSource?.requires_key === false,
  );
  check(
    "account access from probes",
    tickflow?.billing_mode === "account" &&
      tickflow?.requires_key === true &&
      sources.items.every((s) =>
        s.capabilities.every((c) => "checked_at" in c && "account_access" in c),
      ),
  );

  check(
    "source credentials never returned",
    sources.items.every((s) => !s.api_key && !s.token && !s.secret),
  );
  check(
    "actual source capabilities",
    sources.items.every(
      (s) =>
        Array.isArray(s.capabilities) &&
        s.capabilities.every(
          (c) =>
            typeof c.key === "string" &&
            typeof c.status === "string" &&
            typeof c.reason === "string",
        ),
    ),
  );
} catch (e) {
  check("audit completed", false, e.message);
}
const report = {
  scope:
    "frontend local GET API contract audit for an isolated data copy; research report reads renew cache TTL and retention pins; no source authentication/configuration, no trade or sync creation",
  checked_at: new Date().toISOString(),
  pages: 15,
  passed: checks.filter((c) => c.passed).length,
  total: checks.length,
  checks,
};
await fs.writeFile(
  "api-contract-checks.json",
  JSON.stringify(report, null, 2) + "\n",
);
console.log(
  JSON.stringify(
    {
      passed: report.passed,
      total: report.total,
      failed: checks.filter((c) => !c.passed),
    },
    null,
    2,
  ),
);
if (report.passed !== report.total) process.exitCode = 1;
