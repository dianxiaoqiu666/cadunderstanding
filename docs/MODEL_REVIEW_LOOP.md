# 图片、JSON 与失败日志的审核流程

更新：2026-09-10。1.5.1 已把网页解析、补全接口和评测脚本接入统一诊断，并将详细机器记录默认折叠。每次运行使用独立编号，记录输入、响应、校验结果与提示词版本；失败不会自动触发下一次收费请求。

## 网页使用

打开或刷新 http://127.0.0.1:8123 后，按原方式选择 CAD、解析。结果区新增“查看诊断记录”链接，主流程仍然只有一个模型设置入口。旧页面中尚未保存的圈定内容应先保存，再刷新。

“错误与核验结果”和“调用与版本记录”默认折叠，无需人工展开。机器直接 GET 下列 JSON 地址即可读取或下载，折叠状态不影响接口。页面同时提供下载链接与 `rel=alternate / application/json` 发现标记。

```text
GET /v1/cad/model-runs/{run_id}/feedback.json
GET /v1/cad/model-runs/{run_id}/run.json
```

其中 `feedback.json` 是错误与核验反馈，`run.json` 包含完整调用、回执和版本记录。读取这些文件不会发起模型请求。

诊断文件位于 `runtime/model-runs/<run_id>/`，网页结果在 `provenance.analysis.diagnostics` 返回链接。补全 API 在 `enrichment.provenance.diagnostics` 返回链接；调用失败时在错误详情中返回。

| 记录 | 内容 |
|---|---|
| run.json / events.jsonl | 本次状态、阶段、调用次数、错误码、代码位置、提示词与输入哈希 |
| evidence/ | 当次源 JSON、六张模型输入图、额外构件编号图、原提示词与返回契约 |
| model-response.json | 实际收到的最终文本与脱敏回执；未收到时不会编造文件 |
| proposal.json / result.json | 通过 Schema 解析的模型提案与本地校验结果 |
| feedback.json | 格式错误字段、几何失败边、文字锚点位置、模型未决问题和复查建议 |
| review.png | 原坐标下的源 CAD、模型候选、声明补边与失败边叠加 |
| review-prompt.txt | 单独版本化的审核提示词；不替换当前运行提示词 |
| files.json | 完成记录中各文件的 SHA256，可检查后续是否变化 |

日志不保存 API Key、请求鉴权头或模型内部思考。模型最终文本仍是待检查的数据，报告展示会转义。磁盘无法创建日志时，流程在模型请求之前停止；若后续磁盘写入失败，网页会单独报告诊断保存不完整。

## 怎样结合图片和程序提高可核对性

模型继续读取全图、顶点编号全图、四张重叠局部图和 `model-input.json`。JSON 保留源坐标、线段连接、构件 ID 和文字插入点；图片提供空间关系。取景框和闭合小面都不能直接作为整店范围。

返回后，程序检查来源与引用、边界连续性、自交、孔洞、范围约束、柱体占地及文字位置关系。例如未声明缺边会记录 `region_id`、两个 `vertex_ids` 和 `uncovered_length_mm`，在审核图上用粗红线标出。文字锚点位于范围内只证明插入点的位置，不自动证明文字指向的功能区域已经确定。

每次复核应一起看原图、`model-input.json`、`feedback.json`，并从 `run.json` 查看调用回执。不要将图上的像素测量替换为 CAD 坐标。模型无法确定时保留具体问题和所需证据。

## 不同失败应采取不同改进

| 分类 | 典型情况 | 下一步 |
|---|---|---|
| MODEL_CALL / LOCAL_CONFIGURATION | 超时、HTTP 错误、配置或日志目录问题 | 先定位网络、接口、配置或本机问题 |
| PROTOCOL | 返回结构不符合所选接口格式 | 核对 API 格式与响应结构 |
| INCOMPLETE_RESPONSE | max_tokens、length、拒绝或非正常结束 | 查看结束原因、用量及最终文本，再评估请求参数和输出预算 |
| OUTPUT_SCHEMA | 无效 JSON、缺字段、字段类型错误 | 按字段路径对照原契约，调整输出要求 |
| VALIDATION_REJECTED | 不存在的顶点、缺边、越界、自交、文字关系矛盾 | 按实体和顶点回看原图，判断输入、提示词或几何规则的根因 |
| MODEL_UNRESOLVED | 模型没有确定整店范围 | 保留问题，补充必要证据；不能把空候选报为已完成 |
| SEMANTIC_REVIEW | 本地一致性通过 | 对照真实门店标注核实语义，仍是候选 |

几何检查通过不等于门店外围正确。模型区域仍为 `MODEL_PROPOSAL / PENDING`，`ready_for_placement=false`。原 CAD 和明确的人工作用范围不会因模型输出被直接改写。

## 提示词如何迭代

1. 保留失败输入、原提示词、回执与错误类别，先确定错误发生在哪一层。
2. 根据审核图和源引用提出一个可验证的改动，例如要求逐段检查内凹、区分局部房间与整店、缩短重复理由。不要把某张图的人工坐标硬写进通用提示词。
3. 修订另存新版本并记录哈希。现在运行提示词仍为 `cad-semantic-prompt/2.0`；新增的 `cad-semantic-review/1.0` 是审核材料，未自动投入请求。
4. 同一模型、同一图纸和明确的请求参数下比较修订前后；使用其他保留案例检查是否退化。更换模型时沿用相同的证据、契约与评测方法，同时记录型号和协议。
5. 分别记录有效 JSON、引用/几何校验、完整门店覆盖、室外误纳、柱体遗漏和人工语义核对。没有可信参考范围时，`semantic_accuracy=null`，不能把程序通过率当成识别准确率。

可以用同一模型进行后续审核，但不能因为它重复确认自己的答案，就跳过源数据与几何验证。没有自动循环调用，也不会自动调整提示词或采用候选。

## 离线命令

以下命令不读取 Key、不调用模型、不改原评测文件。在项目目录运行：

```powershell
Set-Location -LiteralPath 'C:\D\汉斯\CADunderstanding'

# 整理已有评测。
.\.venv\Scripts\python.exe -B scripts\model_review.py import-evaluation glm53flash-v2-01

# 整理有精确输入哈希和回执的旧网页失败结果。
.\.venv\Scripts\python.exe -B scripts\model_review.py import-result 9f6dfc763a714dd4667101dbf9d0e0b429bf1071f33345e671f042fe1e513300

# 检查修订后的提案，生成新记录并关联父记录，不覆盖原记录。
.\.venv\Scripts\python.exe -B scripts\model_review.py validate-revision <run_id> .\outputs\revised-proposal.json

# 汇总错误类别，区分真实调用、历史整理和离线修订。
.\.venv\Scripts\python.exe -B scripts\model_review.py summary
```

返回码 0 表示材料检查完成；3 表示已留档，但仍为 FAILED / REJECTED / UNRESOLVED；2 表示输入或来源无法核对。重复整理会产生新记录，汇总的次数不是独立样本准确率。当前提示词/证据版本改变后，不能把新输入混写成同一旧输入的离线修订。

## 当前真实证据与验证

- 较早试验 `glm53flash-v2-01`：约 118 秒收到 HTTP 200，但旧程序漏存结束原因、返回型号和用量，这些字段仍然未知。离线诊断编号 `4b341662d30043b4a4cc523f2060d284`。
- 后来网页保存的另一请求：`response_id=msg_20260910165546dae0b08cbc21466e`，返回 `glm-5.3-flash`，输入 41465、输出 8192 tokens，`stop_reason=max_tokens`、`final_content_present=false`，约 131.9 秒；因此没有可校验的外围提案。离线诊断编号 `8d06942c354b474bb454d4640e71407c`。这条回执不能用于补造较早试验的缺失字段。
- 新诊断代码没有发起新的外部模型请求；上述两份都是已有记录的本地整理。当前仍需先检查输出预算及思考参数，再安排明确次数的新试验。本轮未改请求预算、模型设置或运行提示词。

最新诊断页面：[网页失败回执](http://127.0.0.1:8123/v1/cad/model-runs/8d06942c354b474bb454d4640e71407c/index.html)。错误汇总：`outputs/model-feedback/20260910T094315485055Z/summary.json`。

核心流程 142 项离线测试通过，`outputs/global-vision-2026-09-10/tests-11.xml`；包括格式错误、输出未完成、HTTP/超时、不确定范围、几何拒绝、代码异常、日志写入失败、脱敏、报告转义、离线修订与历史回执来源检查。详情折叠改动另运行现有 18 项诊断测试，`outputs/diagnostic-collapse-2026-09-10/tests.xml`；10 项本机检查及浏览器展开/收起核验通过，`outputs/diagnostic-collapse-2026-09-10/verification.json`。当前服务 1.5.1，实际服务 PID 5964，以 `runtime/server.json` 实时身份为准。

诊断页面和实际审核 PNG 已查看；没有刷新、重新解析或修改原 CAD 浏览器页面。源 CAD、运行提示词、模型配置与旧评测 17 文件哈希不变。真实整店范围准确率尚未得到验证。本阶段代码和记录已落盘，修改前备份为 `_archive/model-review-loop-2026-09-10/`；项目当前无 Git，建议做一次阶段备份。

详情折叠更新也已应用到已有两份报告，仅更新 `index.html` 和 `files.json` 中的展示页哈希；机器日志、反馈、图片及其他原始记录哈希不变。修改前页面和清单备份在 `_archive/diagnostic-collapse-2026-09-10/`，更新核验为 `outputs/diagnostic-collapse-2026-09-10/presentation-update.json`。
