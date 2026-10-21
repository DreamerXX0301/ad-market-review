# 冒险的梦想家

**在本地完成 A 股盘面研究、策略筛选与持续跟踪。**

从指数与市场情绪出发，查看行业、概念和个股走势，组合自己的日 / 周 / 月策略，再把候选加入独立跟踪池。行情、计算依据与业务记录保存在本地，数据缺口有明确提示。

项目仓库：[DreamerXX0301/ad-market-review](https://github.com/DreamerXX0301/ad-market-review) · [版本记录](CHANGELOG.md) · [使用与数据边界](docs/实现范围与数据边界.md)

## 主要功能

| 模块 | 功能 |
| --- | --- |
| 盘面研究 | 指数、市场广度与情绪、行业题材、涨停梯队、30交易日涨幅榜、异动与资金观察，支持按已留存日期回看 |
| 日 / 周 / 月 K | MA5/10/20/30/60/250、成交量、MACD、KDJ、十字线、缩放、涨跌停事件、转折候选结构和全局图表偏好 |
| 自定义策略 | 个股与板块独立管理；条件可分别选择日、完整周、完整月，支持嵌套逻辑及个股“历史事件＋当前条件” |
| 策略跟踪 | 默认30个市场交易日，提醒与结束条件分别设置，查看触发列表、跟踪涨幅和历史轮次；板块可展开成分股 |
| 分类与搜索 | 题材库交并集查股、人工归属与修订记录；统一搜索股票、指数、行业和概念 |
| 个股研究 | 年内 / 30交易日涨幅、年内涨停次数及最近确认日期；指定标的后手动查询研究材料 |
| 回测 | 独立区间、七项统计、权益 / 回撤曲线及交易、订单、成交与持仓记录，运行前检查数据和条件可用性 |
| 数据管理 | 默认近五年初始化、板块 K 全库首次同步与每日尾部增量、真实进度 / 日志 / 历史、暂停和继续、数据源验权 |

## Docker 安装

需要 Docker Engine / Docker Desktop、Compose 和 curl。以下流程安装 `ghcr.io/dreamerxx0301/ad-market-review:v0.1.1`，只下载部署文件并拉取镜像，无需在本机构建。已有安装请先看[升级与回滚](docs/版本升级与回滚.md)。

```bash
mkdir ad-market-review
cd ad-market-review

curl -fL https://raw.githubusercontent.com/DreamerXX0301/ad-market-review/v0.1.1/deploy/docker-compose.yml -o docker-compose.yml
docker pull ghcr.io/dreamerxx0301/ad-market-review:v0.1.1

umask 077
key_dir="$HOME/.local/share/ad-market-review-secrets"
mkdir -p -m 700 "$key_dir"
docker run --rm --user 0:0 --network none \
  --mount "type=bind,src=$key_dir,dst=/secrets" \
  --entrypoint python ghcr.io/dreamerxx0301/ad-market-review:v0.1.1 \
  /app/deploy/create_secret.py /secrets/master.key
export SHIXU_MASTER_KEY_FILE="$key_dir/master.key"
export SHIXU_HTTP_PORT=8788

docker compose -p ad-market-review -f docker-compose.yml pull
docker compose -p ad-market-review -f docker-compose.yml up -d api
```

打开 [http://127.0.0.1:8788](http://127.0.0.1:8788)，确认页面正常后启动唯一 worker：

```bash
docker compose -p ad-market-review -f docker-compose.yml --profile worker up -d worker
```

服务只绑定本机。若8788已有实例，请使用新的安装目录、项目名及空闲端口，不共享现用数据卷。主密钥单独保存，恢复旧凭据必须使用原主密钥。需要固定镜像摘要时可通过 `SHIXU_IMAGE_REF` 覆盖默认镜像；更新实例按 `compose.release.yaml` 的摘要流程进行。完整安装和配置见[Docker部署](docs/Docker部署.md)，备份与进程管理见[部署与运维](docs/部署与运维.md)。

## 开始使用

1. **配置数据源**：填写自己的 Key，验证行情、板块、成员与材料等实际能力，详见[数据源与权限](docs/数据源与权限.md)。
2. **手动初始化**：在数据中心同步主行情、分类成员、板块历史 K 和盘面补充。默认目标近五年，实际范围以来源与页面覆盖提示为准。
3. **盘后更新**：北京时间18:00后手动运行增量。板块首次全库完成后无需重复初始化，后续追加尾部；暂停 / 取消由用户决定是否继续。
4. **运行策略并跟踪**：选择个股或板块，配置条件，核对运行依据，再将命中加入对应跟踪池。板块成分股浏览不会自动创建个股跟踪。

盘面日期只影响盘面研究；策略、跟踪、研究与回测使用各自业务日期或独立区间。当前成员的采集日与行情日分别标注，历史成员缺档不以当前名单回填。

## 数据说明

历史长度、来源权限、停牌和缺报会限制指标与回测。涨停覆盖不足时保留确认下界，未知不补0；图表价格和指标显示设置不会修改策略信号。当前提供盘后研究，不包含实盘自动下单；筹码价格档和连续封单稳定度尚未接入。完整口径见[实现范围与数据边界](docs/实现范围与数据边界.md)。

## 源码运行与开发

需要 Node.js 22.12+、npm、uv 和 Python 3.13–3.14；Python 的 SQLite 运行库须通过程序版本检查。

```bash
./scripts/dev.sh
```

该脚本安装依赖、执行迁移、构建前端并启动 API 与单 worker。源码开发、测试及模块说明见[前端说明](frontend/README.md)、[贡献指南](CONTRIBUTING.md)和[交付与验收](docs/交付与验收.md)。

| 文档 | 内容 |
| --- | --- |
| [数据源与权限](docs/数据源与权限.md) | 来源接入、账户能力和数据授权 |
| [Docker部署](docs/Docker部署.md) | 镜像拉取、新建安装和配置说明 |
| [部署与运维](docs/部署与运维.md) | 安装、配置、进程和备份 |
| [版本升级与回滚](docs/版本升级与回滚.md) | 独立候选验证、维护窗口切换和恢复 |
| [实现范围与数据边界](docs/实现范围与数据边界.md) | 日期、指标、价格、历史成员与回测口径 |
| [架构](docs/架构与模块边界.md) / [数据库](docs/数据库与数据流.md) | 代码模块、存储和数据流 |
| [贡献指南](CONTRIBUTING.md) / [安全说明](SECURITY.md) / [版本记录](CHANGELOG.md) | 协作、问题报告与版本变化 |
