# 独立 CAD 理解服务

运行入口：services.understanding.api:app；services.understanding.app:app 转接到同一接口。

启动、CLI、HTTP 契约、DWG 转换和限制统一见根目录 README.md。公开输出版本为 cad-understanding/1.0；不依赖原 L3 商品与规划业务。

parser.py 处理语义与几何，cad_io.py 隔离读取/转换，contracts.py 定义公共 Schema，geometry.py 保留严格几何工具。scope_review.py 与 door_gaps.py 复用来源拓扑能力。

prepare.py、existing_api.py、store_scope.py、existing_design.py 是复制的 L3 保留代码；未挂载为当前服务路由，原 /prepare /confirm /resolve 不属于当前服务 API。
