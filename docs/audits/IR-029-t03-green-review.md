# IR-029 T03：生命周期核心 GREEN

2026-11-16，用户明确授权连续推进剩余任务至 T06，中间不等待 review，最终等待用户验收。
本次免除阶段人工等待，不免除 RED、GREEN、回归和最终验收。

T02 冻结的 `tests/unit/test_asset_lifecycle.py` 52 项原断言全部转绿，测试文件 SHA256 仍为 `2ce043b87c5f5edd3639cb909bf7db7ae9cf71917609ebf944805899dbe65f90`。
结果见 [机器记录](IR-029-t03-results.json)，原始 XML 为 `.check-results/ir029-t03-green.xml`。

实现独立 active／deleted 状态、单调 revision、只读影响预览、完整引用摘要、确认／CAS／幂等、原子审计及共享来源保护。
删除只改变生命周期，不改版本、发布状态、原始文件或历史字节；恢复逐份校验版本文件摘要。
旧 built／forged key 在删除前固定有效 IR 和编辑层；既往物理删除且无快照时只保留不可恢复 tombstone，不制造空本体。
内置 demo／app 通过身份保护，不依据用户填写的名称判断。

本记录证明核心服务 GREEN；真实 HTTP、排队竞态、会话冻结、扫描／缓存、重启及 UI 在 T04／T05 验证。
最终人审尚未完成，IR 不标记 delivered。
