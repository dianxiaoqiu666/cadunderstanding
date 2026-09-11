# CAD 理解服务

当前项目可独立运行：上传 DXF 或 DWG，输出建筑构件数组、来源证据、平面拓扑、室内可用区域及后续 3D 构建条件。在右上角“模型设置”填写一组接口和一个模型后，解析时会自动完成理解补全，主页面直接展示结果。

配置方法见 [单模型设置](docs/MODEL_SETTINGS.md)。本机已按官方最新文档配置智谱 Anthropic 接口及唯一视觉模型 `glm-5.3-flash`；真实整店外围识别尚未通过实测。当前进展和调用证据见 [视觉评测记录](docs/GLOBAL_VISION_EVALUATION.md)。解析不读取旧项目的商品表、物料配置、数据库或已保存的人工确认。

模型调用失败、返回格式错误、范围校验不通过或无法确定时，会自动保存独立诊断。网页“查看诊断记录”可打开日志、源图和审核叠加图；如何结合图片、JSON 和失败原因迭代提示词，见 [审核与优化流程](docs/MODEL_REVIEW_LOOP.md)。

## 从 GitHub 首次使用

当前已验证的环境为 Windows 和 Python 3.12。新电脑需先安装依赖，再启动服务：

~~~powershell
git clone https://github.com/dianxiaoqiu666/cadunderstanding.git
Set-Location -LiteralPath .\cadunderstanding
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\start.ps1
~~~

DWG 输入还需要本机安装 ODA File Converter。模型 API Key 在网页“模型设置”中自行填写。仓库只保存源码、脚本、测试和文档；真实 CAD、依赖安装目录、运行日志、模型回执与本机配置不会上传。文档中的历史验证输出位于原开发电脑，涉及真实图纸的回归和 `audit_sources.py` 需要对应的本机图纸与迁移归档，克隆仓库不会附带这些数据。

## 直接运行

本机已从用户指定的 L3 项目迁移并校验依赖，不需要再次安装。

~~~powershell
Set-Location -LiteralPath 'C:\D\汉斯\CADunderstanding'
powershell -ExecutionPolicy Bypass -File .\start.ps1
~~~

打开 http://127.0.0.1:8123 。选择 DXF/DWG，点击“解析 CAD”，可查看二维预览、下载完整 JSON 或扁平构件数组。默认使用 8123，避免占用旧 L3 服务的 8100–8102。端口被占用时会明确退出，可以指定 -Port 8124。

控制台运行时 Ctrl+C 停止；后台运行的本项目实例可用：

~~~powershell
powershell -ExecutionPolicy Bypass -File .\stop.ps1
~~~

停止脚本验证 PID、启动时间和命令行，只停止本项目记录的实例。

## 命令行输出

~~~powershell
.\.venv\Scripts\python.exe -B scripts\parse_cad.py '.\选定规划只留墙体.dxf' -o '.\outputs\my-cad.json'
.\.venv\Scripts\python.exe -B scripts\parse_cad.py '.\drawing.dwg' -o '.\outputs\my-components.json' --array
~~~

默认拒绝覆盖现有输出；明确需要重新生成时加 --force。原 CAD 始终不能作为输出路径。省略 -o 时将 UTF-8 JSON 写到标准输出。输出文件必须是本项目内的 .json 文件。

返回码：0 = 成功输出（可以包含待核实项）；2 = 文件、单位、配置或转换失败；使用 --require-3d-ready 时，已输出但 3D 事实不足返回 3。

## HTTP 接口

| 接口 | 作用 |
|---|---|
| GET /health | 服务状态、DWG 转换器可用性 |
| GET / PUT /v1/cad/model-settings | 读取或保存一份模型配置，Key 本机加密、不回显 |
| POST /v1/cad/analyze | 网页使用的自动解析与单模型理解入口 |
| GET /v1/cad/model-runs/{run_id}/{name} | 独立诊断日志、核验图、反馈与冻结输入 |
| POST /v1/cad/parse | 完整 CadPackage JSON |
| POST /v1/cad/components | 扁平构件数组，包括未知对象和标注 |
| POST /v1/cad/usable-area | 上传 CAD，返回可用区域 |
| POST /v1/cad/placement-check | 对已解析结果检查整个设备占地 |
| POST /v1/cad/enrichment/prepare | 生成同源 JSON、全图及局部编号图、提示词和响应契约；模型读取其中六张图 |
| POST /v1/cad/enrichment/run | 调用本机配置的多模态模型，返回含 enrichment 的完整包 |
| POST /v1/cad/enrichment/import | 导入并校验 SemanticProposal JSON |
| GET /v1/cad/enrichment/providers | 候选模型、API 地址与官方依据 |
| POST /understand | /v1/cad/parse 的兼容别名 |
| GET /v1/cad/schema | 完整输出 JSON Schema |
| GET /v1/cad/results/{result_id}/{kind} | kind 为 package / components / usable-area / options / enrichment / interpreted-components / interpreted-area |
| GET /capabilities | 支持类型、限制和 ParseOptions Schema |
| GET /docs | FastAPI 接口文档（Swagger UI 资源来自 CDN） |

上传使用 multipart/form-data，文件字段 file，可选文本字段 options_json。

~~~powershell
curl.exe -sS -F "file=@选定规划只留墙体.dxf" http://127.0.0.1:8123/v1/cad/parse
~~~

~~~python
from pathlib import Path
from services.understanding.parser import parse_bytes

cad = Path("选定规划只留墙体.dxf")
package = parse_bytes(cad.read_bytes(), cad.name)
walls = package.walls
result = package.model_dump(mode="json")
components = [item.model_dump(mode="json") for item in package.components()]
~~~

## 输出格式与含义

schema_version 为 cad-understanding/1.0。顶层包含：

- walls / doors / windows / columns / beams / stairs：有明确图层、块名或用户映射支持的构件。
- spaces / holes / exclusions：显式区域和内部孔洞。
- annotations / unknown_objects：标注及尚不能确定用途或不能转换的实体。
- candidates：门开口、柱等候选，带证据且 adopted=false；不自动计入已确认构件。
- topology：精确分段、去重、交点、来源区间和闭合面。闭合面只证明几何围合，不自动成为整店范围。
- reconstruction：构件挤出/扫掠所需的几何、参数、缺失字段和空间范围。
- inventory：每个模型空间源实体的句柄、DXF 属性和标签、对应输出 ID。
- usable_area：明确室内范围扣除柱体等障碍后的多边形、孔洞、面积、退让与待核实问题。
- enrichment：独立模型提案、来源与几何校验、室内候选及 usable_area_preview；未补全时为 null。
- issues / summary / provenance：问题、覆盖统计、单位、审计及配置记录。

每个构件包含 type、id、source_handles、source_path、layer、geometry、confidence、evidence、dimensions。所有发布几何均为毫米，保留 WCS 原点，Z 向上；Three.js Y 向上时可用 [x,z,-y] 并乘 0.001 转为米。

尺寸未提供时 height_mm/thickness_mm/sill_height_mm 为 null。墙线也可能是墙面边线，line_reference 默认 UNKNOWN，不擅自当成中心线。将墙线扫掠成体需要明确墙厚、高度及 CENTERLINE/LEFT_FACE/RIGHT_FACE。闭合实心轮廓可按已知高度挤出；圆柱保留精确圆参数。曲线近似有独立精度字段。

status=COMPLETE 只表示本服务支持范围内已完成几何提取与约定语义归类，不等于完整 3D。reconstruction.status=READY 才表示本契约支持的构件、唯一显式整店范围和构建参数齐备。building_shell_complete 保留 null、building_shell_status=NOT_ASSESSED：本服务尚不证明已恢复全部建筑外壳。门窗符号不等于开洞跨度和所属墙体；梁、楼梯仍需剖面/标高，所以这些信息不充分时仍返回 NEEDS_INPUT。

## 按自己的图层命名配置

把自定义图层对应到角色，或提供已知构建尺寸。示例中的 2800/200 只是填写格式，使用时必须替换为你确认的尺寸：

~~~json
{
  "layer_roles": {"建筑墙线": "WALL", "建筑窗": "WINDOW", "结构柱": "COLUMN"},
  "dimensions": {"WALL": {"height_mm": 2800, "thickness_mm": 200, "line_reference": "CENTERLINE"}}
}
~~~

命令行传 --options config.json；网页在“可选”区域填写；HTTP 使用 options_json。这些参数会标为 USER_PARAMETER，不会冒充 CAD 实测值。

针对单个源句柄的事实使用 entity_overrides，并必须携带本次输入文件 source_sha256。错误的哈希或不存在的句柄会被拒绝。不会迁移另一张图或旧项目的人工墙体判断。

默认识别 WALL/A-WALL/墙体、DOOR/A-DOOR/门、WINDOW/窗、COLUMN/柱、BEAM/梁、STAIR/楼梯、PLANNING_SCOPE/HUMAN_PLANNING_SCOPE/FLOOR_BOUNDARY 等约定名称；含混名称保留 UNKNOWN。自定义中文复合层名推荐显式配置。

## 室内可用区域

页面支持 CAD 明确范围、手动圈定室内、障碍与边界退让、区域/配置下载。自动理解的结果直接进入原平面、统计及区域显示，候选状态和待核实项保留，不另设补全操作区。

网页构件数组和可用区域下载使用 interpreted-components / interpreted-area，与当前展示一致；完整包继续保留原构件事实及 enrichment 来源。占地校验也使用当前显示区域，未确认模型候选不允许自动摆放。

`usable_area.status` 可为 READY / REVIEW_REQUIRED / NOT_READY / EMPTY；只有 READY 且 ready_for_placement=true 才能通过自动摆放前置条件。面积计算保留内部孔洞及多个独立区域，扣除已知障碍；墙线缺少厚度时保留屏障和待核实状态。占地接口检查完整多边形，不能只凭中心点在室内就认为可摆放。

通过 `ParseOptions.usable_area.indoor_regions` 提交手动范围时，同时填写当前文件 source_sha256；每个区域含 boundary_mm 和 holes_mm。退让参数为 boundary_clearance_mm、obstacle_clearance_mm。网页保存的配置可用于 CLI 的 --options。

当前真实图没有可靠整店室内范围，返回 NOT_READY。几何闭合面、测试范围和未确认模型提案均不自动升级为正式范围。详情见 [可用区域设计](docs/USABLE_AREA_DESIGN.md)。

## 输入与边界

- 每个上传最大 25 MB；转换后 DXF 最大 100 MB。解析最多 50000 个实体，单曲线最多 20000 个采样点；精确墙拓扑最多 3000 条边。
- 支持文本/二进制 DXF，按 DXF 编码声明读取旧版中文。单位可从 mm/cm/m/in/ft 等已支持的 INSUNITS 换算；无单位文件须显式传 assume_units。
- 支持 LINE、二维 POLYLINE/LWPOLYLINE、ARC、CIRCLE、ELLIPSE、SPLINE、直线环 HATCH style 0、平面 SOLID/TRACE/3DFACE、嵌套 INSERT 及标注。
- HATCH 缺失/非法/style 1/2、曲线 HATCH、无效孔洞、非平面或自交几何不被部分采用；保留来源并输出问题。
- XREF、XCLIP、递归块和不支持的实体为未知；不自动从外部路径加载内容。
- DWG 调用本机 ODA File Converter，禁用主动 audit，记录原 DWG 和转换 DXF 的各自哈希；句柄属于转换后的 DXF 命名空间。转换过程可能有格式规范化，不能保证任意专有对象均可还原。
- 转换器自动查找 Program Files/ODA，可通过环境变量 CAD_ODA_CONVERTER 指向可信的已安装绝对路径。此路径不能由上传表单控制。
- 真实输入和其他项目只读；所有临时转换位于本项目 .tmp/cad 并按请求隔离、用后清理。
- HTTP 解析结果以 JSON 保留在本项目 runtime/results，下载地址位于 provenance.http_exports；已保存结果不会自动清理。CLI 按指定输出路径保存。
- 本次实测 DWG 来自当前真实 DXF 的 ODA 往返转换；用户另外制作的原生 DWG 尚未提供验收。

ODA 接入按 [ezdxf 官方文档](https://ezdxf.readthedocs.io/en/stable/addons/odafc.html) 与已安装 ezdxf 1.4.4 的命令参数核对，日期 2026-09-09。

## 当前这张真实图的结果

当前输入为根目录“选定规划只留墙体.dxf”，SHA256：
0753b00fae27eaa1355b9a3274789079ad9d2b33b28ba343f9afe5516d91468d。

104 个模型空间实体全部有记录：68 条墙图层线、8 个墙图层填充轮廓、18 条标注、10 个未知用途实体。76 是墙类源记录数，不是 76 堵独立物理墙。8 个矩形柱候选与 3 个门开口候选单列。精确拓扑有 72 条派生边、76 个节点、9 个几何闭合面。

图纸未给出可信整店闭合范围、墙厚、高度和全部对象用途，故 PARTIAL / NEEDS_INPUT。这些信息可以用于后续核实和构建，不宣称已完整恢复真实建筑空间。

实际输出见 outputs/current-cad.json；DWG 往返记录见 outputs/dwg-verification/。完整测试与运行证据见 docs/VALIDATION.md。

## 依赖迁移与目录

本项目的 .venv 是新建的独立环境；从用户授权的 C:\D\汉斯\平面与货架-L3 迁移 32 个已安装包和 shared/*.py，复制前后校验 SHA256。未复制旧环境的启动器、运行库、密钥或商品资料。详情见 control/DEPENDENCY_MIGRATION.json。

图片渲染另外安装了 Pillow 12.3.0（官方 PyPI），安装报告 outputs/pillow-install.json。当前共 32 个迁移包和 1 个新增渲染包，没有复制旧项目 Key，也没有升级其余迁移包。

重新迁移并补齐渲染依赖（Pillow 缺失时需要访问官方 PyPI）：

~~~powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SourceProject 'C:\D\汉斯\平面与货架-L3'
~~~

在其他电脑使用 requirements.lock.txt 安装。完整旧版锁文件保留在 control/imported/requirements.l3.lock.txt；新锁文件包含迁移依赖及 Pillow，不含浏览器运行时。

当前运行入口为 services/understanding/api.py（services/understanding/app.py 转接到相同接口）。核心解析在 parser.py，DWG 在 cad_io.py，契约在 contracts.py，复用 L3 的严格几何与拓扑工具。

复制来的 planning、delivery、旧 prepare/confirm、已有设计货架提取和 benchmark 文件属于原项目保留代码，不是当前独立服务的启动链路，原三服务工作流不在本次恢复范围内。旧控制文件与被替换的入口原版保存在 _archive/l3_import/。

原代码主要输出 UnderstandingPackage/PlanRequest（范围、walls/barriers、排除区并依赖商品资料），以及 existing-1.0 观察包。原正式 /understand 已返回 410；本轮建立独立的版本化 CAD 输出，不依赖这些旧业务资料。

## 验证命令

截至 2026-09-10：124 项测试通过，10 份 CAD / 补全 Schema 已导出。包含六图输入、整店核对契约、调用与失败回执记录、GLM-5.3-Flash 的两种协议及单模型流程。一次授权真实请求取得 HTTP 200，但补全未正常完成，整店语义未验收；未自动重试。测试使用模拟响应或本机合成 HTTP 服务。记录见 docs/VALIDATION.md 和 [单次调用核验](docs/GLM_LIVE_TRIAL_2026-09-10.md)。

~~~powershell
.\.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B scripts\export_schemas.py
.\.venv\Scripts\python.exe -B scripts\audit_sources.py
~~~

加密存储测试须在正常 Windows 用户权限下运行。若此前由受限账户生成的 pytest 临时目录无法访问，请指定项目内全新的 --basetemp 目录，避免改动旧测试目录权限。

当前目录不是 Git 仓库。本轮没有提交或推送；如需管理版本，建议按依赖迁移、服务实现、测试与文档三个主题记录。
