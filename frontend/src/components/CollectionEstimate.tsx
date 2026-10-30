import type { Row } from "../lib/api";
import { duration } from "../lib/format";

export default function CollectionEstimate({
  estimate,
}: {
  estimate?: Row | null;
}) {
  const lower = estimate?.remaining_min_ms,
    upper = estimate?.remaining_max_ms;
  const known =
    typeof lower === "number" &&
    Number.isFinite(lower) &&
    lower >= 0 &&
    typeof upper === "number" &&
    Number.isFinite(upper) &&
    upper >= lower &&
    upper > 0;
  return (
    <div className="increment-progress-estimate" aria-live="polite">
      {known ? (
        <>
          预计还需 {duration(lower)}～{duration(upper)}
          <small>
            基于 {estimate!.sample_count}{" "}
            次同类已完成任务，包含排队时间，仅供参考
          </small>
        </>
      ) : (
        estimate?.reason || "历史样本不足，暂无法估计剩余时间"
      )}
    </div>
  );
}
