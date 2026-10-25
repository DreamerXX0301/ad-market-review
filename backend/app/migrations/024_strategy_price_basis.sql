-- 保存每个个股策略版本的技术指标与事件价格口径；既有版本保持原价。
ALTER TABLE strategy_version ADD COLUMN price_basis TEXT NOT NULL DEFAULT 'none'
  CHECK(price_basis IN ('none','forward'));
