# 设计 HTTP 与页面测试接口 v1 — DR-070 / IR-024 T08

本文件细化已确认的双路径与服务契约，供 T08 RED 测试评审；不表示公开功能已实现。
沿用 [T01](robotics-dual-build-v1.md) 与 [T06](robotics-design-service-v1.md) 的字段、阶段、错误及完整 JSON 约束。

## HTTP 与宿主

有效模型配置时 capabilities.build_ready=true，configuration_issue=null；查询不调用模型。
POST /api/robotics/builds 始终返回 202；空描述返回 needs_description 会话，非空描述排队设计运行。
设计操作只接受 JSON 对象且严格校验字段，不允许夹带模型配置、新 XML 或存储路径。
expected_revision 必须为非负整数（不接受布尔、字符串），text 为非空且至多 20000 字符的文本。
messages、confirm-schema、retry 成功返回 202；confirm-classes、edit-classes 本地操作返回 200。
所有操作沿用服务层 request_id、修订、阶段、摘要、活动运行与来源完整性检查；错误返回 JSON error.code/message。
功能关闭时所有设计写入口返回 503/feature_disabled；既有会话和固定导出仍可只读访问。
沿用平台 Host/CSRF/JSON 请求防护，不新增浏览器可注入的 Python 执行接点。
平台先于路由拒绝的 Host、Origin、非 JSON 对象请求保留既有响应格式；不为本功能重写全局防护。

GET /api/robotics/design-sessions/<id>/revisions 返回 {items:[...]}，按修订递增。
会话、修订和导出 GET 不执行恢复、建库、迁移、模型调用或重试；未知 ID 返回 404。
修订 exports/json 返回已固定 JSON 字节；exports/owl 返回同修订 OWL；两者为附件。
未知格式、非法修订或额外查询参数返回 400，缺失合法修订返回 404，工件损坏返回 409/artifact_integrity。
GET /api/domain-runs 的 operation 增加 design_tbox/extract_abox，缺省仍为旧 build_ontology。
新运行详情、事件游标和工件下载复用现有运行 URL；通用 retry 不得绕过会话重试约束。
详情与下载不得泄漏密钥、模型原始端点、存储绝对路径。

宿主正常启动后实际领取并执行设计运行，不要求客户端调用 claim/execute。
等待用户确认不占执行槽；重启恢复遗失的 running 为 interrupted，不自动重放模型。
恢复后只有显式 retry 才产生新运行；已完成历史保持原字节。
模型失败保持原有效修订、真实错误与可重试状态，不能建立旧离线运行或假成功资产。
本组真实宿主测试采用独占临时目录与本地合成模型 HTTP 服务，不触碰用户常驻服务。

## 候选访问

复用 GET /api/ontology-assets/<oid>/versions/<vid>，设计版本增加 design（T06 detail）和 application_compatibility。
目录中机器人版本的 application_compatibility 绑定实际固定版本，至少含 status/reasons/payload_sha256。
GET .../versions/<vid>/compatibility 返回该资格结果；旧合法 UR 为 supported，设计样例为 unsupported，损坏为 invalid。
GET .../versions/<vid>/exports/json 返回原始完整 design.json；现有 payload 返回同版本 OWL。
GET .../versions/<vid>/views/relations 对设计版本使用通用类／属性／实例图。
设计详情不调用只接受旧 UR 的读取器；不兼容设计不能出现在可生成任务选项中，任务 API 仍独立拒绝绕过。
两个主要下载选项为“下载完整 JSON”和“下载 OWL”；manifest 只属于内部运行追溯。

## 控制器与页面

构建页继续 #build?recipe=robotics，同一页面显示后端判定的方式，无手动模式选择。
可选输入标签“建模要求”；离线路径明确说明文本不参与建模；错误配置阻止提交且展示原因。
RoboticsBuild 控制器发送 description（非空时），模型准入保存 session；旧空描述离线表单兼容。
设计页地址仍在构建页：#build?recipe=robotics&session=<id>，刷新只读恢复。

ui/modules/robotics-design.js 暴露 RoboticsDesign.createController(options)。
注入边界沿用 fetch/onChange/requestId/schedule/cancelSchedule；不伪造领域结果。
方法：openSession(id)、setMessage(text)、sendMessage()、confirmClasses()、editClasses()、confirmSchema()、retry()、openRevision(revision)、poll()、snapshot()、dispose()。
snapshot 含 session、messageText、selectedRevision、busy、error；有效会话按返回身份检查。
操作使用当前 expected_revision/expected_stage/snapshot_sha256/failed_run_id；浏览器不能越过阶段确认。
网络结果不明时相同操作复用 request_id；输入改变产生新编号；双击只发一次。
失败保留输入及上一有效设计；409 冲突后由用户重新读取再操作，不自动换修订重提。
已发请求期间的输入修改不能被迟到响应抹掉；切换会话或离开页面忽略迟到响应并清除计时器。
只在 queued/running 时轮询，等待输入／确认及终态停止轮询；轮询单飞。
历史读取不改变当前可写修订，不触发模型；快照返回独立副本。

设计区域 data-testid=robotics-design，标记 data-session-id/data-stage/data-revision/data-status。
显示类别／关系与属性／待明确项，提供“发送建模要求”“确认类别”“返回类别修改”“确认骨架并构建实例”“重试设计”。
结构化按钮决定确认；消息文本中的“确认”不能推进阶段。
历史选择标签“设计修订”；当前与历史结果、模型方式、错误、部分覆盖与执行未验证均如实呈现。
选择历史时显示与当前设计的差异（data-testid=robotics-design-diff），包含修改前后内容；不将历史改为当前修订。
正文和模型回复以文本渲染，不能执行模型生成的 HTML。
完成后可进入现有候选详情、完整 JSON/OWL 下载、通用图谱；不兼容时解释不能生成行为树。
本组包含现有离线页面的真实构建与配置错误控制，以及模型会话各阶段、刷新、重启、失败与显式重试。
真实模型语义质量仍属于 T10。
