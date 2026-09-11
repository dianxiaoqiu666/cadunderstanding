# 来源清单

日期：2026-09-09。

| 用途 | 路径 | 核验 |
|---|---|---|
| 当前真实 CAD | 选定规划只留墙体.dxf | SHA256 0753b00fae27eaa1355b9a3274789079ad9d2b33b28ba343f9afe5516d91468d |
| 授权依赖来源 | C:\D\汉斯\平面与货架-L3\.venv\Lib\site-packages | 32 包；逐文件复制与 SHA256 记录见 DEPENDENCY_MIGRATION.json |
| 授权共享代码来源 | C:\D\汉斯\平面与货架-L3\shared\*.py | 8 模块；复制后字节一致 |
| 原依赖清单 | control/imported/requirements.l3.lock.txt | 按来源文件原样保留 |
| 旧入口和控制文档 | _archive/l3_import/ | 备份与哈希见 IMPORT_ARCHIVE.json |
| DWG 验证文件 | outputs/dwg-verification/dwg/drawing.dwg | 从当前真实 DXF 转换的测试文件，不是用户另行提供的原生 DWG |

代码来源项目本次读到的 HEAD 为 4ba98c58f98dff722194ff435b831fde7e54f21a，同时有混合未提交改动；该值只说明一次观察，不是所有迁移文件均来自该提交的证明。逐文件哈希才是迁移依据。

运行时不读取上述依赖来源项目。原始 CAD 和来源项目没有由本轮改写。
