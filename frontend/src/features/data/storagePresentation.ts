type StorageRow = Record<string, any>;

const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const number = (value: number, digits: number) =>
  value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

/** Decimal GB is the primary unit; exact bytes keep small values unambiguous. */
export function storageSize(value: unknown) {
  if (!count(value)) return { gb: "—", gib: "—", bytes: "—" };
  const rounded = (unit: number, label: string) =>
    value > 0 && value < unit / 1000
      ? `<0.001 ${label}`
      : `${number(value / unit, 3)} ${label}`;
  return {
    gb: rounded(1_000_000_000, "GB"),
    gib: rounded(1_073_741_824, "GiB"),
    bytes: `${number(value, 0)} B`,
  };
}

const categories = [
  ["current_market", "当前行情代际"],
  ["old_market", "旧行情代际"],
  ["shared_market", "跨版本共享行情对象"],
  ["supplement", "补充数据"],
  ["backtests", "回测结果"],
  ["metadata_other", "目录元数据与其他文件"],
] as const;

/** Reconcile both legacy five-way and partitioned six-way inventories. */
export function publishedStorageView(summary: StorageRow | null | undefined) {
  const accounting = summary?.storage_accounting;
  const provided = accounting !== undefined && accounting !== null;
  const rows: StorageRow[] = Array.isArray(accounting?.categories)
    ? accounting.categories
    : [];
  const recognized =
    (rows.length === categories.length ||
      rows.length === categories.length - 1) &&
    categories.every(
      ([key]) =>
        rows.filter((row) => row?.key === key).length === 1 ||
        (key === "shared_market" && rows.length === categories.length - 1),
    );
  const valid =
    provided &&
    accounting.scope === "active_published_root" &&
    typeof accounting.complete === "boolean" &&
    count(accounting.logical_bytes) &&
    count(accounting.file_count) &&
    recognized &&
    rows.every((row) => count(row.logical_bytes) && count(row.file_count)) &&
    rows.reduce((sum, row) => sum + row.logical_bytes, 0) ===
      accounting.logical_bytes &&
    rows.reduce((sum, row) => sum + row.file_count, 0) ===
      accounting.file_count &&
    (summary?.storage_bytes === undefined ||
      summary.storage_bytes === accounting.logical_bytes);
  const total = valid
    ? accounting.logical_bytes
    : !provided && count(summary?.storage_bytes)
      ? summary.storage_bytes
      : null;
  const size = storageSize(total);
  const complete = valid ? accounting.complete : null;
  const excluded = accounting?.excluded;
  const omitted = valid
    ? [
        ["staging_entries", "临时项"],
        ["symlink_entries", "符号链接"],
        ["special_entries", "其他文件类型"],
      ]
        .filter(([key]) => count(excluded?.[key]) && excluded[key] > 0)
        .map(([key, label]) => `${label} ${number(excluded[key], 0)} 项`)
    : [];
  const writes = summary?.last_storage_write;
  const objectRows =
    writes && typeof writes === "object"
      ? (Object.values(writes).filter(
          (row: any) =>
            count(row?.new_object_bytes) && count(row?.reused_object_bytes),
        ) as StorageRow[])
      : [];
  const retention = summary?.retention;
  const validDate = (value: unknown): value is string =>
    typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  return {
    valid,
    complete,
    total,
    size,
    value: size.gb,
    sub:
      complete === false
        ? "已计量小计 · 部分文件暂未计入"
        : valid
          ? "当前发布目录 · 文件逻辑大小"
          : "当前发布目录 · 构成明细暂不可用",
    reason: valid
      ? typeof accounting.reason === "string"
        ? accounting.reason
        : null
      : provided
        ? "文件构成与总量暂未对齐，请刷新后再查看。"
        : "尚未取得文件构成明细，请刷新后再查看。",
    rows: valid
      ? categories
          .filter(([key]) => rows.some((row) => row.key === key))
          .map(([key, label]) => {
            const row = rows.find((item) => item.key === key)!;
            return {
              key,
              label,
              size: storageSize(row.logical_bytes),
              files: number(row.file_count, 0),
              percentage:
                accounting.logical_bytes > 0
                  ? `${number((row.logical_bytes / accounting.logical_bytes) * 100, 2)}%`
                  : "0.00%",
              generations: count(row.generation_count)
                ? number(row.generation_count, 0)
                : null,
            };
          })
      : [],
    files: valid ? number(accounting.file_count, 0) : "—",
    allocated:
      valid && count(accounting.allocated_bytes)
        ? storageSize(accounting.allocated_bytes)
        : null,
    omitted,
    layout:
      summary?.storage_layout === "market-partitions-v1"
        ? "按月分片 · 跨版本共享"
        : "兼容旧单文件格式",
    objectWrite: objectRows.length
      ? {
          added: storageSize(
            objectRows.reduce((sum, row) => sum + row.new_object_bytes, 0),
          ),
          reused: storageSize(
            objectRows.reduce((sum, row) => sum + row.reused_object_bytes, 0),
          ),
        }
      : null,
    retention:
      validDate(retention?.active_begin) && validDate(retention?.active_end)
        ? {
            activeBegin: retention.active_begin,
            activeEnd: retention.active_end,
            physicalBegin: validDate(retention.physical_begin)
              ? retention.physical_begin
              : null,
            physicalEnd: validDate(retention.physical_end)
              ? retention.physical_end
              : null,
            protectedSeeds: retention.recursive_seed_preserved === true,
            automaticCollection: retention.automatic_collection === true,
          }
        : null,
  };
}

export type PublishedStorageView = ReturnType<typeof publishedStorageView>;
