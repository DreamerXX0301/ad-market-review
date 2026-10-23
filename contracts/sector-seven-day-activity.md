# 行业、题材榜近七日活跃

盘面行业与题材接口在原 `items` 每行追加 `sector_activity_7d`；不改变原始行集合、金额、涨幅与排序。顶层同名字段是统计覆盖说明，不是主线识别。

## 固定口径

- 窗口为观察日及此前六个市场交易日，包含观察日。缺交易日历不能补足七日；请求和计数不使用观察日之后的数据。
- `change_pct`：该来源、同分类内正涨幅最高十名（`change_pct > 0`），不是下跌榜；`amount`：该来源、同分类内成交额最高十名（金额非负），不是净流入。相同指标值以来源指数代码升序确定名次。
- 概念与行业分开；行业按明确的 `classification_level` 或 `level` 分开。行业层级未知的目录单独统计，明确标注“来源行业层级未知目录”，不默认成一级行业。前端切换跌幅、涨停家数、资金等排序也不改变这两个固定历史榜。
- 跨日身份严格匹配 `source + normalization_version + kind + industry_level + topic_code`。同名异源、同源异代码、异分类和异层级不合并。名称变化不改变来源指数身份。
- 历史排名直接来自各日已留存、已核验的来源指数价格；不使用当前成员回填历史，不要求该日成员快照。成员样本派生的行不能混进来源指数排名，活跃次数保持未知。
- 这是**本地已留存来源指数目录**的排名，不保证完整历史全市场目录。目录可能来自较晚的采集批次，原覆盖与目录观察时间仍由 `quote_coverage`、行 `catalog_observed_date` 等字段提供；不将目录归属解释为当日主线归因。已核验的小目录不足十项或正涨幅不足十项时，可以是完整的本地样本榜，不能称全市场前十。

## 逐行字段

```json
{
  "sector_activity_7d": {
    "change_pct": {
      "count": 3,
      "dates": ["2026-10-20", "2026-10-21", "2026-10-22"],
      "ranks": [
        {"date": "2026-10-20", "rank": 2, "value": 1.25},
        {"date": "2026-10-21", "rank": 1, "value": 2.5},
        {"date": "2026-10-22", "rank": 4, "value": 0.8}
      ],
      "observed_days": 3,
      "expected_days": 7,
      "complete": false,
      "state": "observed_partial",
      "label": "已观察3次 · 样本不完整",
      "reason": "缺少同源、同类、同行业层级的完整7日本地榜；仅展示已观察次数，补齐竞争者后排名与次数可能改变，不是次数下界",
      "window_start": "2026-10-14",
      "window_end": "2026-10-22",
      "scope": "统计范围与不足说明",
      "basis": "同类正涨幅前10",
      "metric": "change_pct",
      "group_kind": "concept",
      "group_level": null,
      "quote_basis": "provider_index",
      "source": "fuyao"
    },
    "amount": {"count": null, "state": "unknown", "complete": false}
  }
}
```

示例为字段说明，不是产品演示数据；`amount` 的完整结构与 `change_pct` 相同。`ranks[].value` 使用该指标原数值：涨幅是百分数，成交额沿用来源金额单位。前端只显示日期/名次时不把 `value` 一概叫成交额。

- `complete`：市场日历可核验，七个日期都存在该同源分类/层级分组，且每日价格表明确完整、身份唯一、该指标有效。`count` 可为零；七日重复上榜两次以上标注“持续活跃”。
- `observed_partial`：已有本地样本榜命中，但缺日期、缺竞争样本或校验未完整。保留已观察次数与明细，标注“已观察N次 · 样本不完整”。**不称“至少N次”或次数下界**：后补竞争者可能挤出已有第十名，名次和次数可以降低。
- `unknown`：未完整核验且未观察到命中，或不是可核验来源指数行。`count=null`，不能把缺失当零。没有成员历史不会单独使真实来源指数价格失效。
- `observed_days` 是该同源分类/层级有本地榜事实的日期数，不等于全部已核验完成的日期数；不足原因与顶层 `incomplete_dates` 一起说明。

## 顶层覆盖与读取边界

`sector_activity_7d` 包含 `algorithm_version`、`expected_days=7`、`window_start`、`window_end`、`scope`、固定 `metrics=["change_pct","amount"]` 与 `groups[]`。

每个分组包含 `source/kind/level/metric/observed_days/complete/missing_dates/incomplete_dates/reason`。它只解释统计覆盖，不生成“活跃主线”结论。

使用 `sector_prices_for_date` 现有精确日期、来源代码、请求证明与 SHA 校验；只读本次七日窗口的留存文件，观察日复用已验收价格，不联网、不触发盘面补充、不展开所有历史成员或重复计算七次个股指标。事实缓存最多 32 项，按行情代际、市场实例、观察日、窗口、算法版本、补充描述符与文件元数据隔离；缓存命中仍以本次观察日已验收价格为准，不保留市场大表的强引用。补充数据指针在同一主行情代际更新后可失效重算。
