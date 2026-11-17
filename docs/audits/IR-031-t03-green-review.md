# IR-031 T03 · 投影和共享组件 GREEN — DR-159

2026-11-17，用户明确授权继续所有剩余任务，不等待中间人审。
实现完整 IRI 与规范化 blank node 的固定 RDF 投影、已保存设计阶段投影、版本只读 studio 视图和现有设计响应中的 studio 文档。
迁入 Studio 图、模型、连线路由及标签布局，新增隔离控制器以保留 revision/epoch、位置、固定、暂停、展开和资源释放。
运行不依赖附件，源码来源保留于 T01 清单。

T02 证据复核发现六项 Node 失败是“不支持 --experimental-default-type 参数”，原报告将其归为产品 RED 不准确。
保留原 JSON 原样，移除测试启动器中的无效参数，增加 ES module package 声明；未修改任何行为断言。
在临时空产品目录复放原 Node harness，六项均确实触发缺失控制器断言；详见 IR-031-t02-node-red-correction.json。
Python 投影原 RED 仍有效，不能把原六项环境失败算成产品行为复现。

新增 31 项全部转绿；连同旧图、候选、设计 HTTP 和追溯测试共 103 passed（38.83s）。
ruff 与 Node 实际执行通过；固定版本 HTTP 接点另由 T04 记录，不把页面集成算作 T03 已交付。
