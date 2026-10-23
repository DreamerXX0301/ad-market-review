import { ChevronRight, Database } from "lucide-react";
import type { PublishedStorageView } from "./storagePresentation";

export default function PublishedStorageDetails({
  view,
}: {
  view: PublishedStorageView;
}) {
  return (
    <details className="data-storage-details">
      <summary>
        <span>
          <Database size={14} /> 发布文件构成
        </span>
        <span>
          {view.valid ? `${view.files} 个文件 · ` : ""}
          查看构成及统计范围 <ChevronRight size={14} />
        </span>
      </summary>
      <div>
        <div className="data-storage-total">
          <strong>
            {view.complete === false ? "已计量小计" : "本实例已发布文件"} ·{" "}
            {view.size.gb}
          </strong>
          <span>
            {view.total === null
              ? "构成明细待读取"
              : `约 ${view.size.gib} · ${view.size.bytes}`}
          </span>
        </div>
        {view.complete === false && (
          <p className="muted">
            扫描尚未完整，以下仅为已计量文件，不代表完整总量。
          </p>
        )}
        {view.reason && <p className="muted">{view.reason}</p>}
        <div className="data-storage-scope">
          <p>主行情存储：{view.layout}。</p>
          {view.objectWrite && (
            <p>
              最近一次主行情对象写入：新增 {view.objectWrite.added.gb}，复用{" "}
              {view.objectWrite.reused.gb}
              ；不包含业务库、下载原包、补充资料与备份。
              这是本代构建用量；首次转换包含全量成本，不代表每日增长。
            </p>
          )}
          {view.retention && (
            <>
              <p>
                活动窗口：{view.retention.activeBegin} →{" "}
                {view.retention.activeEnd}；实际报价保留：
                {view.retention.physicalBegin || "待核验"} →{" "}
                {view.retention.physicalEnd || "待核验"}。
              </p>
              <p>
                {view.retention.protectedSeeds
                  ? "窗口外的递推指标种子与历史引用独立保护。"
                  : "窗口外历史引用独立核验。"}
                {view.retention.automaticCollection
                  ? "已启用按引用回收。"
                  : "自动物理回收未开启；维护清理先核验引用并预览，不按日期盲删。"}
              </p>
            </>
          )}
        </div>
        {view.valid && (
          <div className="data-storage-table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">组成</th>
                  <th scope="col">逻辑大小</th>
                  <th scope="col">文件数</th>
                  <th scope="col">占比</th>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((row) => (
                  <tr key={row.key}>
                    <th scope="row">
                      {row.label}
                      {row.generations !== null && (
                        <small>{row.generations} 代</small>
                      )}
                    </th>
                    <td>
                      <strong>{row.size.gb}</strong>
                      <small>
                        约 {row.size.gib} · {row.size.bytes}
                      </small>
                    </td>
                    <td>{row.files}</td>
                    <td>{row.percentage}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="data-storage-scope">
          <p>
            仅统计当前实例 published 内的普通文件，排除临时项与符号链接。
            业务库、原始数据 raw、工作文件
            work（含缓存、断点与核验记录）、其他运行版本和备份不在本项统计内。
          </p>
          <p>1 GB = 1,000,000,000 B；1 GiB = 1,073,741,824 B。</p>
          {view.allocated && (
            <p>
              文件已分配块量：{view.allocated.gb} · {view.allocated.bytes}。
              这不代表磁盘独占占用或可释放空间。
            </p>
          )}
          {view.omitted.length > 0 && (
            <p>本次已排除：{view.omitted.join("、")}。</p>
          )}
          <p>
            共享行情对象按文件计量一次，由多个版本清单共同引用。旧代际可能仍被历史事件、任务或报告引用，本页仅展示构成，不将其直接当作可清理空间。
          </p>
        </div>
      </div>
    </details>
  );
}
