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
        {view.valid && (
          <div className="data-storage-table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">组成</th>
                  <th scope="col">逻辑大小</th>
                  <th scope="col">文件数</th>
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
          <p>旧代际可能仍被历史事件、任务或报告引用，本页仅展示构成。</p>
        </div>
      </div>
    </details>
  );
}
