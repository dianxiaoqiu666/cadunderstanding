# 单模型设置与自动 CAD 理解

更新：2026-09-10。主页面只保留“模型设置”入口，不显示独立补全面板、供应商列表或提案导入操作。

## 使用方法

1. 打开 http://127.0.0.1:8123，点击右上角“模型设置”。
2. 填写名称、Base URL、按量 API Key、API 格式和一个模型名称。模型需要支持图片输入。
3. 点击“保存设置”。关闭窗口后，选择 DXF 或 DWG，点击“解析 CAD”。

每次解析会自动使用这一个模型，完成 JSON 和编号图的语义补全。页面直接显示整合后的构件、室内区域和净空面积，不需要再点一次补全按钮。重新计算退让或圈定范围也会重新解析，使用当前配置；没有自动重试、多个模型并行或供应商自动切换。

API 格式只有 OpenAI Chat Completions 和 Anthropic Messages 两种协议。Base URL 例如 `https://api.example.com/v1`；如果粘贴的是末尾带 `/chat/completions` 或 `/messages` 的完整请求地址，会按所选格式去除末尾操作路径，避免重复拼接。模型 ID 以服务商实际提供的值为准。

### 本机智谱配置（2026-09-10）

- Base URL：`https://open.bigmodel.cn/api/anthropic`
- API 格式：Anthropic Messages
- 最终 POST 地址：`https://open.bigmodel.cn/api/anthropic/v1/messages`
- 当前唯一模型名：`glm-5.3-flash`。单次授权请求已取得 HTTP 200，但补全未正常完成；有效输出与整店范围仍未验收。

这是用户指定、且由[智谱官方 Claude 兼容文档](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction)确认的地址。兼容客户端会补齐 `/v1/messages`；若 Base URL 已带 `/v1` 则只追加 `/messages`。智谱兼容配置使用基本 Messages 字段、base64 图片块及提示词中的 JSON 契约，本地继续严格校验返回结构。`glm-5.3-flash` 使用 `output_config.effort=max`；新模型等待上限为 300 秒，最大输出 8192 tokens，无自动重试。只编辑名称时保留已经设置的高级参数。

模型选择依据是[GLM-5.3-Flash 官方说明](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)和[官方模型切换指南](https://docs.bigmodel.cn/cn/coding-plan/latest-model)，并非仅依据客户端“视觉”标签。其标准 Chat Completions 协议使用 `thinking.type=enabled`、`reasoning_effort=max`，与 Anthropic 参数分开构造。最新原文、哈希及本机核验说明见 [GLM 接入核验](GLM_API_VERIFICATION_2026-09-10.md)。

`/api/paas/v4` 是另一个 OpenAI 兼容入口，不能把它的协议参数和 Anthropic 地址混用。Coding Plan 积分与标准 API 用量不是同一统计口径，见[官方说明](https://docs.bigmodel.cn/cn/coding-plan/faq)。截图中的模型列表和“视觉”标签不能代替本接口的实际模型响应验证。

## 配置保存

只保存一份 `runtime/llm.local.json`。新设置替换当前模型；保存后立即生效，不需重启。重新打开设置不会返回真实 Key，Key 留空表示保留原值；更换 API 地址或格式需要重新填写对应 Key。

网页保存的 Key 使用 Windows 当前账户 DPAPI 加密，再与地址、模型配置一起原子保存。明文 Key 不进入配置 JSON、网页读取响应、浏览器存储或 CAD 导出文件。实现依据：[Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)、[CryptUnprotectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)，2026-09-10 核对。

运行服务和保存/使用 Key 应使用同一正常 Windows 用户账户。复制配置到其它账户后，应重新填写 Key。本次受限测试进程没有可用的 DPAPI 用户配置，因此加密存储与完整回归在正常用户权限下验证，正式 8123 服务也使用正常用户账户运行；无需管理员身份。

仍可沿用以前的环境变量配置。网页保存后以网页的单一配置为准，旧的 `CAD_LLM_MODEL / CAD_LLM_BASE_URL / CAD_LLM_API_KEY` 不会悄悄覆盖它。高级配置字段仍可由维护人员在本机文件中管理；无需在网页展示多组候选。

## 主页面和下载

- 未配置模型时：基础 CAD 解析照常可用，输出 `provenance.analysis.status=NOT_CONFIGURED`。
- 已配置且基础解析通过时：后台生成同源 JSON、两张全图与四张局部放大图，至多尝试一次模型请求，再校验来源哈希、ID、范围、孔洞等。
- 基础解析失败时：HTTP 422 带 `stage=CAD_PARSE`、`model_call_started=false`，页面明确保留上次结果，本次没有进入模型请求。
- 模型失败时：保留基础解析，待核实项说明原因，`analysis.status=FAILED`，不静默重试。
- 模型明确无法确定范围时：`analysis.status=UNRESOLVED`；候选校验不通过时为 `REJECTED`。结果区的“查看诊断记录”链接可查看错误字段、源图与候选核验图，主页面不增加独立补全面板。
- 结果通过结构和几何校验时：构件统计、平面图及区域下载展示同一份理解结果，推断仍标记为 CANDIDATE / MODEL_PROPOSAL / PENDING。

“下载完整 JSON”保留原 CAD 构件和独立 `enrichment` 来源；“下载构件数组”使用 `interpreted-components`；“下载可用区域”使用 `interpreted-area`。两个整合附件与网页一致。原始 `components` / `usable-area` 附件和基础提取 API 继续保留。

已有明确室内范围、孔洞和原坐标不会被模型直接改写。模型候选区域的 `ready_for_placement=false`；占地校验接口也使用相同的整合区域，不能在网页显示待核实时从接口获得自动摆放许可。

## 接口

| 接口 | 行为 |
|---|---|
| GET /v1/cad/model-settings | 当前配置和 api_key_set 状态，不返回 Key 或加密内容 |
| PUT /v1/cad/model-settings | 保存一份配置，字段 name / base_url / api_key / provider / model |
| POST /v1/cad/analyze | 上传 file 和可选 options_json，自动解析并使用当前模型 |
| POST /v1/cad/parse | 保留原来的基础提取行为，不调用模型 |
| GET /v1/cad/results/{id}/interpreted-components | 与页面一致的构件数组，推断带候选标记 |
| GET /v1/cad/results/{id}/interpreted-area | 与页面一致的区域、孔洞和面积 |

配置写接口仅接受本机地址上的 JSON 请求，拒绝其它网页来源；错误消息不回显提交的 Key。完整包中 `provenance.analysis` 记录自动流程的状态；HTTP 附件格式记录 `http_export_version=2`，避免读取旧附件元数据。

## 当前验证

142 项离线自动测试通过，证据 `outputs/global-vision-2026-09-10/tests-11.xml`。生产 8123 已运行 1.5.0，服务 PID 32384；15 项进程/HTTP 检查通过，证据 `outputs/model-review-2026-09-10/verification.json`。模型设置与运行提示词哈希不变，本轮未刷新原页面或调用外部模型。[诊断与提示词迭代说明](MODEL_REVIEW_LOOP.md)。

一次授权试验在约 118 秒取得 HTTP 200，随后报 LLM_OUTPUT_INCOMPLETE，未重试。旧失败分支漏存结束原因与用量，无法据此确认具体失败原因或计费。1.4.2 已修复为校验前保存脱敏回执，但不能恢复旧响应。详见 `docs/GLM_LIVE_TRIAL_2026-09-10.md`；真实视觉补全与整店外围质量仍未通过。

随后网页保存的另一请求已有可核对回执：返回 `glm-5.3-flash`、`stop_reason=max_tokens`、输入 41465 / 输出 8192 tokens、没有最终文本。已离线整理到 `runtime/model-runs/8d06942c354b474bb454d4640e71407c/`。这表明该请求在生成有效提案前结束，需先核对输出预算及思考参数；不能据此断言模型圈错范围，也不能把它当作较早试验的缺失回执。

## 单模型界面首版的历史验证

76 项自动测试通过，包含原 61 项与新增 15 项。记录：`outputs/model-settings-2026-09-10/tests.xml`。

隔离网页实例完成保存、重开、隐藏 Key、一次解析调用一次本机模拟模型、统一构件统计、79.75 m² 合成候选区域和两类下载验证；HTTP 附件与展示值一致。真实 DXF 和 ODA 往返 DWG 在正式 8123 的 analyze 接口均返回 104 个源实体，模型未配置时保持 NOT_READY，不把测试面积写入真实图纸。汇总：`outputs/model-settings-2026-09-10/verification.json`。

测试只使用合成 Key 和本机模拟响应，没有填写正式供应商、调用真实模型或向外部服务发送 CAD。真实模型判断质量和真实图纸语义验收仍待完成。
