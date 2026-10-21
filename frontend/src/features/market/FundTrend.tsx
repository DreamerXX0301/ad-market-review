import { useState } from "react";
import type { Row } from "../../lib/api";
import { integer } from "../../lib/format";
import { EChart } from "../../components/Charts";
import { Panel } from "../../components/UI";
import { MetricHelp } from "./MetricHelp";
import { marketMoney } from "./marketPresentation";

export default function FundTrend({ data }: { data: Row }) {
  const [focus, setFocus] = useState("");
  const items: Row[] = data.items || [];
  const history: Row[] = data.history || [];
  const dates = [...new Set(history.map((row) => row.date))].sort();
  const sorted = [...items].sort(
    (a, b) => Math.abs(b.net_inflow) - Math.abs(a.net_inflow),
  );
  const effectiveFocus = items.some((row) => row.topic_code === focus)
    ? focus
    : "";
  const chosen = effectiveFocus
    ? items.filter((row) => row.topic_code === effectiveFocus)
    : sorted.slice(0, 8);
  const values: (number | string)[][] = [];
  const rows = new Map(
    history.map((row) => [row.topic_code + ":" + row.date, row]),
  );
  chosen.forEach((sector, y) =>
    dates.forEach((date, x) => {
      const value = rows.get(sector.topic_code + ":" + date)?.net_inflow;
      if (typeof value === "number" && Number.isFinite(value))
        values.push([x, y, value]);
    }),
  );
  const scale = Math.max(1, ...values.map((row) => Math.abs(Number(row[2]))));
  const coverage = data.coverage || {};
  return (
    <Panel
      title={
        <>
          近20个交易日行业资金趋势{" "}
          <MetricHelp
            label="行业日级资金趋势"
            description="每格为对应行业、对应交易日的来源主力净流入，金额已统一为人民币元。默认按观察日净流入绝对值选取8个行业，也可单选；这是跨交易日历史，不是全天盘中轮动时序。空白表示缺数据，不能补作0。"
          />
        </>
      }
      subtitle="红色净流入 · 绿色净流出 · 空白表示缺数据，非零流入"
      actions={
        <select
          aria-label="资金趋势观察行业"
          value={effectiveFocus}
          onChange={(event) => setFocus(event.target.value)}
        >
          <option value="">当日资金变化较大的8个行业</option>
          {items.map((row) => (
            <option key={row.topic_code} value={row.topic_code}>
              {row.name}
            </option>
          ))}
        </select>
      }
    >
      <EChart
        height={330}
        option={{
          animation: false,
          grid: { top: 20, left: 100, right: 24, bottom: 70 },
          tooltip: {
            confine: true,
            formatter: (event: any) => {
              const [x, y, value] = event.value || [];
              return `${dates[x]}<br/>${chosen[y]?.name || ""}<br/>主力净流入 ${marketMoney(value)}`;
            },
          },
          xAxis: {
            type: "category",
            data: dates,
            axisLabel: {
              color: "#8191a8",
              fontSize: 9,
              formatter: (value: string) => value.slice(5),
            },
            splitArea: { show: true },
          },
          yAxis: {
            type: "category",
            data: chosen.map((row) => row.name),
            axisLabel: { color: "#8191a8", fontSize: 10 },
            splitArea: { show: true },
          },
          visualMap: {
            min: -scale,
            max: scale,
            calculable: false,
            orient: "horizontal",
            left: "center",
            bottom: 12,
            text: ["流入", "流出"],
            textStyle: { color: "#8191a8", fontSize: 9 },
            inRange: { color: ["#228f78", "#263344", "#e86765"] },
          },
          series: [
            {
              type: "heatmap",
              data: values,
              itemStyle: { borderColor: "#111924", borderWidth: 2 },
              emphasis: {
                itemStyle: { borderColor: "#8ba9ce", borderWidth: 1 },
              },
            },
          ],
        }}
      />
      <div className="panel-footnote">
        已取得 {integer(coverage.observed_count ?? items.length)} /{" "}
        {integer(coverage.selected_count ?? coverage.directory_count)}{" "}
        个当前目录行业；当前窗口已留存 {dates.length}/20 个交易日，20日完整{" "}
        {integer(coverage.history_complete_count)}{" "}
        项。此处展示跨交易日趋势，全天盘中资金时序尚未接入。
      </div>
    </Panel>
  );
}
