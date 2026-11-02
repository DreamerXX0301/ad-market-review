# 常用 GPT 模型目录 v1

目录版本：`openai-common-models-2026-11-02`；价格版本沿用同日核对的 `pricing-2026-11-02`。此契约只收敛可选择的 GPT 模型，不改变 DeepSeek 配置、实例预算、会话绑定或资料权限。

## 选择与历史

OpenAI 的配置界面及保存 API 只允许下表八个模型 ID，显示顺序与表格一致。账号模型发现结果不能增加下拉选项；GET `/api/v1/ai/providers` 的 OpenAI `models` 返回八项，`discovered_models` 只返回该八项的目录交集。目录出现不代表账号已获模型权限。

OpenAI 不再提供“其他模型 ID”或自定义 ID 输入。原配置、历史会话、消息和费用中的旧模型字符串不自动改写或删除。原来已启用的不支持模型仍可用同 ID、无新 Key 的关闭请求退役；不能借此重新启用、换 Key 或创建不支持的模型配置。界面对这种旧配置显示说明并要求重新选择，不静默改为首项。

修改模型或 Key 后，原验证签名失效；须保存、验证所选模型与凭据，再手动启用。验证仍由唯一 worker 发送原有固定小请求并计入预算；本契约不引入自动的八模型轮流探测。关闭及清除 Key 后，历史仍可阅读。

## 官方标准文本价格

下表为 2026-11-02 实际打开各模型官方页面核对的 USD / 百万 token。Cache write 为标准 30 分钟保留；应用未请求长保留、Fast/Ultrafast 或区域加价，输入上限也未达到官方长上下文加价阈值。费用回执继续冻结发出时的费率，属于预估而非官方账单。

| 模型名称及官方来源 | 模型 ID | 输入 | 缓存命中 | 缓存写入 | 输出 |
| --- | --- | ---: | ---: | ---: | ---: |
| [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) | `gpt-6.1-sol` | 2.00 | 0.10 | 2.50 | 10.00 |
| [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) | `gpt-6-astra` | 10.00 | 1.00 | 12.50 | 50.00 |
| [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra) | `gpt-6.1-terra` | 1.00 | 0.05 | 1.25 | 5.00 |
| [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) | `gpt-6-sol` | 2.00 | 0.20 | 2.50 | 10.00 |
| [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) | `gpt-6-luna` | 0.10 | 0.01 | 0.125 | 0.50 |
| [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) | `gpt-5.6-sol` | 4.00 | 0.40 | 5.00 | 20.00 |
| [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | `gpt-5.6-terra` | 2.00 | 0.20 | 2.50 | 12.00 |
| [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | `gpt-5.6-luna` | 0.20 | 0.02 | 0.25 | 1.20 |

GPT-5.6 三项的缓存写入单价按其官方页面明确的“输入费率 1.25 倍”换算。GPT-5.6 Sol 官方页面说明促销费率至少持续至 2026-11-21；后续版本更新应重新核对，不能把本表当作永久承诺。

## 接口能力边界

八项使用现有 Responses 调用链、流式文本与函数工具协议；官方能力不代替本实例的实际认证与模型验证。增加可选项不自动开放新的工具或改变网络来源范围；当前应用原生联网能力仍由 `ai_web.supported` 和对应会计、来源核验契约独立控制。没有进行八模型真实付费试问。

本契约对应 `ai_catalog.py`、`ai_service.save_provider/providers`、`aiState.ts` 及 `AIModelPicker.tsx`；与每轮研究角色、语义范围判断互相独立。
