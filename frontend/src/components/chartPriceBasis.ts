type Row = Record<string, any>;

/** Explain the returned chart's actual basis, independently of the UI preference. */
export function chartPriceBasis(data: Row | null, isIndex = false) {
  if (isIndex && data?.price_basis === "none")
    return {
      label: "指数原始点位",
      reason: "本图显示来源指数点位，不采用个股复权价格。",
    };
  if (data?.price_basis === "forward_event_affine")
    return {
      label: "本地前复权",
      reason: `本图历史价格及指标使用本地前复权，以行情截止日 ${data.as_of || "待核验"} 为锚。顶部原始收盘报价另行保留来源价格。`,
    };
  if (data?.price_basis === "none")
    return {
      label:
        data.requested_price_basis === "forward"
          ? "未复权（前复权不可用）"
          : "未复权",
      reason:
        data.adjustment?.reason ||
        "本图价格及指标使用来源未复权价格；除权派息可能重启指标预热。",
    };
  return {
    label: "价格口径待核验",
    reason: "本图响应没有可核验的实际价格口径，不按下拉框偏好推断。",
  };
}
