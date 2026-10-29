# 手动盘后增量的阻塞进度

`GET /api/v1/sync/session?task_id=...` 或 `?request_key=...` 只读查询用户已经提交的一次 `increment`。不创建、恢复、重试或取消任务，不取行情。尚未找到404，非increment任务422。不带标识时返回 `{active: null|receipt}`，供首次挂载、新标签页、清空浏览器存储后的全局恢复；只读查询一个持久会话指针，不扫描行情目录。

响应包含 `root_task_id/request_key/status/can_acknowledge/created_at_ms/observed_at_ms/finished_at_ms/elapsed_ms/tasks/waiting_followup/issues/estimate`。`tasks` 是至多32个去重任务的小型回执，不包含完整参数、凭据和日志。通过持久化的 `followup_queued/line_alert_check_queued/listing_status_check_queued` 事件追踪已授权子任务；主行情成功而后续尚未排队时保持 `awaiting_followup`，不能提前称整个增量成功。主行情发布后的本地策略核对仍包含在等待后续阶段内。

0.1.15新建increment冻结`workspace_followup_version=after-data-chain-v1`；原冻结任务不补此字段或扩大范围。该相位必须核验原根的冻结目标日、主行情代际和发布回执、原链接板块批次完整请求回执及本次supplement的job/date/snapshot/SHA。数据链未结束保持waiting，不读旧日补充替代。本次目标日有已发布的合法partial输入时由具体规则保留unknown；整日目标池缺失、来源损坏、必要子任务失败或已非当前业务日则blocked。

数据发布核验后才执行原策略开关授权的选股与跟踪，成功写`workspace_followup_completed`后接续划线提醒；`workspace_followup_started`尚未有`workspace_followup_released`时，相位仍视作running，不能通过SQL完成状态或提前COMPLETE确认解锁。暂停/取消持续等待真实finally释放；worker异常退出后仅由取得唯一worker锁的新进程补记旧执行退出，不恢复显式停止。重启通过原request_key与已有run/轮次幂等修复中断，失败计算保留阻断回执；有效停止的旧根在LIMIT前排除，避免占满有界恢复候选。

只展示当前实际任务进度；任务总数会随已授权子任务排队增加，因此不把主行情100%当作整链100%。全部已授权任务结束才返回 `can_acknowledge=true`；失败、暂停、取消、排队受阻均明确展示后允许确认离开，不自动继续。仍有子任务运行时继续等待。`partial` 表示执行结束但来源仍有缺口，不等于数据完整。

预计时间来自最近最多12次同 `data_chain_version`、整条链已完成的增量（含排队和后续阶段），至少3个正耗时样本才显示历史四分位区间及中位参考结束时刻。样本不足、超出历史区间或读状态失败时不编造倒计时。它是耗时参考，不是完成承诺。

前端在提交前开启阻塞层，使用相同幂等请求标识。仅保存不含个人设置的请求/任务ID和界面状态；刷新及同源标签页恢复该层，仅GET轮询，不重发POST。背景工作台 `inert`，锁定页面滚动、焦点和Escape；任务结束后须明确点击确认。服务端拒绝或请求持续未确认有独立核查出口，不将其标作成功，不悄悄重试任务。关闭浏览器不取消服务端任务。普通页面浏览不自动启动增量。

活动查询每2秒一次，不重叠；隐藏页面降低频率。估计样本在API进程有界缓存30秒，不扫描行情文件。实际取数预算、来源限速、唯一worker与人工启动约束保持原有契约。

## 服务端锁定与确认

响应另含 `locked/session_epoch/acknowledged/draining_task_ids`。会话标识、确认和安全停止存于业务SQLite的 `platform_setting/job_event`，不新增schema、不保存凭据。

`POST /api/v1/sync/session/acknowledge` 接受 `request_key/task_id/session_epoch`。短写事务重新核对当前根、会话轮次和整条链的实际结束状态，通过后持久记录确认并移除锁。旧根或旧轮次不能解锁新会话；链仍运行、仍等待已冻结后续阶段或工作进程尚未安全释放时返回409。另一个标签页确认后，原具体会话查询返回 `acknowledged=true, locked=false`，浏览器同步收敛。

`POST /api/v1/sync/session/control` 接受 `task_id/session_epoch/action`，action仅支持 `pause/cancel`。停止本条已有数据链，不新建、恢复或重试取数；尚未排队的后续阶段不再派发，停止竞态中迟到的子任务在创建事务内直接保持停止。运行步骤先显示 `stopping`，等真实worker finally回执才允许确认。worker异常退出时，只能由新worker取得既有唯一执行锁后证明旧执行已退出并补记释放，不能用清空lease或超时猜测停止。queued步骤可直接停止；显式resume/retry重新建立同链锁，不自动继续。

整根increment重试创建新increment任务与独立根。单个子步骤的手动恢复/重试以 `attempt_task_id` 冻结本次分支：历史根仅保留来源身份，`historical_attempt=true` 的旧失败不参与本次进度和结果；UI明确称“本次重试步骤/分支”，不能称原整链已完整成功。后续权限依赖原持久链接及内部派发父步骤，子分支的新epoch只解除该分支的停止；原根、兄弟步骤的显式停止继续有效，不能因重试本地核验便自动启动未建的板块阶段。

若本地仅持有请求标识且尚无已接收task_id，404或等待30秒都不能解锁。用户可显式调用确认接口核查并关闭未接收请求：事务证明无对应job、无当前会话后写该request_key的放弃墓碑；迟到的原POST必须在创建任务的事务内核对墓碑并拒绝，确认成功后才清本地层。已知task_id却真实缺失时保持未知，要求核查服务端存档；断网或确认请求失败保持锁定。

API中间件在锁定时拒绝所有新的业务GET/写请求并返回409与当前会话身份。静态资源、SPA、health、会话进度/确认/停止和同链任务读取及pause/cancel保持可达；`POST /sync/tasks`只允许相同已接收request_key的幂等重读。统一任务创建在 `BEGIN IMMEDIATE` 内再次校验HTTP会话锁，防止提前通过中间件的选股或批量请求趁并发窗口新排队。worker不处于HTTP上下文，不会锁死自己的授权链或心跳；不强杀已接收任务，增量沿原唯一worker规则排队。开始锁定前已接收的HTTP读取可能正常完成，此门禁不追溯取消它们。

## 更新完成后的重复请求

`GET /data/status` 新增 `sync_operations.increment/supplement`，各含 `completed/reason/task`。主行情按北京时间盘后日期核验已发布终态，盘面补充按行情代际、目标日期及覆盖范围核验成功回执；不存在、失败或partial补充不填成功。`POST /sync/tasks` 与 `/sync/market-supplement` 在重复成功范围返回409与 `detail.message/completed_task_id`，重复同request_key的任务创建仍先幂等读取。扩大补充范围、明确核验计划、离线重验及失败恢复保持原范围。新补充回执存业务SQLite platform_setting并以真实job终态再次核验，不增加schema；旧版完整成功索引可只读识别。文件取证在事务外，创建事务内只读SQLite回执。

新手动初始/增量/补充冻结 `fund_flow_policy=fund-flow-retired-v1` 并逐级传递：不再请求行业资金历史；旧冻结任务不增删范围。资金历史不删除，旧只读API继续兼容。
