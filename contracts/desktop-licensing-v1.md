# Windows 桌面离线授权与覆盖更新 v1

首发目标为 Windows x64。用户选择离线授权，卖方在自己的电脑发码，用户电脑本地验签。这条路径无需购买或部署授权服务器。此分支以已提交的 0.1.24 作为原型基线，正式出包使用届时确认的完整源码提交、迁移清单和 Windows 验收结果。

## 发码、到期和续费

1. **买家取机器码。** Windows 客户端生成随机安装身份，用 Windows DPAPI 当前用户范围密封在本地应用数据目录，显示机器码。机器码由随机身份的摘要产生，可以交给卖方。
2. **卖家发授权。** 卖方把买家机器码、订单号和授权类型输入本地工具，工具用 Ed25519 私钥签发授权码或文件。台账按订单记录机器码、类型、起止时间和序号。同一订单、机器码与类型的重试取回同一份授权，订单换机器码或类型时拒绝。
3. **买家导入授权。** 客户端用内置公钥验签并检查本机身份、授权序号和时间。别人拿到同一份授权，另一台电脑生成的机器码无法通过绑定检查。同一安装重新导入同一码保持原截止时间。
4. **到期续费。** 到期停止进入正式工作台和开始新付费任务，授权入口可查看机器码并导入新授权。业务库、历史及备份留在用户数据目录，保留恢复与来源授权允许的导出。收到绑定原机的月卡、年卡或永久授权后重新启用。任务开始时还需由本地 API 和唯一 worker 核验，已经提交的写入按原任务状态安全收尾。

| 类型 | 期限规则 |
| --- | --- |
| 1 天卡 | 24 小时 |
| 7 天试用 | 7 × 24 小时，卖方可用新订单重新签发 |
| 包月 | 30 × 24 小时 |
| 包年 | 365 × 24 小时 |
| 永久 | 无截止日，绑定原安装身份 |

有期限的授权均从该卡签发时起算。导入序号更新且有效的新卡后，客户端使用新卡的截止时间。卖方重试同一订单时取回原卡及原截止时间。时间使用 UTC 秒保存，界面按本地时区展示。在截止秒到期，系统时间明显回拨时要求校准。离线时间检查依赖本机时钟与已见时间，管理员修改程序或恢复整机快照仍可能绕过本机控制。

卖方工具选项 1 和命令行 `--plan daily` 签发 1 天卡。菜单按期限从短到长排列，直接回车使用选项 2 的 7 天试用。卖方台账记录 `daily`，对已发行客户端使用原有 `monthly` 签名标签和确切到期秒数。授权测试工具可能显示包月标签，托盘工作台显示实际到期时间。Mac 测试发卡器在 v1 台账第一次签发 1 天卡前保留独立校验的 SQLite 恢复副本，签发时台账迁移与插入在一个事务完成。

普通覆盖升级和同一 Windows 用户重装程序沿用原身份。清空 Windows 用户数据、系统重装及换机可能改变机器码。卖方核对原订单后人工处理。离线授权发出后无法远程收回，补发新机时旧机可能继续用到原截止日，永久卡的旧机授权长期有效。默认永久卡绑定原机，换机政策需在销售时说明。 [Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) · [Ed25519](https://cryptography.io/en/stable/hazmat/primitives/asymmetric/ed25519/)

## 客户端代码和信任边界

下载安装到客户电脑的代码可以被分析和修改。冻结的 Python 字节码仍可能被反编译，WebView 中的前端资源也可读。发行客户端入口独立于发卡工具，包中只交付验签所需的公钥及客户端产物。打包检查应排除源码、源映射、卖方台账、私钥和供应商凭据。必须保密的逻辑可以留在受控服务端，首期离线授权的防护范围是正常客户端对复制安装包和授权码的校验。 [PyInstaller 打包机制](https://pyinstaller.org/en/stable/operating-mode.html#hiding-the-source-code) · [Tauri 架构](https://v2.tauri.app/concept/architecture/)

卖方签名私钥和本地订单台账分开保存、备份。台账锁定对应验签公钥的 SHA。电脑更换后恢复原台账和原签名密钥，客户端授权公钥必须保持一致。发行版若轮换密钥，先交付支持旧签名过渡的客户端。原型命令接受显式公钥文件用于隔离测试，发行版须由签名安装包固定信任公钥。

## 覆盖更新与数据

用户双击新版安装包可以保留机器码、已购期限和原数据。新版程序写入独立版本目录，SQLite、published、raw、work、配置及 AI 历史放在可写用户目录，密文与外置主密钥分开。DPAPI 授权文件位于同一 Windows 用户的本地应用数据目录，在程序版本间共享，安装器不重新签发或延长授权。

1. **准备。** 验证 Windows x64 安装包及签名清单。清单记录产品标识、完整源码提交、包与运行时摘要、支持升级的 schema 与迁移 SHA。核算预演、最终副本、备份、压缩和独立解压的峰值空间。
2. **预演。** 用标准业务备份及必要 raw/work 证据恢复隔离副本。候选只启动本地 API，关闭 AI 和自动计划，核对业务、迁移 SHA、已发布来源、附件与凭据。
3. **切换。** 在维护窗口等待旧进程安全退出，确认旧 API、唯一 worker 和计算子进程释放文件锁。以停机时的最新数据重做最终备份。在另一个独立候选库迁移、核验后，原子切换程序与数据根配对。新 API 与唯一 worker 同版启动。
4. **回滚与收尾。** 新版开放写入前可恢复旧配对。开放写入后先保护新记录和费用台账，再用最终备份恢复旧版并对账。成功后制作完整压缩恢复集，独立解压、逐文件 SHA 和业务核对通过后，清理已覆盖旧集与无引用临时文件，记录同一文件系统的实际可用空间。 [版本升级与回滚](../docs/版本升级与回滚.md)

安装回执记录暂存、停旧进程、最终备份已核验、候选已核验、已切换、已开放写入及已归档状态。重复安装和中断后按回执恢复，旧版只读取对应旧恢复库。到期仍可升级程序、补发授权和执行备份恢复。

## 原型运行和验收

从开发分支的 backend 运行。以下 PowerShell 使用临时目录及测试订单。先在买家 Windows 终端运行机器码命令，把输出的机器码填入卖方终端。卖方工具也可以在 Mac 或 Linux 运行。

```powershell
$lab = Join-Path ([System.IO.Path]::GetTempPath()) ("admr-license-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $lab | Out-Null
uv run --locked python -m desktop_licensing.windows machine-code --state-dir (Join-Path $lab "device-a")
$key = Join-Path $lab "issuer.pem"
$pub = Join-Path $lab "verify.pem"
$db = Join-Path $lab "issuer.sqlite3"
$license = Join-Path $lab "trial.amr-license"
uv run --locked python -m desktop_licensing keygen --private-key $key | Set-Content -Encoding ascii $pub
uv run --locked python -m desktop_licensing init --db $db --private-key $key
$machine = Read-Host "粘贴买家机器码"
uv run --locked python -m desktop_licensing issue --db $db --private-key $key --plan trial --order-ref test-trial --machine-code $machine --output $license
uv run --locked python -m desktop_licensing.windows activate --public-key $pub --license-file $license --state-dir (Join-Path $lab "device-a")
uv run --locked python -m desktop_licensing.windows check --public-key $pub --state-dir (Join-Path $lab "device-a")
uv run --locked python -m pytest -q tests/test_desktop_licensing.py
```

这里只在同一隔离环境验证流程，正式交付仅传给买家授权文件，验签公钥由签名安装包固定。也可省略授权文件参数并在隐藏输入提示粘贴授权码。check 返回 0 表示有效，3 表示未激活、到期或需校准时间，2 表示文件或签名等校验错误。卖家用 info 查订单。第二台设备先生成自己的机器码，再导入同一授权，应被拒绝。

本轮已实现离线发卡、本地验签和到期准入检查。在 Mac 验证了签名、到期、续期、普通跨设备重用及程序替换后的状态保留，并用隔离库演练了 schema36 到 39 的恢复迁移和回滚。Windows 原生 DPAPI 及两台真机验收、主程序的 API 和 worker 授权准入、签名安装器和覆盖更新编排仍待接入。最终 Windows 包以用户正在开发的功能完成后的指定提交为基线。

首个工作台预览以 b3454f2 为业务基线，在独立分支对接桌面进程与授权准入。测试包沿用前一个授权测试的公钥和身份目录，工作台业务数据单独保存。预览启停、数据围栏与验收步骤见 [Windows 桌面预览](../docs/Windows桌面预览.md)。

托盘预览沿用首版的验签公钥和 DPAPI 目录。买家从同一个工作台 EXE 获取机器码、导入授权文件和续期。Windows 托盘控制本机服务生命周期，默认浏览器仅用于访问页面。每次重新打开通过短时的一次性回环票据建立或恢复浏览器会话。首版数据可在旧版停止后通过独立恢复验收导入托盘版，详见 [Windows 桌面预览](../docs/Windows桌面预览.md)。
