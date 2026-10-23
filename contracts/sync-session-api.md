# 手动盘后增量的阻塞进度

`GET /api/v1/sync/session?task_id=...` 或 `?request_key=...` 只读查询用户已经提交的一次 `increment`。不创建、恢复、重试或取消任务，不取行情。缺少标识422，尚未找到404，非increment任务422。

响应包含 `root_task_id/request_key/status/can_acknowledge/created_at_ms/observed_at_ms/finished_at_ms/elapsed_ms/tasks/waiting_followup/issues/estimate`。`tasks` 是至多32个去重任务的小型回执，不包含完整参数、凭据和日志。通过持久化的 `followup_queued/line_alert_check_queued/listing_status_check_queued` 事件追踪已授权子任务；主行情成功而后续尚未排队时保持 `awaiting_followup`，不能提前称整个增量成功。主行情发布后的本地策略核对仍包含在等待后续阶段内。

只展示当前实际任务进度；任务总数会随已授权子任务排队增加，因此不把主行情100%当作整链100%。全部已授权任务结束才返回 `can_acknowledge=true`；失败、暂停、取消、排队受阻均明确展示后允许确认离开，不自动继续。仍有子任务运行时继续等待。`partial` 表示执行结束但来源仍有缺口，不等于数据完整。

预计时间来自最近最多12次同 `data_chain_version`、整条链已完成的增量（含排队和后续阶段），至少3个正耗时样本才显示历史四分位区间及中位参考结束时刻。样本不足、超出历史区间或读状态失败时不编造倒计时。它是耗时参考，不是完成承诺。

前端在提交前开启阻塞层，使用相同幂等请求标识。仅保存不含个人设置的请求/任务ID和界面状态；刷新及同源标签页恢复该层，仅GET轮询，不重发POST。背景工作台 `inert`，锁定页面滚动、焦点和Escape；任务结束后须明确点击确认。服务端拒绝或请求持续未确认有独立核查出口，不将其标作成功，不悄悄重试任务。关闭浏览器不取消服务端任务。普通页面浏览不自动启动增量。

活动查询每2秒一次，不重叠；隐藏页面降低频率。估计样本在API进程有界缓存30秒，不扫描行情文件。实际取数预算、来源限速、唯一worker与人工启动约束保持原有契约。
