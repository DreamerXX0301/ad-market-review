# 贡献指南

欢迎为“冒险的梦想家”提交问题和改进。开始前请阅读 [README](README.md)、[实现范围与数据边界](docs/实现范围与数据边界.md) 和仓库 `AGENTS.md`。

## 提交问题

请提供版本或 commit、操作步骤、预期与实际结果，以及脱敏后的日志。行情或指标问题请同时注明观察日、日 / 周 / 月周期、价格口径与来源覆盖。安全问题请使用 [SECURITY.md](SECURITY.md) 中的方式报告。

附件中不要包含 Key、主密钥、数据库、备份、个人路径或未经授权的供应商原始数据。

## 开发约定

- 使用独立分支和测试数据目录，不让开发进程连接现用数据，也不为测试请求正式来源或创建用户策略。
- 保持个股与板块策略、运行、跟踪的隔离，以及盘面日期与其他业务日期的独立。
- 新增字段明确周期、单位、价格口径和未知状态；合成 fixture 只用于测试，当前成员不能回填历史。
- 数据库只追加迁移，保留 API 与单 worker 的存储边界；升级先在恢复副本验证，见[升级与回滚](docs/版本升级与回滚.md)。
- 接口和功能变化同步更新文档与回归用例。依赖变更提交锁文件，不提交运行数据、凭据、备份、依赖目录和构建缓存。

## 分支与版本

`main` 保存发布基线，`develop` 汇总下一版；功能使用 `codex/功能名` 分支，经 Pull Request 和 CI 检查后合并。发行时同步 `VERSION`、前后端版本和 CHANGELOG，使用明确的 `vX.Y.Z` 标签及镜像摘要。代码合并不会自动更新用户实例，CI 不部署、不发起取数。

## 提交前检查

在仓库根目录：

```bash
cd backend
uv sync --frozen
.venv/bin/python -m pytest tests ../scripts/test_deployment.py ../scripts/test_upgrade.py -q
cd ../frontend
npm ci
npm run format:check
npm run build
find src -name '*.test.mjs' -print0 | xargs -0 -n 1 node
```

真实 API 和容器验收需要独立环境，详见[交付与验收](docs/交付与验收.md)。Pull Request 请说明问题、最终行为、检查结果和迁移影响；界面改动附脱敏的深浅主题截图，升级改动说明备份与回滚边界。
