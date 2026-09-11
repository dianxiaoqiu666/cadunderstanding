# 验证记录

## 2026-09-10：统一错误日志、图片审核与离线迭代

142 项全量离线测试通过，记录 `outputs/global-vision-2026-09-10/tests-11.xml`，2 条既有依赖弃用提示。新增 18 项日志/复核测试覆盖 HTTP 与超时、Schema 字段错误、输出不完整、UNRESOLVED、几何拒绝边、程序异常位置、日志目录失败、凭据回显脱敏、HTML 转义、同源离线修订及历史回执导入。较早测试中修正了两处断言：报告只展示实际纳入反馈的文本；错误源引用属于 REJECTED 校验结果，而非程序 FAILED。

15 项本机进程/HTTP 检查通过，`outputs/model-review-2026-09-10/verification.json`。8123 已运行 1.5.0，实际服务 PID 32384；页面 JavaScript 语法检查通过，诊断页面、JSON 与六张原图及审核 PNG 哈希一致。浏览器已查看真实失败报告，原页面未刷新。源 CAD、运行提示词、配置和旧实验 17 文件哈希不变。

本轮新发起外部模型请求为 0。分别整理了旧的无完整回执试验，以及网页后来保存的另一条 `max_tokens` 回执；后者输出 8192 tokens、无最终文本。两份诊断明确标记 HISTORICAL_REVIEW，整理过程调用次数为 0，不补造旧数据，不据此证明整店识别准确率。细节和命令见 `docs/MODEL_REVIEW_LOOP.md`。

## 2026-09-10：一次授权请求及失败回执保存修复

用户授权的 glm-5.3-flash 请求已执行一次：HTTP 200、约 118 秒、响应正文读取完成，随后 LLM_OUTPUT_INCOMPLETE，未生成外围候选。记录 `outputs/model-evaluations/glm53flash-v2-01/`。结束原因、返回型号、usage 未被旧失败分支保存，不能确认具体失败原因或计费；没有重试。

已修复为结束状态校验前保存脱敏回执，124 项离线回归通过（`outputs/global-vision-2026-09-10/tests-06.xml`，2 条既有依赖弃用提示）。新增测试覆盖截断仍保留回执且不采用候选、协议错误/接口错误分类，以及不导出思考与 Key；没有新增外部模型调用。

服务已部署 1.4.2，PID 40124；11 项调用证据、进程/HTTP 检查通过：`outputs/model-diagnostics/glm53flash-v2-01-audit.json`。原 CAD 与原实验 17 文件哈希不变。详细记录 `docs/GLM_LIVE_TRIAL_2026-09-10.md`。该轮授权已用完，真实整店语义验收仍为 PENDING。

## 2026-09-10：按最新官方原文接入 GLM-5.3-Flash

116 项离线回归通过，2 条既有依赖弃用提示；证据 `outputs/global-vision-2026-09-10/tests-05.xml`。新测试验证六张图片及同源 JSON 在两种协议中的实际请求体、最终 JSON 校验、思考参数、保留 Key 和高级选项，不访问外部模型。

8123 已运行 1.4.1，PID 3892，唯一模型 `glm-5.3-flash`，Anthropic 地址不变。进程/HTTP 八项检查通过，浏览器设置显示新模型，11 点草稿未刷新或改动；证据 `outputs/model-diagnostics/glm53-live-verification.json`。

原文核验纠正旧搜索缓存中的过时型号结论。`outputs/model-evaluations/glm53flash-v2-input/` 已冻结同源六图、JSON 与预定请求参数，实际调用次数为 0。尚未验证当前 Key 的模型权限、计费或真实整店边界。详见 `docs/GLM_API_VERIFICATION_2026-09-10.md`。下面为历史阶段记录。

## 2026-09-10：六图输入与智谱 Anthropic 适配

109 项离线回归通过，2 条既有依赖弃用提示；证据 `outputs/global-vision-2026-09-10/tests-04.xml`。覆盖整店核对契约、六图与顶点对应、评测归档、网络阶段诊断、失败不重试及 Anthropic 地址正确拼接。模型响应使用模拟或本机合成 HTTP 服务，没有据此宣称真实 GLM 质量通过。

生产服务为 1.4.0。用户指定的 `https://open.bigmodel.cn/api/anthropic` 与 Anthropic 协议已保存，模型名仍为 `glm-4.6v`，当前 Key 在本机重新加密绑定。七项生产 HTTP 检查通过：`outputs/model-diagnostics/live-anthropic-verification.json`。

无密钥 HEAD 得到 401，仅证明网络入口可达。之前旧入口的一次模型尝试只得到 `LLM_TIMEOUT`，没有官方请求回执、最终回复和 usage；本轮没有新增真实推理。当前网页 11 点草稿未改动，真实整店语义验收未通过。详见 `docs/GLOBAL_VISION_EVALUATION.md`；下文配置为空、三图、76 项测试等为历史阶段记录。

## 2026-09-10：单模型设置与自动理解

用户要求简化为单一模型配置、移除独立补全操作面板。本轮已完成并验证：

| 检查 | 结果 / 证据 |
|---|---|
| 完整自动回归 | 76 通过，2 条既有依赖弃用提示；outputs/model-settings-2026-09-10/tests.xml |
| Key 保存 | 正常 Windows 账户 DPAPI 往返、配置文件无明文 Key、读取接口不回显、地址切换不复用旧 Key；tests/test_model_settings.py |
| 自动流程 | 当前单一模型调用一次，无配置跳过模型，失败保留基础解析，无自动重试 |
| 下载与摆放校验 | interpreted-components / interpreted-area 对应页面理解结果；候选区域占地接口返回 REVIEW_REQUIRED，不误用原 READY 状态放行 |
| 浏览器 | 单一表单保存/重开，Key 输入框清空并提示已保存；一次解析产生一次本机模拟模型请求、3 张图片，页面显示 1 个候选柱及 79.750 m² 合成区域 |
| 浏览器下载 | 构件数组和区域附件下载事件通过，HTTP 正文与展示对应，检查到的 error/warn 日志为空 |
| 正式 8123 | 新 analyze 入口对真实 DXF / ODA 往返 DWG 均为 104 个源实体；无真实模型配置，区域保持 NOT_READY |
| 汇总 | outputs/model-settings-2026-09-10/verification.json；隔离实例证据在 browser-session/ |

受限进程的 DPAPI 无可用用户配置，初次加密测试失败；在正常用户权限下验证后通过。测试临时目录与先前受限账户目录隔离，没有修改旧目录权限。正式服务使用同一正常用户账户启动，只监听 127.0.0.1:8123，未改动 L3 的服务。

新表单与调用链已验证；没有向正式配置写入测试 Key，没有外部模型调用或 CAD 对外发送。真实模型质量与用户语义验收仍待完成。旧页面、设置代码和文档已备份至 _archive/single-model-ui-baseline-2026-09-10/manifest.json。

## 2026-09-10：可用区域与第一阶段模型补全

自动测试与本地技术链路通过；没有选择供应商、配置 Key、调用外部模型或进行真实 CAD 模型效果验收。下面旧日期的记录保留为首版历史。

| 检查 | 结果 | 证据 |
|---|---|---|
| 完整 pytest | 61 通过，0 失败；2 条既有依赖弃用提示 | outputs/enrichment-2026-09-10/tests.xml |
| 同源材料 | 真实 DXF 与实际 ODA 往返 DWG 均为 104 构件、89 顶点；各 6 个材料文件 HTTP 正文哈希一致 | outputs/enrichment-2026-09-10/http-verification.json |
| 真实室内范围 | 两种输入继续 NOT_READY；未从小闭合面或模型预览伪造正式范围 | 同上、dxf-package.json、dwg-package.json |
| 编号图 | PNG 1800×1500，中文可见；E 编号对应实体、V 编号对应源顶点；14 个 DIMENSION 作为未渲染对象明确记录 | runtime/evidence/27a432c51c495b50d3716bce17c804e0b0e81159e9c5b66ac673ac2f2b67e84b/ |
| 提案约束 | 错文件/配置、未知 ID、断口未声明、自交、重复角色、已有室内孔洞被填掉时拒绝；源结果保留 | tests/test_enrichment.py |
| 模型协议 | OpenAI 兼容与 Anthropic 两类请求发送同源 JSON + 3 PNG；超时、401、截断、格式/响应外壳错误不伪造成功 | 同上；MockTransport，本机模拟 |
| 合成样例导入 | 80 m² 范围减 0.25 m² 柱 → 79.75 m² 候选；MODEL_PROPOSAL / REVIEW_REQUIRED / ready_for_placement=false；原构件及正式区域不变 | synthetic-enriched-package.json、http-verification.json |
| 网页技术实测 | 真实 DXF 解析、输入生成、候选服务列表和编号图片展示；合成提案导入、紫色区域、补全 JSON 与输入 JSON 下载事件通过 | Codex 内置浏览器实际操作，当前轮记录 |
| 网页运行日志 | 检查到的 warning / error 为空；新 enrichment.js 语法检查通过 | 当前轮浏览器日志、node --check |
| Schema | 10 份契约已导出，含 SemanticProposal / EnrichmentResult / 输入请求 | schemas/、scripts/export_schemas.py |
| 依赖 | 原 32 包保持迁移版本，增加官方 PyPI Pillow 12.3.0；pip check 通过 | requirements.lock.txt、outputs/pillow-install.json |
| 来源保护 | 原 CAD、3745 个迁移文件和 9 个入口归档通过哈希检查 | outputs/source-audit.json |

网页验证修正了输入 JSON 链接打开空标签页的问题，改为本页附件下载后已观察到下载事件。附件内容另外经过实际 HTTP 比对，不能只凭点击认定下载正确。

当前真实 CAD 未导入测试范围或合成提案，模型真实判断尚未运行。DWG 仍是由当前真实 DXF 经 ODA 转出的验证文件，用户原生 DWG 未单独验收。61 项测试包含 39 项原服务/可用区域回归和 22 项新增补全测试。

原可用区域实现前的 14 文件基线在 _archive/usable-area-baseline-2026-09-09/manifest.json；本轮模型补全前 16 文件基线在 _archive/model-enrichment-baseline-2026-09-10/manifest.json。8123 已加载本轮服务；8101 的 L3 服务没有停止。

模型候选与官方核对来源见 docs/MODEL_ENRICHMENT.md；当前仍待供应商选择、按量 Key、实际图纸模型评测及用户语义验收。

## 2026-09-09：首版 CAD 输入输出

日期：2026-09-09。项目：C:\D\汉斯\CADunderstanding。

## 结论

独立 CAD 服务可运行，DXF 与 DWG 的 CLI、HTTP 解析及浏览器上传/下载链路已验证。自动测试和技术验证通过；真实图纸仍缺完整 3D 建筑事实，未进行用户产品验收。

服务地址：http://127.0.0.1:8123。当前运行使用本项目 .venv，不要求原 L3 项目运行。8101 已由原项目使用，本次没有停止它。

## 自动与命令验证

| 检查 | 结果 | 证据 |
|---|---|---|
| pytest | 20 通过、0 失败、0 错误、0 跳过 | outputs/pytest-results.xml |
| 真实 DXF | 104 个模型空间实体全部有输出记录 | outputs/current-cad.json |
| 实际 DWG 转换 | 真实 DXF → ODA DWG → 解析 DXF；墙线端点在 1e-7 mm 绝对比较公差内一致 | tests/test_cad_service.py、outputs/dwg-verification/ |
| 二进制 DXF、R2000 GBK 中文 DXF | PASS，保留“墙体”图层名 | 同上测试 |
| 嵌套块旋转/缩放 | PASS，输出世界坐标及块来源路径 | 同上测试 |
| HATCH 与空间孔洞 | PASS，style 0 保洞；style 1/2/99/缺失不部分采用 | 同上测试 |
| 空模型、非平面、NaN、未知句柄、错误源哈希 | 按契约拒绝或保留未知，无静默伪造 | 同上测试 |
| CLI 返回码与原图保护 | 4 项通过：未就绪返回 3、拒绝已有输出/原 CAD 覆盖返回 2、数组成功返回 0 | outputs/cli-verification.json |
| 依赖检查 | pip check 无损坏依赖；32 个迁移包 | requirements.lock.txt |
| 迁移哈希 | 3745 个目标文件全部匹配 | outputs/source-audit.json、control/DEPENDENCY_MIGRATION.json |
| 原入口备份 | 9 个备份文件哈希全部匹配 | outputs/source-audit.json、control/IMPORT_ARCHIVE.json |
| PowerShell | start.ps1、stop.ps1、scripts/setup.ps1 语法检查通过 | 本轮实际执行 Parser.ParseFile |
| 浏览器 JavaScript | node --check 通过 | .tmp/frontend-check.js |
| JSON Schema | 3 个 Schema 已导出；HTTP、Pydantic 序列化回读通过 | schemas/ |
| 旧 Python 导入入口 | app/understand_bytes/parse_cad 已转到新 CAD 契约，不再读取商品资料 | 同上测试 |

依赖中的测试框架产生两条弃用提示（Starlette/httpx 和 AnyIO 别名），没有测试失败。本次沿用已迁移的固定依赖，没有为消除提示扩大升级范围。

## 真实 HTTP 与浏览器技术验证

HTTP 在实际监听的 8123 端口验证：

- /health 返回 cad-understanding、DXF/DWG 可用。
- DXF、DWG 上传均 HTTP 200，各输出 104 条源记录。
- 扁平构件接口输出 104 项。
- 两种文件的完整包及构件数组共 4 个下载响应均为 HTTP 200，Content-Disposition 为 attachment；附件内容与对应结果一致。
- 记录见 outputs/http-verification.json。

浏览器使用 Codex 内置浏览器实际操作：

- 选择当前真实 DXF、点击解析，页面显示 104 / 104、76 墙类记录、18 标注、10 未知。
- 对平面预览进行了截图目视检查，墙、填充、原文字和未知线可见。
- 选择实际生成的 DWG、点击解析，得到相同计数。
- 完整 JSON 与构件数组的标准 HTTP 附件均触发浏览器下载事件。原先临时 Blob URL 方式已替换为服务端附件。
- 检查到的浏览器 warning/error 日志为空；下载附件正文另由 HTTP 对照核验。

机器可读汇总见 outputs/verification-summary.json。浏览器技术验证不等于用户对识别结果的业务确认。

## 当前真实图的事实边界

原文件 SHA256 保持 0753b00fae27eaa1355b9a3274789079ad9d2b33b28ba343f9afe5516d91468d。

- 68 条墙图层 LINE、8 个墙图层 HATCH；76 为源记录数，不能称为 76 堵物理墙。
- 18 条标注、10 个几何用途未知实体；Defpoints 的既有原线不会因为旧项目曾确认过而自动变成新项目墙事实。
- 8 个柱候选、3 个门开口候选，不作为已确认柱/门采用。
- 72 条精确拓扑派生边、76 个节点、9 个几何闭合面；这些闭合面没有冒充整店范围。
- status=PARTIAL，reconstruction.status=NEEDS_INPUT；整店显式范围、部分构件用途、墙高/墙厚、墙线参考方式以及门窗开洞关系需要真实依据。
- building_shell_complete=null、building_shell_status=NOT_ASSESSED。即使某个标准输入的构件参数齐备，本服务也不宣称已证明整个实际建筑外壳完整。

本次没有用户另行提供的原生 DWG；DWG 技术验证采用当前真实 DXF 经已安装 ODA 转出的文件。也没有用户对最终空间的人工验收。

## 保存与后续

运行入口、输入配置、JSON 字段和限制见 README.md。最终状态见 control/PROJECT_STATE.md。

源项目只读，未迁移运行数据库、密钥或人工确认数据；未修改原 CAD。当前目录不是 Git 仓库，未执行提交、推送或旧工作树清理。建议建立版本记录时按“依赖迁移 / CAD 服务 / 测试与文档”分组。
