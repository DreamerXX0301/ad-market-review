# Docker部署

本文对应冒险的梦想家0.1.3发行，源码以 [v0.1.3 标签](https://github.com/DreamerXX0301/ad-market-review/tree/v0.1.3)为准，镜像为 `ghcr.io/dreamerxx0301/ad-market-review:v0.1.3`，目标平台为 Linux amd64、arm64。使用安装命令前，须核对[本版 Release](https://github.com/DreamerXX0301/ad-market-review/releases/tag/v0.1.3)与[镜像发行工作流](https://github.com/DreamerXX0301/ad-market-review/actions/workflows/publish-image.yml)的实际结果；完整源码 SHA、镜像摘要及平台检查以本版 `release-image.json` 为准，不沿用旧版提交或成功记录。无需在安装机器构建前后端。

[0.1.3](../CHANGELOG.md#v013)在0.1.2基础上增加自动只读主线、监管最新/历史名单切换和明确的板块成员口径，股票反馈统一红涨绿跌；浏览只读取已发布资料，缺档保留未知。schema仍为023，无新增迁移。合入或推送 `master` 不会更新既有镜像；已有安装按[版本升级与回滚](版本升级与回滚.md)使用独立恢复副本验收后切换。[0.1.2历史发行](../CHANGELOG.md#v012)保留原源码与镜像标签，不重新覆盖。

## 全新安装

需要 Docker Engine 或 Docker Desktop，以及 Docker Compose v2；`curl`仅用于可选健康检查。以下命令面向全新安装；已有实例应先阅读[版本升级与回滚](版本升级与回滚.md)，不要直接复用旧实例的卷。默认只绑定本机 `127.0.0.1:8788`；已有服务占用时先设置 `export SHIXU_HTTP_PORT=8789`。

镜像允许匿名拉取，不要求GitHub登录；源码仓库和Release附件则为私有资源，需要授权。下载独立Compose文件须安装GitHub CLI（`gh`），使用已有的授权登录并具备仓库读权限，再从[0.1.3 Release附件](https://github.com/DreamerXX0301/ad-market-review/releases/tag/v0.1.3)获取，不必克隆仓库。匿名raw链接不能用于取得这些私有文件。

```bash
mkdir ad-market-review
cd ad-market-review
gh release download v0.1.3 --repo DreamerXX0301/ad-market-review --pattern docker-compose.yml
docker pull ghcr.io/dreamerxx0301/ad-market-review:v0.1.3
```

下载命令不覆盖已有同名文件；若文件已存在，先核对其版本，或使用新的安装目录。已持有对应发行源码时，也可将其中的 `deploy/docker-compose.yml` 复制到新安装目录并命名为 `docker-compose.yml`，跳过附件下载命令，再继续后续步骤。GitHub授权只用于私有源码或附件访问，不需要把该授权配置为镜像登录。

创建一个新的、独立保存的主密钥。镜像中自带生成脚本，不要求宿主安装 Python；脚本遇到已有文件会拒绝覆盖。Docker Desktop 需允许挂载此目录。

```bash
umask 077
key_dir="$HOME/.local/share/ad-market-review-secrets"
mkdir -p -m 700 "$key_dir"
docker run --rm --user 0:0 --network none \
  --mount "type=bind,src=$key_dir,dst=/secrets" \
  --entrypoint python ghcr.io/dreamerxx0301/ad-market-review:v0.1.3 \
  /app/deploy/create_secret.py /secrets/master.key
export SHIXU_MASTER_KEY_FILE="$key_dir/master.key"

docker compose config
docker compose pull
docker compose up -d api
```

如果目录已存在，不要为了继续命令删除其中的密钥；先核对是不是原安装。原数据源密文需要原主密钥，重新生成不能解密它。主密钥不要放进源码、命名数据卷或公开备份。

打开 `http://127.0.0.1:8788`，或先用 `curl -fsS http://127.0.0.1:8788/health` 检查应用名与版本。确认页面可用，再显式启动唯一 worker：

```bash
docker compose --profile worker up -d worker
docker compose --profile worker ps
```

`up -d api` 只运行卷初始化与 API，worker 默认不启用。启动 worker 也不会自动创建首次行情任务；在页面配置数据源，再手动初始化。之后盘后增量由用户在北京时间18:00后发起。不要另开第二个 worker 或让另一项目共用此业务库。

每次在新终端执行 Compose 命令，需要重新设置 `SHIXU_MASTER_KEY_FILE`。默认项目名是 `ad-market-review`，不会沿用开发入口原有项目名。内部 `SHIXU_*` 配置、卷名和容器数据路径保留兼容，不需自行批量改名。

## 镜像与数据

版本标签方便首次安装；运行记录应保留真实摘要。发行 workflow 的 `release-image.json` 给出 `pull_reference`，可据此固定内容：

```bash
export SHIXU_IMAGE_REF='ghcr.io/dreamerxx0301/ad-market-review@sha256:实际发行记录中的64位摘要'
docker compose config
```

此行包含占位说明，须替换成真实摘要后执行。Compose 会按机器选择 amd64 或 arm64，无需强制 `platform`。GHCR镜像允许匿名访问，拉取镜像不要求GitHub登录；这不代表私有源码和Release附件也能匿名下载。每次新发行须独立核验精确摘要的匿名访问；索引、配置读取或层HEAD检查不能代替完整镜像拉取，回执应分别记录实际完成范围。若镜像返回拒绝访问，先核对版本与包权限，不应把数据源 Key 用作镜像登录密码。[GitHub Container Registry 权限与匿名拉取](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

业务、已发布行情、原始响应、worker 工作文件和密文凭据分别放在命名卷。API 对已发布行情只读，使用独立可丢弃 tmpfs 计算派生缓存，不打开 worker 的活动 DuckDB。`docker compose down` 保留数据；**不要加 `-v`、执行卷清理或覆盖原主密钥**。生产备份、迁移与回滚参见[版本升级与回滚](版本升级与回滚.md)。当前入口只监听本机，远程访问需要自行配置认证与反向代理。

## 维护者发行流程

`.github/workflows/publish-image.yml` 只接受人工 `workflow_dispatch`，限定本仓库 `master`，并要求确认上传。workflow 必须先合入默认分支；版本源码标签及配套Release附件也应绑定同一审核提交，私有附件的下载仍需仓库读权限。[手动事件的默认分支要求](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)

已有 `v0.1.2` 及更早版本不能用于重新发布当前新增功能，也不能删除或覆盖旧版本标签来绕过门禁。本次选定 `0.1.3`；发行前须确认该版本的 Git 源码标签与镜像标签均未占用，同步根 `VERSION`、前后端版本与锁文件、运行版本、Docker/Compose默认版本、workflow输入默认值及安装文档，审核后合入 `master`。下面命令仅用于已明确授权的发行，版本取自该已审定源码；执行前核对完整提交，避免把后续变动一起发出。

```bash
gh workflow run publish-image.yml --repo DreamerXX0301/ad-market-review --ref master -f version="$(cat VERSION)" -F confirm=true
```

流程先复用只读 CI，核对根 `VERSION`、前后端版本及完整 Git SHA；之后在原生 `ubuntu-24.04` 与 `ubuntu-24.04-arm` 分别构建。每个平台在隔离空目录运行 API 健康、品牌、版本和首页检查，amd64 另跑14项独立空卷验收；成功后才上传各平台完整提交标签。两个构建都成功，才按精确子镜像摘要创建版本标签与提交标签，已有版本标签拒绝覆盖。没有 `latest`、自动部署或本机服务操作。[Docker 原生多平台构建](https://docs.docker.com/build/ci/github-actions/multi-platform/)

只在需要上传的 job 授予 `packages: write`，用仓库的 `GITHUB_TOKEN` 登录，不要求用户提供新的个人包 Token。OCI labels 和发行 artifact 留存版本、提交、平台摘要与总摘要；Actions 绑定完整提交。首次创建的 GHCR 包可能是 private，仓库维护者须明确设为 public，再独立验证匿名拉取，不能仅凭 workflow 上传成功宣称匿名可用。[GHCR 发布与可见性](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

workflow 不创建 Git 源码标签或 GitHub Release。维护者需另外核对固定版本源码标签与 `release-image.json` 的 `git_commit` 完全一致，再将该提交的配套Compose文件及本版 `release-image.json` 作为Release附件交付；源码完整SHA也须与镜像OCI revision一致。私有附件通过有仓库读权限的GitHub CLI下载，也可直接交付经核对的文件。不要将 `master` 上的 Compose 当成某个旧镜像的发行配套文件，也不要为公开镜像提供无法匿名访问的raw安装命令。

CI 本身不部署、不开来源取数，也不操作现用数据。发行检查不能代替真实升级演练；schema 变化后的回滚需要旧镜像、切换前备份和独立恢复卷。

## 向部署方交接

交接时同时给出：已审定的完整40位 Git SHA、应用版本、对应 Actions 发行链接及 `release-image.json`、`pull_reference` 总摘要、amd64/arm64 子摘要、配套 Compose 的源码版本，以及匿名拉取是否已独立核验。未发行时明确写“仅源码已合入，镜像待发行”，不得用旧版摘要声称包含新功能。`/health` 只提供应用名和版本，不能证明源码提交；应将镜像 OCI 的 `org.opencontainers.image.revision` 与发行回执的完整 SHA 对照。

候选仍需在独立恢复副本上做功能验收，入口见[部署与运维：新增功能验收](部署与运维.md#新增功能验收与版本交接)。交接记录区分源码检查、镜像发行、独立恢复验收和正式切换的完成状态；发行回执中的 `deployed: false` 与 `anonymous_pull_verified: false` 不能当作已部署或已匿名验收。
