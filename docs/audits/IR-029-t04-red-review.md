# IR-029 T04：入口与兼容 RED

用户授权本轮连续推进到 T06，不等待 T04 人审，直接实现 T05。
17 项真实 Flask HTTP／存储集成断言在实现入口前全部 RED，0 收集／环境错误、0 跳过。
冻结副本位于 `.check-results/ir029-t04/reviewed-tests.py`，原始日志和 XML 同目录；详见 [机器结果](IR-029-t04-results.json)。

覆盖目录／回收站、预览／确认／恢复、旧 built／forged 删除接口、off／shadow／enforce 三模式同步与 SSE 的缓存前守卫、扫描和重启不能复活、恢复固定快照、queued／running 冲突、实际并发入队竞争、等待会话只读、历史详情以及严格写字段／ETag。
所有 17 项原行为断言先转绿；其后仅删除一个未使用的局部 RunStore import 以通过 lint，未修改断言、fixture、参数化或期望。
原冻结字节及前后 SHA、忽略 import 后 AST 一致证明均保留在机器结果中。
未以修改测试来接受更宽松的行为。
