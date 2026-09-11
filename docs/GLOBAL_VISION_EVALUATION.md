# 整店外围视觉评测与调用诊断

更新：2026-09-10。代码、协议适配和本机检查通过；真实模型补全尚未通过。不要把“配置已保存”“HTTP 尝试开始”“本地保存了模型元数据”作为本次外部模型成功的证明。

## 当前接入

用户指定 Base URL 为 `https://open.bigmodel.cn/api/anthropic`，协议为 Anthropic Messages，完整请求地址为 `https://open.bigmodel.cn/api/anthropic/v1/messages`。当前唯一模型为 `glm-5.3-flash`，本机 Key 保留。单次授权试验已取得 HTTP 200，补全未正常完成；不能确认返回型号、用量或具体失败原因，详见 `docs/GLM_LIVE_TRIAL_2026-09-10.md`。

2026-09-10，本项目 8123 已更新至 1.5.0，服务 PID 32384，以 `runtime/server.json` 为当前身份依据。最新日志、核验图与离线修订流程见 [图片与失败审核](MODEL_REVIEW_LOOP.md)；142 项离线测试、15 项本机检查通过，未新增外部调用。配置迁移记录 `outputs/model-diagnostics/glm53-profile-migration.json`；较早单次试验核验 `outputs/model-diagnostics/glm53flash-v2-01-audit.json`。

另有较后网页失败包 `9f6dfc763a714dd4667101dbf9d0e0b429bf1071f33345e671f042fe1e513300` 已保留真实回执：返回 `glm-5.3-flash`，输出 8192 tokens、`stop_reason=max_tokens`、没有最终文本。离线诊断 `8d06942c354b474bb454d4640e71407c`。它与下述约 118 秒的 `glm53flash-v2-01` 是不同请求，不能用来填补旧试验未保存的字段。

官方依据（2026-09-10 核对）：[智谱 Claude API 兼容](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction)、[GLM-5.3-Flash](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)、[最新模型切换](https://docs.bigmodel.cn/cn/coding-plan/latest-model)。早先“未查到 GLM-5.3-Flash 官方支持”的判断来自旧搜索缓存，现已更正：实时原文确认原生多图及该模型的 Anthropic 接入。官方能力支持与本机账户实际调用成功仍是两件事。完整原文与哈希在 `outputs/zhipu-docs-2026-09-10/`，说明见 `docs/GLM_API_VERIFICATION_2026-09-10.md`。

## 模型应看到的内容

实际输入使用 `cad-semantic-prompt/2.0`，包含：

1. `plan.png`：整张平面，先建立主空间、入口和附属房间的整体关系。
2. `vertices.png`：整张平面的顶点编号。
3. `detail-nw/ne/sw/se.png`：四张重叠局部图，从 CAD 几何重新渲染，保留同一坐标朝向和同一套编号。
4. `model-input.json`：所有构件、所有编号顶点、源线段连接、文字锚点及未渲染实体记录。

同时归档完整 `evidence.json`、额外构件编号图 `entities.png`、提示词、响应契约和文件哈希。给模型发送六张图；`entities.png` 仅作为额外核对材料。取景框不代表室内范围。

真实图有 104 个实体、89 个顶点，压缩 JSON 从 92,171 字节降至 51,216 字节；所有实体与顶点引用保留。已移除可直接复述的候选理由，避免将脚本提示误认为模型独立判断。14 个不支持渲染的 DIMENSION 仍在 JSON 中声明，当前图片不是原生 CAD 全对象截图。

提示词要求先看全图，再逐段核对外围、内凹、入口缺口、功能文字和柱群；只圈小房间时明确声明局部范围。新返回契约 `cad-semantic-proposal/2.0` 要求 `scope_review`，记录核对过的视图、文字锚点相对范围的位置和完整性声明。旧 1.0 提案仍可读取，但缺少整店核对信息。

程序验证真实来源、ID、边界连续性、未声明缺边、自交、孔洞及文字插入点关系。柱体占地在外围范围内扣除。所有模型输出仍为候选，`MODEL_PROPOSAL / PENDING / ready_for_placement=false`；一致性检查通过不等于整店语义正确。

## 已有实验与证据边界

- `outputs/model-evaluations/glm46v-v2-input/`：离线准备，零模型调用，六图输入已保存。
- `outputs/model-evaluations/glm53flash-v2-input/`：最新模型的同源六图输入，零模型调用；`planned-request.json` 固定地址、模型、300 秒等待、8192 输出上限及输入哈希，状态为 PREPARED_NOT_SENT。
- `outputs/model-evaluations/glm53flash-v2-01/`：用户随后授权的一次实际请求，约 118 秒取得 HTTP 200，但 LLM_OUTPUT_INCOMPLETE，没有新提案；原文件保持不变。准备包中的待授权状态是当时快照，不是本次执行状态。
- `outputs/model-evaluations/glm46v-v2-01/`：经用户同意进行的一次旧 OpenAI 兼容入口尝试，结果 `LLM_TIMEOUT`。未保存 HTTP 状态、官方请求编号、最终回复或 usage；不能判断服务器是否收到或是否计费，也没有自动重试。
- 原失败记录的 `api_calls_attempted=1` 表示代码进入尝试流程，当时计数早于网络请求，因此不能据此证明请求实际发出。原记录保持不变，本说明补充其解释。
- 此前 7.410㎡ 结果来自已有本地包 `9454e0f786ee30ab95f9c79e9897c028a74f07782eddb291307ede72b47162da`；只圈中了局部几何面，没有证明整店外围正确。早先审查核对了保存元数据和本地导出，没有对应官方请求回执。
- 用户页面的 11 点草稿仍保留。最近两次应用该范围返回 422，发生于基础解析、早于模型调用；精确失败坐标尚未取得，几何根因未复现。

本次 glm-5.3-flash 的单次授权已用完，没有自动重试。旧 1.4.1 客户端在检查结束状态后才保存回执，导致失败时丢失字段；1.4.2 已修复为先存脱敏回执，并区分截断、协议与接口错误。修复不能恢复本次未保存的响应，后续试验不自动扩展为已有授权。

## 后续可复现的检查方法

在本项目目录运行：

```powershell
.\.venv\Scripts\python.exe -B scripts\diagnose_model.py
```

该脚本仅做无 Key、无 CAD 的 HEAD 检查，并检查本机 API 版本；不调用模型、不能证明 Key 可用。不会修改网络代理设置。

冻结输入，不调用模型：

```powershell
.\.venv\Scripts\python.exe -B scripts\evaluate_model.py '.\选定规划只留墙体.dxf' --run-name new-input-check
```

明确授权后，使用 `--call-model` 才会尝试当前配置的一次请求。`--model` 可临时换同一入口下的模型，`--timeout-seconds` 可临时调整等待，二者均不更改应用配置。每次使用新的 `--run-name`，既有实验不覆盖；`--baseline` 要求 CAD 源哈希一致。

评测保存模型最终文字、提案、整合包、外围叠加 SVG、HTML 和请求进度。`transport.json` 记录连接/TLS、发送、等待响应、HTTP 状态和白名单请求编号，不保存 Key、完整头信息或模型内部思考。

- `http_request_started`：开始本机 HTTP 操作，不代表智谱收到。
- `request_body_sent`：本机写请求体完成，不代表服务端完成处理；代理 CONNECT 不计作模型请求体。
- `response_headers_received / http_status`：收到该端点 HTTP 响应。
- `response_body_complete`：响应正文读取完成。
- `model_response_validated`：回复满足本地提案 Schema，不等于空间语义正确。
- `transport_kind=INJECTED`：注入的测试传输，不得作为外部模型成功证据。

自动错误保存在完整 CAD 包的 `provenance.analysis.transport`。失败报告仍可查看原图，但没有模型回复时不能生成新的外围候选。请求阶段依据[HTTPX 官方 trace 扩展](https://www.python-httpx.org/advanced/extensions/)，固定依赖 httpx 0.28.1 / httpcore 1.0.9。

## 验证与剩余工作

124 项自动测试通过（`outputs/global-vision-2026-09-10/tests-06.xml`），在原有 116 项基础上增加失败回执、用量、结束原因与最终文本保留、协议错误分类、失败不生成候选及密钥/思考排除测试。全部使用模拟模型或本机合成 HTTP 服务。

当前请求证据、进程及 HTTP 共 11 项检查通过（`outputs/model-diagnostics/glm53flash-v2-01-audit.json`），包含调用次数、HTTP 状态、失败未采用、原证据/源 CAD 哈希不变、回归、服务和报告导出。Human 与真实模型语义验收仍为 PENDING。

后续需要取得完整可核对的模型响应，再根据具体结束原因调整参数、对照六图检查整店外围。本次没有第二次调用。当前没有提交或推送，可按调用诊断修复与验证记录分组保存版本。
