# 自动午评与盘后任务

## 授权与范围

竞价、午评和盘后计划安装默认均关闭。用户在数据中心显式保存启用设置后，唯一worker可以创建当日竞价、午评及盘后增量。初始化、研究仍由用户手动创建。API导入、服务GET和设置保存均不执行取数或调度；现用环境不会因源码更新自动启用。

默认北京时间午评12:05、盘后17:00；已保存的自定义时间保持原值，不因默认时间调整而强制覆盖。午评仅11:31至13:00前，盘后可配置17:00至当天23:59。关机后同日有效窗口内重启可补交当日任务；跨零点不追历史，午评13:00后不补造午间行情。忙碌时在窗口内等待，错过窗口则不补。

## 设置接口

- `GET /api/v1/settings/automatic-tasks`：在同一SQLite读事务中返回午评、盘后小设置及关联job状态，另含`auction`竞价设置投影；不读行情文件、不联网、不创建任务。
- `PUT /api/v1/settings/automatic-tasks`：完整提交`noon`、`after_close`两项；可携带`expected_revision`。这两项仅接受严格布尔`enabled`及北京时间`HH:mm`形式的`time`。午评时间为11:31≤time<13:00，盘后为17:00≤time≤23:59。
- 同一PUT可附`auction:{enabled:boolean}`及必需的`expected_auction_revision`。竞价不接受时间、日期、单次模式或手填代码；固定北京时间09:25:30至09:30前，范围由worker自动冻结。旧客户端只交午评、盘后时保持兼容，不修改竞价设置；孤立提供竞价修订号返回422。
- GET另返回`auction_discovery:{enabled:boolean,revision:int}`。PUT可附`auction_discovery:{enabled:boolean,expected_revision:int}`。它默认关闭，开启时须有已开启的每日竞价授权，两者可在同一事务保存。修改前校验独立修订号，关闭会撤销该修订号下尚未执行的补充请求。旧客户端省略该项时保留现值。恢复副本关闭该项并递增修订号。
- 午评/盘后共用顶层`revision`，竞价使用独立`auction.revision`。携带竞价时，两个版本检查及三项保存位于同一SQLite短写事务；任一版本冲突或校验失败，全部回滚。顶层`expected_revision`保持旧请求的可选兼容；当前页面同时提交读取到的两个修订号。
- 各自配置实际变化才递增对应revision；相同设置保存保持版本。不匹配版本返回409，非法字段或时间返回422。设置请求沿用增量期间全站锁，不提供绕过。

响应示例中的时间仅为契约示意，产品时间取服务器实际北京时间：

```json
{
  "timezone": "Asia/Shanghai",
  "now": "2026-10-29T12:00:00+08:00",
  "revision": 0,
  "noon": { "enabled": false, "time": "12:05" },
  "after_close": { "enabled": false, "time": "17:00" },
  "last_checks": { "noon": null, "after_close": null },
  "auction": {
    "enabled": false,
    "revision": 0,
    "mode": "off",
    "can_enable": false,
    "reason": "请先完成行情初始化，再开启自动竞价",
    "fixed_time": "09:25:30",
    "window_end": "09:30:00",
    "next_run_at": null,
    "last_run": null,
    "worker": { "alive": false, "last_heartbeat_at_ms": null },
    "legacy_configuration": false,
    "legacy_instrument_count": 0
  },
  "policy": {
    "check_interval_seconds": 60,
    "noon_window_start": "11:31",
    "noon_window_end": "13:00",
    "after_close_not_before": "17:00",
    "catchup": "仅当天有效窗口补跑；跨零点不追历史，午评13:00后不补",
    "calendar_requests_per_day": 1
  }
}
```

`policy`还有初始化、停止任务和结果确认说明；客户端展示后端说明，不据此自行计时创建任务。`last_checks`每项为null或`{state, reason, date, task_id, checked_at, task_status}`。state可为`blocked/waiting/missed/calendar_unknown/not_trading/claiming/submitted/existing/settings_changed/failed`；task_status从实际job重读，缺失保持null。checked_at是该状态最近变化时间，不用周期性写时间制造进度。不声明尚未核验交易日历的精确next_run时间。

一次统一保存的合成请求示例（使用刚读取的两个修订号，不根据本地时间生成版本）：

```json
{
  "expected_revision": 0,
  "expected_auction_revision": 0,
  "auction": { "enabled": false },
  "noon": { "enabled": false, "time": "12:05" },
  "after_close": { "enabled": false, "time": "17:00" }
}
```

`auction`投影包含`enabled,revision,mode,can_enable,reason,next_run_at,last_run,worker,legacy_configuration,legacy_instrument_count,fixed_time,window_end`。旧单次或手填名单配置只标识历史遗留，不驱动新任务；重新保存每日开关才进入当前规则。竞价的固定窗口、有界自动重试和旧快照兼容见[竞价契约](auction-snapshots.md)。

原`GET /data/status.automatic_schedule`保持布尔类型，取午评、盘后和有效每日竞价三项实际启用状态的OR；旧单次/手填名单不算启用。

## 调度、幂等与停止边界

- 调度器只在已持有唯一worker锁的主循环中运行，单调时钟限流为最多每60秒读取一次设置；关闭或尚未到点时不访问市场、目录、日历或网络。
- 到点后先核验初始化、当日既有任务、上次增量确认锁与当前队列。已运行、排队、成功、部分成功、无变化、失败、暂停或取消的同日任务均沿用原记录，不重新创建。未初始化不自动初始化；已有任务先处理，调度不抢占。
- 仅启用且到点、队列空闲时，唯一worker请求扶摇正式交易日历；不按星期猜节假日。日历最多4096条，自动检查每天最多1次实际HTTP尝试，午评与盘后共享这份有来源SHA的小缓存；失败或进程中断当天不连续重试。此预算只属于调度前置检查，原午评及增量采集自身仍按冻结任务核验来源。
- 当前日期确在正式交易日历时才提交。日历未覆盖当前日期保留unknown，不把日历末尾之后的日期推断为休市。休市无任务；读取配置不触发上述日历请求。
- SQLite短事务认领120秒有界提交租约，固定日期/类型request_key跨重启复用。提交前在真正job写事务中再次核验配置版本、开关、当前北京时间窗口和同日手动请求，防止关闭、改时间或跨13:00/零点后的迟到提交。租约中断只恢复尚未落地的提交；已有job优先关联，不再运行。
- 自动午评直接复用原一次性午评public creator及每日台账；自动盘后与手动HTTP共用`sync_requests.create_sync_request`，保留17:00最早创建边界、全部增量参数、冻结链、保留规则及`increment_session`全站锁。初始化和旧冻结任务范围不变。
- 自动计划不直接执行策略。盘后后续仍按原策略“同步完成后重新筛选”和独立的“自动加入跟踪池”设置执行。午评及盘后失败、暂停、取消均由用户通过原任务入口处理，不自动重试或继续；关闭计划也不擅自取消已提交任务。
- 自动盘后完整结果仍需用户确认。未确认上轮结果会阻塞下一轮自动任务，不替用户确认或清除锁。常驻前端沿用`GET /sync/session`及409中的increment_session发现后台任务，运行期间全站操作锁与完成确认不变。

## 存储与验证

午评及盘后沿用既有platform_setting，使用三份固定小键：`automatic-review-config-v1`、`automatic-review-state-v1`、`automatic-review-calendar-v1`；不按日期无限新增设置行。原job、午评台账和job_event保存任务及授权依据，自动事件记录计划版本、日期、时间与日历来源SHA。竞价继续使用既有031配置表及日运行表，两处配置不复制、不混用修订号；本轮统一设置不新增migration。无Key进入设置、任务证据或日志。

合成验证覆盖开关和版本并发、同日幂等、手动竞争、关闭后迟到提交、时间窗口和时区、节假日/周末、日历缺口和请求失败、重启认领窗口、原停止状态、完整增量锁及worker领取。低占用计时只测合成clock/disabled/not_due路径的CPU和tracemalloc分配峰值，不代表真实取数耗时或服务常驻RSS。

标准恢复关闭三类自动开关、增加各自合法修订号，保留自定义午评/盘后时间、历史日账及检查回执；原排队/运行任务按既有规则暂停，不自动复活。缺失配置不凭空开启，畸形配置拒绝恢复目标。

### 采集防误触与进度展示

盘后增量、盘前竞价和午评运行时不提供暂停/取消；任务记录与HTTP控制入口同时执行此限制。保留当前阶段、已耗时、历史样本支持的预计剩余范围与结果查看；样本不足不编造倒计时。关闭浏览器不停止已提交任务，关闭自动计划只影响未来触发。旧版/恢复副本中已保存的暂停、取消状态仍保留，按原规则处理，不自动复活。
