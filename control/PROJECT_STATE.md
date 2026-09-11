# 当前状态：GitHub 私有仓库发布基线

更新：2026-09-11。用户明确授权创建并推送 `cadunderstanding`。已创建私有仓库 https://github.com/dianxiaoqiu666/cadunderstanding ，本机 Git 主分支为 `main`。首次发布包括项目源码、配置示例、Schema、脚本、测试与文档；实际提交与远端核验结果记录在本机 `outputs/github-publication-2026-09-11/`。

发布前完整离线回归 142 项通过（`outputs/github-publication-2026-09-11/tests.xml`），没有调用外部模型。上传集合已进行凭据检查；真实 CAD、本机 Key 与配置、日志和模型回执、虚拟环境、node_modules、缓存及历史备份均由 Git 忽略。提示词文件保持原始字节，避免 Git 换行转换改变证据哈希。新电脑安装和启动步骤已补充到 README。

此轮仅整理版本管理和发布，不改变 CAD 解析、区域规则、模型配置或服务运行方式。整店外围仍需有效模型输出及语义核验。以下为功能阶段的历史记录。

## 上一阶段：诊断详情默认折叠，机器直接读取 JSON

更新：2026-09-10。按用户要求，将“错误与核验结果”“调用与版本记录”改为默认折叠，提供 JSON 下载与机器接口发现标记。机器仍直接 GET `feedback.json` / `run.json`，无需打开页面或展开详情。两份已有报告已更新展示页和对应文件清单哈希，其他日志、图片、输入和反馈均保持原样。

当前 8123 为 1.5.1，实际服务 PID 5964，以 `runtime/server.json` 为实时身份依据。18 项现有诊断回归和 10 项本机检查通过，浏览器已确认默认折叠和展开/收起、JSON 链接。证据 `outputs/diagnostic-collapse-2026-09-10/`，修改前备份 `_archive/diagnostic-collapse-2026-09-10/`。本轮仅刷新了诊断报告页，没有重新解析 CAD 或调用外部模型。代码与说明已保存，未提交或推送。

## 上一阶段：统一诊断与图片审核已部署；真实完整外围仍待有效输出

更新：2026-09-10。1.5.0 已接入每次网页/接口/评测的独立模型日志、脱敏回执、Schema 字段错误、几何失败边、UNRESOLVED 问题、原图与候选叠加 PNG。新增版本化审核提示词和离线导入/修订/错误汇总工具，运行提示词仍为 v2，未自动循环调用。说明 `docs/MODEL_REVIEW_LOOP.md`。

142 项全量离线测试通过（`outputs/global-vision-2026-09-10/tests-11.xml`），15 项本机进程/HTTP 检查通过（`outputs/model-review-2026-09-10/verification.json`）。本项目 8123 运行 1.5.0，实际服务 PID 32384，以 `runtime/server.json` 为实时身份依据。原 CAD、运行提示词、模型设置与旧评测 17 文件哈希不变；本轮没有新发起外部模型调用。

实时核对原网页时发现已有另一条失败结果，当前页面未见先前 11 点草稿；本轮仅只读核对，没有刷新、重新解析或修改原页面。已在新标签打开诊断报告：`/v1/cad/model-runs/8d06942c354b474bb454d4640e71407c/index.html`。

该后续网页结果 `9f6dfc763a714dd4667101dbf9d0e0b429bf1071f33345e671f042fe1e513300` 的回执为 `msg_20260910165546dae0b08cbc21466e`：HTTP 200、返回 glm-5.3-flash、输入 41465 / 输出 8192 tokens、stop_reason=max_tokens、无最终文本，约 131.9 秒。离线导入精确核对了模型输入 JSON 与六图哈希。它与下面约 118 秒的旧试验不同，旧试验缺失回执仍然未知。优先排查输出预算与思考参数，再进行明确次数的新试验；当前没有有效完整外围提案，Human 语义验收 PENDING。

本阶段所有修改已落盘，15 个修改前文件的备份在 `_archive/model-review-loop-2026-09-10/manifest.json`；新日志在 `runtime/model-runs/`，错误汇总在 `outputs/model-feedback/20260910T094315485055Z/summary.json`。项目当前无 Git，无提交/推送，建议做阶段备份。以下为历史快照。

## 历史记录：单次 GLM 请求收到 HTTP 200，补全失败；回执保存修复已部署

更新：2026-09-10。用户已明确“授权调用”，已使用一次 glm-5.3-flash 请求发送之前校验的 JSON 和六张图；约 118 秒收到 HTTP 200 且正文读取完成，但 1.4.1 在正常结束检查处报 LLM_OUTPUT_INCOMPLETE。实际记录 outputs/model-evaluations/glm53flash-v2-01/；没有新外围提案、没有重试，本次授权剩余次数为 0。

本次旧失败分支先检查 stop_reason、后保存回执，因此返回型号、stop_reason、usage 与最终文本未落盘，进程已结束，不能恢复，也不能确认是输出截断还是协议/接口错误。修复现在先保存脱敏回执，再验证结束状态与 Schema；依旧拒绝不完整结果。124 项离线测试通过，outputs/global-vision-2026-09-10/tests-06.xml；没有新增外部模型请求。

8123 已运行 1.4.2，PID 40124，模型 glm-5.3-flash、Anthropic 地址、Key、300 秒/8192 tokens/effort=max 设置未改变。11 项核验通过，outputs/model-diagnostics/glm53flash-v2-01-audit.json。本轮没有刷新主页面或改动 11 点草稿。原 CAD 和原实验 17 个文件哈希不变，尚未通过整店语义验收。

本轮结论与后续检查边界：docs/GLM_LIVE_TRIAL_2026-09-10.md。修改前 9 文件及实验哈希备份 _archive/model-response-receipt-2026-09-10/manifest.json。当前无 Git，无提交/推送；诊断修复与证据已落盘，可作为本阶段保存。下文为先前阶段记录，待授权描述已由本次授权/执行记录取代。

## 历史记录：GLM-5.3-Flash 接入准备

更新：2026-09-10。按用户提供的官方文档重新读取实时 Markdown 原文，已确认 glm-5.3-flash 原生多图与 Anthropic 接入。此前“未查到官方能力”的判断受搜索缓存影响，现更正。原文、URL 与 SHA256 见 outputs/zhipu-docs-2026-09-10/；说明 docs/GLM_API_VERIFICATION_2026-09-10.md。

本项目 8123 当前版本 1.4.1、PID 3892，唯一模型 glm-5.3-flash，Base URL=https://open.bigmodel.cn/api/anthropic、provider=anthropic；Key 沿用本机加密保存。请求使用 output_config.effort=max，等待 300 秒、最大输出 8192 tokens、无自动重试。该型号尚未取得真实响应，当前账户权限与计费未验证。

116 项离线自动测试通过，证据 outputs/global-vision-2026-09-10/tests-05.xml；进程与 HTTP 八项检查通过，证据 outputs/model-diagnostics/glm53-live-verification.json。网页设置已读取新模型，11 点圈定草稿保留，未刷新页面。源 CAD 哈希不变，不修改几何与 Human 状态。

新试验输入已冻结在 outputs/model-evaluations/glm53flash-v2-input/，planned-request.json 包含地址、型号、等待/输出限制与全部输入哈希，PREPARED_NOT_SENT / api_calls_attempted=0。此前一次 glm-4.6v 的授权尝试仅得到超时且无法确认服务器回执，未自动扩展到新型号或再次调用。下一步是在这份已准备材料的一次调用获得授权后检查回执、返回型号、usage、最终 JSON 与六图外围叠加；整店语义验收仍为 PENDING。

本轮代码和文档修改前的 10 文件哈希备份位于 _archive/zhipu-official-profile-2026-09-10/，未包含密钥。当前没有 Git，没有提交/推送。已完成一个可按主题保存的阶段：官方参数适配、回归与接入核验。

## 历史记录：1.4.0 Anthropic 地址适配

更新：2026-09-10 16:03。本项目 8123 运行 1.4.0，PID 29836（实时身份以 runtime/server.json 为准）。按照用户指定地址保存 Base URL=https://open.bigmodel.cn/api/anthropic、provider=anthropic；正确 POST 地址为 /api/anthropic/v1/messages。Key 本机重加密保留，模型名仍为 glm-4.6v。截图中的 GLM-5.3 / GLM-5.3-Flash 尚未作为本项目模型采用或实测。

109 项自动测试通过，生产 HTTP 七项检查通过；证据 outputs/global-vision-2026-09-10/tests-04.xml、outputs/model-diagnostics/live-anthropic-verification.json。无 Key、无 CAD 的 HEAD 检查在代理及直连下都得到 401，只证明当前 HTTP 入口可达，不证明鉴权、余额或模型调用成功。

已完成六图全局/局部输入、精简 JSON、cad-semantic-prompt/2.0、scope_review、引用与文字位置核对、评测脚本及请求阶段日志。仍保留 MODEL_PROPOSAL / PENDING；真实整店范围未验收。实际源 DXF 哈希不变，用户网页 11 点草稿未刷新或改动。最近两次网页 422 在基础解析阶段结束，未调用模型，几何错误根因尚未复现。

授权的一次旧入口评测 outputs/model-evaluations/glm46v-v2-01/report.json 仅记录 LLM_TIMEOUT，未取得 HTTP 回执、模型回复或 usage；不清楚是否到达服务端或计费。旧 api_calls_attempted=1 不能证明请求已发出。没有进行第二次真实调用；300 秒重试方案未获授权并已暂停。改地址后仍没有真实推理，下一步须确认唯一视觉模型和这次材料的一次调用范围，再做真实外围对照。

详细设计、证据和复现方式：docs/GLOBAL_VISION_EVALUATION.md。配置迁移脱敏备份及网络诊断在 outputs/model-diagnostics/；旧 v1 提示词文件保持不变，新提示词单独版本化。当前无 Git、无提交/推送；修改已落盘，适合按视觉输入、协议诊断、评测文档分组保存。

## 历史记录：此前本地保存结果核验

下述“真实 MODEL_API 返回”等表述来自当时保存包的元数据；当时只核对了本地记录与导出一致性，没有对照官方请求回执，不能拿它证明本轮新请求成功。

更新：2026-09-10。

最新检查（15:03）：本项目 8123 服务 PID 3916、版本 1.3.0，身份、监听及健康接口正常。用户已配置智谱 `glm-4.6v`，当前网页已有真实 `MODEL_API` 返回：输入 41,782 / 输出 3,673 tokens。结果 ID `9454e0f786ee30ab95f9c79e9897c028a74f07782eddb291307ede72b47162da`。以下旧记录中的“配置为空/未调用外部模型”仅适用于此前阶段。

本次 15 项进程、来源、模型结果重放和 HTTP 导出核验通过，新增付费调用为 0。识别结果为 8 个柱候选和一个 7.410㎡局部室内候选；柱的理由全部复述输入候选，区域与原拓扑 region-7 相同，尚未识别整店可用空间。状态仍为 REVIEW_REQUIRED / MODEL_PROPOSAL / ready_for_placement=false，不能视为语义或 Human 验收通过。

网页另保留 11 点圈定草稿，应用范围失败，显示 AREA_TOPOLOGY_INVALID；日志两次 HTTP 422 发生在基础解析阶段，未进入模型调用。当前 7.410㎡是之前成功请求的结果。保留用户页面，不刷新、不重启、不改生产代码；未取得失败草稿的精确坐标，根因尚未复现。详细核验 docs/GLM_LIVE_AUDIT_2026-09-10.md，机器证据 outputs/glm-live-audit-2026-09-10/audit.json。

以下为单模型界面完成时的历史记录：

最新用户要求：模型只在一个设置入口填写名称、Base URL、API Key、协议和一个模型名；不展示独立“大模型理解补全”面板。已实现右上角设置窗口，网页解析调用 /v1/cad/analyze 自动完成基础提取、一次模型请求和校验，结果进入现有平面/区域/下载。Key 按当前 Windows 用户账户 DPAPI 加密保存，不回显。实际正式配置仍为空，未调用外部模型。

本轮完整回归 76 项通过（原 61 + 新增 15）。隔离浏览器完成保存、重开、单模型自动调用、统一统计和下载；主服务 8123 的 DXF / ODA 往返 DWG analyze 均通过。证据 outputs/model-settings-2026-09-10/verification.json、tests.xml。当前服务已用正常 Windows 用户权限重启，以支持账户加密；PID 以 runtime/server.json 为准。

当前整合导出为 interpreted-components / interpreted-area，推断保留 CANDIDATE / MODEL_PROPOSAL / PENDING。完整包原几何与正式事实不被覆盖。占地接口使用整合区域并继续拒绝未确认候选。操作文档 docs/MODEL_SETTINGS.md；原四家模型调研仅作为历史资料，不再要求用户在候选列表中选择。

旧设置代码、页面和文档等 10 文件有校验备份：_archive/single-model-ui-baseline-2026-09-10/manifest.json。未创建 Git 或提交；本阶段修改已落盘，可按单模型配置与自动流程分组保存版本。

以下为上一阶段记录，涉及独立补全面板/供应商列表的操作以以上最新状态为准：

已实现同源 JSON + 三张编号 PNG、SemanticProposal 契约、引用与几何校验、OpenAI 兼容 / Anthropic 两类调用适配、离线提案导入和网页候选展示。已列出千问、OpenAI、Gemini、Claude 的模型与 API 配置示例。尚未选择供应商或配置 Key，没有向外部模型发送 CAD。详细接入方法见 docs/MODEL_ENRICHMENT.md。

真实图纸网页已完成解析和模型输入生成：104 个构件、89 个顶点；证据 ID 27a432c51c495b50d3716bce17c804e0b0e81159e9c5b66ac673ac2f2b67e84b。61 项测试通过（39 项原服务/区域 + 22 项补全）；实际 HTTP 的 DXF/DWG 材料与附件校验通过；网页合成提案导入、候选覆盖层和下载通过，未观察到 warning/error。证据在 outputs/enrichment-2026-09-10/。10 份 Schema、README 和 VALIDATION 已同步。

正式可用区域仍要求可靠室内范围；当前真实 CAD 为 PARTIAL / NEEDS_INPUT / usable_area.NOT_READY。模型候选标为 MODEL_PROPOSAL、PENDING，ready_for_placement=false，不修改原几何或已有用户参数。测试得到的 79.75 m² 是独立合成样例，不是当前门店面积。

当前服务为本项目 http://127.0.0.1:8123，PID / 启动时间以 runtime/server.json 为准。只使用 scripts/stop.py 验证并停止本项目实例；没有停止 L3 8101。

依赖：原授权来源 C:\D\汉斯\平面与货架-L3 的 32 包 / 8 个 shared 模块 / 3745 文件哈希一致。本轮追加官方 PyPI 的 Pillow 12.3.0，安装报告 outputs/pillow-install.json；未升级其它包，没有复制密钥或业务/Human 数据。

归档：模型补全前 16 文件见 _archive/model-enrichment-baseline-2026-09-10/manifest.json；可用区域前 14 文件见 _archive/usable-area-baseline-2026-09-09/manifest.json；原复制入口 9 文件见 control/IMPORT_ARCHIVE.json。真实 CAD 哈希仍为 0753b00fae27eaa1355b9a3274789079ad9d2b33b28ba343f9afe5516d91468d。

下一关：用户选择候选服务后，在本机配置按量 API Key；用编号材料进行真实模型对照评测。当前没有自动将模型判断变为 Human 确认的入口。实际模型质量、真实室内范围和 3D 高度/开洞事实待确认。

当前目录没有 Git，未提交或推送；阶段修改均已落盘，可按“依赖 / 可用区域 / 模型补全与测试”分组建立版本记录。

