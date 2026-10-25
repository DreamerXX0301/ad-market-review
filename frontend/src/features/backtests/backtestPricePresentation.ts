type Row = Record<string, any>;
/** Frozen signal and execution lanes have different purposes and price bases. */
export function backtestPriceContext(value: Row | null | undefined) {
  if (value?.signal_price_basis !== "forward") return null;
  return {
    label:
      value.execution_price_basis === "none"
        ? "前复权信号 · 未复权撮合"
        : "前复权信号 · 撮合口径待核验",
    reason: `信号使用当时观察日锚定的本地前复权价格；成交与账户记账使用${value.execution_price_basis === "none" ? "未复权原价" : "待核验价格口径"}，前复权信号不等于含分红收益${value.price_basis_contract ? `。价格口径合约：${value.price_basis_contract}` : "。价格口径合约待核验"}。`,
  };
}
