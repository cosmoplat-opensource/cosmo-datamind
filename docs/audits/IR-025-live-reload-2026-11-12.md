# IR-025 本机运行态同步 — 2026-11-12

用户在本体详情点击行为树入口遇到 HTTP 404。
本机 access.log 在 15:52:25 记录 POST /api/application-checks 为 404（HTML 路由不存在），GET /api/applications 同样 404；原机器人接口为 200。
Gunicorn worker 自 10:52:06 运行，尚未加载 T05 新 Blueprint；磁盘静态前端已是新版本，形成前后端版本不同步。

确认没有 queued/running 构建和运行中任务后，以 SQLite backup API 保存 .deploy/backups/before-ir025-reload-20261112-155349.sqlite3。
15:53:49 对既有 Gunicorn master 发送 HUP 平滑重载，新 worker 72303 启动，原 worker 正常退出。
新 GET /api/applications 返回 200；写初始化将真实域库从 schema 6 增量升级到 7。
对用户刚生成的固定版本执行一次其原本请求的应用检查，POST 返回 201。
报告 check_e45fc4448fcd4d48a58ef2cd84a970d8：preparation_status=blocked、execution_status=not_evaluated、execution.consumer_incompatible。
这说明路由恢复；当前本体与旧行为树消费者尚不兼容，具体机器人适配继续属于 IR-026。

重载前后及诊断 POST 后独立核对全部原有业务表行、本体／来源／工件文件摘要一致，外键检查通过。
本次仅同步本机服务运行态、增量迁移公共报告表并保存一次显式检查报告，没有修改本体或源 XML，没有修改产品／测试代码或提交 Git。
IR-025 的最终人审状态没有因本次故障排查自动改变。
