# 智谱最新文档与本项目接入核验

日期：2026-09-10。选用唯一视觉模型 `glm-5.3-flash`，保留用户指定的 Anthropic Base URL。参数适配和本机验证已完成，真实模型识别尚未验收。

## 官方原文与更正

用户提供的 [API 入门](https://docs.bigmodel.cn/cn/api/introduction)说明标准 API 的调用方式。标准 `/api/paas/v4/chat/completions` 和 [Anthropic 兼容](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction) `/api/anthropic/v1/messages` 都是官方入口，须分别构造消息、鉴权和参数。先前标准地址本身不是错误地址；现有证据不足以认定旧请求超时的根因是 URL。

早先搜索结果中的旧型号信息不能作为官网最新状态。通过官方 `llms.txt` 索引读取实时 Markdown 原文后确认：

- [GLM-5.3-Flash](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)支持原生图像和多图输入，标准协议可以传入多个 Base64 Data URL 图像块，并支持 JSON 输出。
- [模型切换指南](https://docs.bigmodel.cn/cn/coding-plan/latest-model)明确给出该型号的 Anthropic 接入及 `output_config.effort` 参数。GLM-5.3 是文本模型；本项目选用带 Flash 后缀的视觉模型。
- [思考参数](https://docs.bigmodel.cn/cn/guide/capabilities/thinking)说明原生 GLM-5.3 系列不能关闭思考，原生 `reasoning_effort` 接受 low、high、max。Anthropic 兼容层参数语义按其单独文档处理。

8 份原文保存在 `outputs/zhipu-docs-2026-09-10/`；`source-manifest.json` 和 `source-manifest-extra.json` 记录 URL、抓取时间、HTTP 200、字节数及 SHA256。获取文档没有携带 Key、CAD 或调用模型。官方支持说明不等于本机 Key 已有对应权限，也不能用 Coding Plan 积分图认定本项目的具体请求或计费。

## 当前请求配置

| 项目 | 当前值 |
|---|---|
| 本机服务 | http://127.0.0.1:8123/，版本 1.4.2 |
| 唯一模型 | glm-5.3-flash |
| Base URL | https://open.bigmodel.cn/api/anthropic |
| 最终 POST | https://open.bigmodel.cn/api/anthropic/v1/messages |
| 图片 | 同源全图、全局顶点编号图、四张重叠局部图，共六张 |
| JSON | 保留全部构件、顶点、源线段和文字锚点 |
| 思考强度 | output_config.effort=max |
| 等待与输出 | 300 秒、max_tokens=8192 |
| 返回契约 | 提示词携带 JSON Schema，本机严格校验；不向兼容端点强加原生 JSON Schema 参数 |
| 重试 | 无自动重试或模型切换 |
| Key | 原本机 Key 加密保留，未打印或写入评测材料 |

新模型配置经本机 PUT 保存，脱敏前后记录在 `outputs/model-diagnostics/glm53-profile-migration.json`。UI 不增加供应商列表或单独的模型补全面板。

## 验证与试验材料

124 项离线测试通过，记录 `outputs/global-vision-2026-09-10/tests-06.xml`。除两种协议、六图、参数和 Key 测试外，新增失败回执保存及协议错误分类回归。模拟响应明确标记 INJECTED，不作为外部模型成功证据。

最新 11 项调用证据、进程/HTTP 检查通过，记录 `outputs/model-diagnostics/glm53flash-v2-01-audit.json`；配置及原页面草稿未改动。实际源 CAD SHA256 仍为 `0753b00fae27eaa1355b9a3274789079ad9d2b33b28ba343f9afe5516d91468d`。

`outputs/model-evaluations/glm53flash-v2-input/` 保留准备时的六图、JSON、提示词、Schema 及参数快照。用户随后授权的实际请求另存于 `glm53flash-v2-01/`，不覆盖准备记录。旧 7.410 m² 结果仅是先前局部候选，不能作为新模型结果。

本次 glm-5.3-flash 请求已经用户授权并执行一次，118 秒取得 HTTP 200，但返回没有通过正常结束检查。旧客户端漏存结束原因、返回型号、usage 与最终文本，无法从本次记录确定具体根因或计费。回执保存修复已部署并通过离线验证，未再次调用。详见 [单次调用结果](GLM_LIVE_TRIAL_2026-09-10.md)。

模型仍只提出来源可追溯的候选；范围与柱体占地须经过几何校验，保留 MODEL_PROPOSAL / PENDING。当前未提交或推送，本阶段可按参数适配、验证证据与文档分组保存版本。
