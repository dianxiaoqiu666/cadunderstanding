# 第一阶段：CAD 理解补全

**当前使用入口已于 2026-09-10 简化：右上角“模型设置”填写一组配置、一个模型，解析时自动完成理解。主页面不再显示本文件下文描述的独立补全面板和供应商候选列表。最新操作、密钥保存和整合下载规则见 [MODEL_SETTINGS.md](MODEL_SETTINGS.md)。以下保留为底层接口设计及早期调研资料，手动 API / CLI 仍可用。**

更新：2026-09-10。代码已接入可配置模型协议、同图证据生成和提案校验。供应商及 API Key 尚未选择，没有进行外部模型调用或真实 CAD 模型效果评测。

## 如何让模型对应 JSON 与 CAD

流程是：**DWG / DXF → 原始构件 JSON → 同图编号材料 → 多模态模型补全 → 来源与几何校验 → enriched CadPackage**。

模型实际接收一份 evidence.json、三张 PNG 和固定提示词。DWG 先由本机 ODA 转成 DXF，再走同样的提取与渲染过程。不会将原始 DWG 二进制当图片交给模型。

| 输入 | 用途 |
|---|---|
| evidence.json | 构件实际 ID、图层、源句柄、文字、几何、顶点、拓扑候选、解析问题及配置 |
| plan.png | 查看原平面关系和文字，减少编号遮挡的影响 |
| entities.png | E001 等显示编号与 JSON 的 display_id / 实际 id 对应 |
| vertices.png | V0001 等顶点编号与 point_mm 对应，用于引用边界和断口端点 |
| prompt.txt | 补全任务、证据要求、不可确定时的返回方式 |
| response-schema.json | 本地严格验证的 SemanticProposal 格式 |

图像直接从这一次解析的毫米几何生成。模型返回构件时引用实际 `element_id`；返回房间范围时引用 `boundary_vertex_ids` 和 `hole_vertex_ids`。精确坐标由程序从顶点表恢复，模型不用重新抄写坐标。证据清单记录坐标到像素的变换、标签位置和各文件 SHA256。

`source_sha256` 绑定原 CAD，`evidence_sha256` 进一步绑定本次解析配置、编号、提示词和输出契约。模型结果使用错误文件、过期配置或不存在的 ID 时，整份提案被拒绝。改变退让参数或手动范围后，应重新生成模型材料。

当前真实 DXF 的材料已生成：104 个构件、89 个顶点。其中 14 条 DIMENSION 留在 JSON / 源库存中，但没有伪造尺寸线图像，清单明确列为 unrendered。材料目录为 `runtime/evidence/27a432c51c495b50d3716bce17c804e0b0e81159e9c5b66ac673ac2f2b67e84b/`。

## 模型能补全什么

固定返回 `cad-semantic-proposal/1.0`，包含以下数组（即使为空也必须返回）：

| 字段 | 内容 |
|---|---|
| role_proposals | 某构件可能是柱、墙、门、窗、空间或障碍，附证据 ID 与理由 |
| indoor_proposals | 室内边界顶点、孔洞、补边声明、证据和理由 |
| relation_proposals | 门窗所属墙 HOSTED_BY、连接 CONNECTS_TO、文字说明 LABELS |
| unresolved | 无法确定的对象和仍需补充的证据 |

源图中没有的边界连接必须单列 `gap_proposals`，不能偷偷把断口封上。环自交、重复角色、未知 ID、区域重叠、超出现有明确室内范围或填掉已有范围孔洞都会拒绝。门窗所属关系会检查引用和角色，但不声称已验证真实开洞跨度、接触关系或施工尺寸。

验证通过后，完整 JSON 新增 `enrichment`，其中包含提案、验证问题、来源、候选区域和 `usable_area_preview`。程序计算候选室内减去柱体等障碍及退让后的净空多边形、孔洞和面积。原 walls / columns / topology / inventory、正式 usable_area 和用户参数保持原值；下载构件数组仍表示原解析记录，语义提案通过完整包或独立补全附件获取。

补全结果状态：`REVIEW_REQUIRED` 表示已有待核实提案，`UNRESOLVED` 表示没有确定的补全，`REJECTED` 表示校验失败。所有模型区域都标记 `MODEL_PROPOSAL`、`semantic_confirmation=PENDING`、`ready_for_placement=false`。网页用紫色展示候选。当前没有自动采纳为人工确认、回写 CAD、直接生成已确认 3D 参数的入口。

## 模型服务候选

以下模型名、图片输入、结构化输出及接口地址于 **2026-09-10** 按各供应商官方文档核对。推荐顺序是接入测试顺序，不是本项目 CAD 准确率排名；账号权限和地域可用性仍以实际 API 为准。

| 服务 | 首轮模型 / 备选 | API Base URL | 本项目接法 |
|---|---|---|---|
| 阿里云百炼 / 千问 | qwen3.8-max；备选 qwen3.8-flash、qwen3.7-plus | https://dashscope.aliyuncs.com/compatible-mode/v1 | OpenAI 兼容，JSON 模式 |
| OpenAI | gpt-5.6-terra；复杂图对照 gpt-6-astra、gpt-5.6-sol | https://api.openai.com/v1 | OpenAI 兼容，严格 JSON Schema |
| Google Gemini | gemini-3.8-flash；备选 gemini-3.1-pro-preview | https://generativelanguage.googleapis.com/v1beta/openai | OpenAI 兼容，JSON Schema |
| Anthropic Claude | claude-sonnet-5；备选 claude-opus-5 | https://api.anthropic.com/v1 | 原生 Messages，JSON Schema |

建议先选千问旗舰做国内接口基线，再用 OpenAI 或 Gemini 对同一组图作对照，确认语义质量后再试较轻型号。这个建议考虑接入便利和比较方法，尚无真实 CAD 模型成绩支持能力排序。预览型号需要留意版本生命周期。

官方依据：[千问视觉模型](https://help.aliyun.com/zh/model-studio/vision-model)、[百炼兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)；[OpenAI 模型比较](https://developers.openai.com/api/docs/models/compare)、[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)；[Gemini 模型](https://ai.google.dev/gemini-api/docs/models)、[Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)、[OpenAI 兼容接口](https://ai.google.dev/gemini-api/docs/openai)；[Claude 模型](https://platform.claude.com/docs/en/models/overview)、[结构化输出](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)、[Messages](https://platform.claude.com/docs/en/api/messages/create)。

本项目提供两类协议适配。以上四家均有独立示例配置，但尚未实际连接其中任何一家；协议单元测试使用本机 MockTransport，不能当作供应商实测。

## 选择后怎样配置

四份示例在 `config/llm.qwen.example.json`、`llm.openai.example.json`、`llm.gemini.example.json`、`llm.claude.example.json`。示例无真实密钥，当前没有自动启用任何一家。

以千问为例，在项目 PowerShell 窗口执行；已有配置时先人工查看再修改：

```powershell
Set-Location -LiteralPath 'C:\D\汉斯\CADunderstanding'
if (Test-Path -LiteralPath '.\runtime\llm.local.json') { throw '本机配置已存在，请先查看。' }
Copy-Item -LiteralPath '.\config\llm.qwen.example.json' -Destination '.\runtime\llm.local.json'
$cadSecureKey = Read-Host '输入按量计费 API Key' -AsSecureString
$env:CAD_LLM_API_KEY = [System.Net.NetworkCredential]::new('', $cadSecureKey).Password
Remove-Variable cadSecureKey
powershell -ExecutionPolicy Bypass -File .\stop.ps1
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

环境变量只在当前窗口及其启动的服务中有效。新开窗口需重新设置；`.env` 不会自动读取。配置文件放在被忽略的 runtime，Key 只通过 `api_key_env` 指定的环境变量读取，不写入配置、前端或导出包。使用按量 API Key；不使用订阅 OAuth / session token。百炼 Key 与接口地域需要匹配。

刷新网页后查看模型状态。“生成模型输入”只在本机准备材料；“大模型补全”将本次 JSON 与三张 PNG 发给配置的服务并调用一次 API。没有自动重试或切换供应商。超时、额度、拒绝、截断或格式错误都会显示原因并保留原解析结果。

配置项可选 `response_format=json_schema/json_object`、`max_output_tokens`、`timeout_seconds`、`token_parameter`、`reasoning_effort` 和 `enable_thinking`；应按已选型号文档填写。环境变量 `CAD_LLM_PROVIDER / CAD_LLM_BASE_URL / CAD_LLM_MODEL / CAD_LLM_API_KEY_ENV` 可覆盖本地文件。

## 无 Key 也可以验证流程

网页：解析图纸 → 生成模型输入 → 查看/下载 JSON 与编号图 → 导入符合格式的提案 JSON → 查看紫色候选与校验结果。

命令行默认仅准备，不调用模型：

```powershell
.\.venv\Scripts\python.exe -B scripts\enrich_cad.py '.\选定规划只留墙体.dxf' --prepare-only -o '.\outputs\model-input.json'
.\.venv\Scripts\python.exe -B scripts\enrich_cad.py '.\drawing.dwg' --call-model -o '.\outputs\model-result.json'
.\.venv\Scripts\python.exe -B scripts\enrich_cad.py '.\drawing.dwg' --proposal '.\proposal.json' -o '.\outputs\imported-result.json'
```

`--options` 与原解析 CLI 相同。同一 CAD 的解析配置必须与模型材料一致。默认拒绝覆盖现有输出；确实需要重生成时使用 `--force`。返回码 0 为已生成（可待核实），2 为输入/配置/模型调用错误，3 为已写出被拒绝的补全结果。

独立合成演示放在 `outputs/enrichment-2026-09-10/`：先上传 `synthetic-model-demo.dxf`，再导入 `synthetic-model-proposal.json`。结果为 80 m² 测试室内扣除 0.25 m² 测试柱得到 79.75 m² 候选。提案来源为 `IMPORTED_PROPOSAL`，不表示真实模型已判断正确，也不是当前门店范围。

## API 与限制

先调用 `POST /v1/cad/parse`，取 `provenance.http_exports.result_id`。

| 接口 | 请求 / 返回 |
|---|---|
| GET /v1/cad/enrichment/providers | 四家候选、API 地址、官方来源、核对日期 |
| GET /v1/cad/enrichment/config | 是否配置、缺失项、模型名，不返回 Key |
| POST /v1/cad/enrichment/prepare | `{ "result_id": "..." }` → 材料清单和下载链接 |
| POST /v1/cad/enrichment/run | 同上 → 单次模型调用与校验后的完整包 |
| POST /v1/cad/enrichment/import | `{ "result_id": "...", "proposal": { ... } }` → 校验后的完整包 |
| GET /v1/cad/enrichment/evidence/{evidence_id}/{name} | 限定材料文件下载 |
| GET /v1/cad/results/{result_id}/enrichment | 独立补全附件；未补全时为 null |

JSON 结构错误返回 422；符合格式但引用/几何错误的提案返回可审查的完整包，其 enrichment.status 为 REJECTED。缺配置返回 503，模型接口失败返回 502。

首版单次材料限制 1500 个构件、12000 个顶点、1 MB 输入 JSON；图片 1800×1500。超限明确报错，不静默截断。大型密集 CAD 尚未实现分块/局部放大，标签遮挡信息留在清单；这类图需要后续专项评测。模型输出上限按配置控制，响应超过 2 MB 拒绝。runtime/evidence 与 runtime/results 保留本机缓存，不自动清理。

图片渲染新增 Pillow 12.3.0，来自 [官方 PyPI](https://pypi.org/project/pillow/)，安装报告在 `outputs/pillow-install.json`。原 L3 未含这个依赖；其它已迁移依赖保持原版本。

## 当前验证和下一关

61 项自动测试通过，包含来源与配置串图、编号/像素对应、断口、自交、范围孔洞保护、角色冲突、API 格式、超时与错误响应。真实 DXF 和 ODA 往返 DWG 均在实际 HTTP 服务生成 104 构件、89 顶点的证据材料；文件哈希与下载内容一致。

接下来选定服务和 Key，用同一组带确认答案的图纸比较：柱/墙/门类别、室内与室外、断口、内部孔洞和候选净空。格式有效率、语义正确率与可摆放状态应分别统计。真实模型补全、用户语义确认、正式 3D 所需高度和开洞尺寸仍待完成。
