# 主行情共享对象与回收契约

## 格式与身份

新手动初始化、盘后增量及题材刷新冻结 `storage_layout=market-partitions-v1`。没有 marker 的旧检查点继续旧格式，不在恢复时扩大任务或重释来源请求。旧 `market-*/raw-daily.parquet`、`daily.parquet`、完整 `reference.json` 保持可读，原文件与 SHA 不改。

新代际保留 `raw-daily.manifest.json`、`daily.manifest.json`、轻量 `reference.json`、公司行动及 `manifest.json`。真实压缩数据位于：

- `market-objects/raw-daily/<SHA前两位>/<SHA>.parquet`
- `market-objects/daily/<SHA前两位>/<SHA>.parquet`
- `market-objects/reference/<SHA前两位>/<SHA>.json`

日K按上海交易日期的月份分片，保留全部原字段/数值类型与精度；规范化日K保留原来的字段和未复权/CNY口径。单次有界分区 COPY 按月、完整证券 ID、日期排序，单写入线程生成确定性对象。首发编码器为 `market-monthly-zstd-rg8192-v1`，8192行组兼顾按股读取；编码器标识进入代际身份，不与此前试验的大行组输出混用。增量仍在唯一 worker 中合并重叠修订，但未变化月份生成相同字节和 SHA，复用既有对象，不向每个代际再复制一套五年数据。新月份或历史修订生成新对象，不覆盖旧对象。

逐股图表读者的固定代际只读连接限定为 `:memory:`，由实例原有可重入锁串行执行；只开启Parquet元信息缓存，关闭外部文件页缓存。连接随代际切换、显式关闭、全行情Arrow缓存接管或查询失败释放。轻清单结构最多缓存64份，每次仍检查实际分片签名和目录/文件符号链接；字节签名改变时重新核验SHA，不能因连接或清单缓存隐去来源篡改。

唯一 worker 的原始行情合并可生成仅在进程内传递的 `MergePartitionProof`，绑定合并文件SHA、合并前后逐个核验的旧原始对象SHA及新增/修订月份。合并过程中实际来源改变则拒绝发布。不得从任务参数、检查点或用户导入JSON反序列化此凭证。导入还须核验前代编码器/算法、已发布清单SHA、完整对象闭包及原始列类型；只有未变化月份的行数和起止日也完全相同，才跳过该月 COPY、直接引用原对象。历史修订与窗口裁剪仍重写受影响月份；无凭证、旧布局、类型变化、清单损坏或复用核验失败均走原完整写入路径。编码、行序、压缩等级与清单格式不变，不削减指标种子或来源保留范围。`storage_write` 的行情角色增加 `encoded_partition_count` 和 `reused_without_encoding_count` 计数，临时少写的字节不等于最终磁盘新增节省。

`daily.manifest.json` 的 `version=market-partitions-v1`，包含 role、rows、data_begin/data_end 和有序 partitions；每个分片保存 month、object_key、SHA、bytes、rows、日期边界。路径必须精确对应内容地址；同角色内月份不能重复或失序。查询支持 legacy 字符串路径及固定清单解析后的路径数组，不跟随另一个代际的 head。

`reference.json` 的 `storage_version=market-reference-parts-v2`。证券目录以列对象和明确证券键保存，重复的上市日期继承证据共享同值；四个指数日K按月保存；其他字段按内容对象保存。重建后的字段、值、缺失与 null 和原参考资料相同，不删除来源属性。兼容早期 `market-reference-parts-v1` 和未分片的普通 JSON。参考来源的前代 ID 是审计血缘，不等于求值时需要读取其文件；真正可读依赖以当前对象闭包和业务快照依赖为准。

代际 `summary.files` 包含全部共享对象、所有轻清单及本代专属文件；相同对象路径去重。SQLite 每个模块登记本模块清单和所需共享对象，仍先文件后成组切换 heads。`raw-daily.parquet`、`daily.parquet` 继续作为逻辑 role，实际 object_key 可指向对应 `.manifest.json`。下游必须通过统一解析器读取，不能直接拼单文件路径。

## 活动窗口与真实保留范围

每次成功发布根据行情业务日计算五个日历年的 `active_window`，不是机器日期、页面观察日或 1250 个任意交易日。字段为：

| 字段 | 含义 |
| --- | --- |
| `anchor`、`active_end` | 最近成功发布的业务日 |
| `active_begin`、`years` | 活动窗口边界及五年目标 |
| `physical_begin`、`physical_end` | 当前精确报价与种子实际仍保留的区间 |
| `protected_seed_begin`、`protected_seed_before` | 活动窗口外但为递推、完整周月和历史依赖保留的前缀；无额外前缀时为 null |
| `recursive_seed_preserved` | 当前始终为 true，不截断任意参数 MACD/KDJ 的初始状态 |
| `automatic_collection` | 当前为 false；对象回收只在明确的离线维护窗口执行 |

`data_begin` 和数据中心 `first_date` 继续表示实际文件覆盖，不改成已物理删除的假范围。`/data/status.summary` 和 bootstrap summary 增加 `storage_layout`、`retention`、`last_storage_write`，便于展示活动窗口与实际保护范围。指标前缀、历史事件、自选基准、划线来源、策略/跟踪、报告和可恢复任务不能仅因超出五年就删除。活动五年不是磁盘硬配额。

## 容量口径

`storage_accounting.categories` 增加 `shared_market`，只统计 `market-objects` 的实际普通文件一次。`current_market`、`old_market` 统计各代际专属清单和旧格式本体。其余 supplement、backtests、metadata_other 保留；六类和严格等于已发布目录的逻辑字节。没有权限、扫描失败、符号链接等边界保留原来的保守行为。

`storage_write` 的 `new_object_bytes`、`reused_object_bytes` 是本次写入/复用证据，不是内存消耗，也不能把每个引用的逻辑大小相加当作独占磁盘。参考资料共享的同一值可能被多个字段引用；容量仍按唯一对象文件计算。磁盘块、文件系统克隆、备份和历史业务引用会影响实际占用。

## 安全回收

新增 023 迁移仅创建 `market_storage_collection`、`market_storage_collection_file` 及不可改写身份/回执守卫，不自动删数据。离线 CLI 分开生成精确计划和执行：

1. 必须已停止目标 API 与唯一 worker；执行方还获取目标 work 的 worker 锁。存在 queued/running 任务时拒绝。
2. 保留全部 heads、有效 retention_pin、保存的策略/选股/回测/跟踪/自选/划线/研究等业务引用、真实 input_snapshots 传递闭包及默认最近两代。没有使用目录年龄作为授权。
3. 暂停初始化/增量精确读取该 job 的 `raw/sync/<job>/checkpoint.json`；题材任务读取 `work/<job>/topic-plan.json`。缺失、损坏、符号链接或过大检查点会保护全部代际，不能推断任务已不需要来源。
4. 候选代际不得含未登记文件、符号链接或 SHA/大小不符。共享对象只要仍被任何保留代际或已发布模块引用就不删。
5. 计划包含每个文件的精确路径、SHA 和字节，执行前重算引用和计划。先在短事务冻结授权、登记逐文件台账及 snapshot_gc=gc_pending；文件 I/O 在业务事务外执行，每次删除后刷目录并写回执。
6. 中断后仅能恢复原 collection。重新核对当前业务根和真实依赖闭包；出现新引用就停止，不能用旧计划覆盖新操作。
7. 完成后 snapshot_gc=purged，保留 dataset_snapshot、artifact、来源 SHA、计划与不可变删除回执。通用 validate 仅对完整、无新业务依赖、逐文件一致的已完成台账认可 authorized_removed；其他缺失仍失败。

此流程不清理业务回收站、不删除用户对象，不回收尚未登记发布的孤立 CAS，也不删除升级备份或真实原始响应。旧代被业务引用时继续占空间；“旧代”不等于“可删除”。

## 备份和转换

标准备份按代际 summary.files 和 SQLite artifact 的完整对象闭包去重复制，包含参考列/指数月份对象；没有到原数据目录的链接，恢复后可独立读取。任何缺失或 SHA 不符均拒绝，不把只含小清单的包称为完整备份。开始备份和读取其已冻结业务库时均检查未完成GC与 `gc_pending`；存在删除中间态时拒绝生成备份，先按原清单恢复维护。

`scripts/compact_market.py` 对明确不可变旧代构建新代，不修改原文件、业务或原报告；对原始与规范化日K进行双向 `EXCEPT ALL` 逐行对账。只有 `--register --offline-maintenance` 才在已恢复的独立候选库登记显式本地维护任务和新模块，不联网、不重放历史选股或提醒。来源数据根与候选根必须独立。转换不保证清除全部旧代，后续仍需按引用运行 GC。

补充 head 与市场代际身份绑定，登记新代后必须用本地原始证据重新核验并发布对应补充索引；不得盲改 snapshot_id 或假定补充自动跟随。正式升级仍须最终备份、独立恢复、自足校验、SHA/业务对账，再切换同版本 API/worker。
