# IR-035 · 单一完善入口与生成错误反馈

状态：delivered。关联：[DR-174](../decisions/DR-174-single-repair-entry-and-generation-feedback.md)。

## 任务与验收

- [x] TCF-01：应用检查只显示一个完善入口，可信词表及自定义对应流程都可进入并正确返回。
- [x] GEN-FEEDBACK-01：可生成任务不显示物理执行提醒；真正校验失败保留阻断，说明具体配置缺项及完善入口。

## 问题与实现

原“信息已保存，但本体仍未通过严格生成校验”来自保存后生成异常的通用提示，并非物理执行能力提醒。
合成用例复现：当前任务配置补齐后，本体内另一份感知配置仍缺识别对象名、识别模型名、阈值、曝光时间及拍照关节配置；任务级缺项投影不再要求填写它，但全本体消费校验仍拒绝该版本。
保留全本体校验及失败状态；未引用的配置也不自动删除、不从验证输入中剔除。
校验异常保留原 ValueError 的继承及文本契约，增加内部结构化校验报告；任务层按业务名称归纳缺项与需要调整的字段，不向普通提示泄露原始校验堆栈。
保存后校验失败和结果保存失败分别提示，实际信息保存成功的版本继续保留，失败不产生候选。
完善链接固定到该版本的知识维护界面；维护产生新版本后，应基于新版本生成，不自动改写原任务固定版本。
首次生成的相同校验错误也显示具体问题；重新读取保留已记录的生成诊断，不写入或改写旧记录。
历史通用错误在前端明确为生成失败并提供完善入口，不假造旧记录未保留的具体缺项。
只保留一个“完善本体”入口，按原推荐选择修订或核对；服务端动作格式及旧报告保持兼容。
详情浏览器回归同步单一入口约定：可信缺项验证修订原设计，自定义词表验证从同一入口进入对应流程；保留固定版本、原载荷、版本数量与无意外写入的断言。
移除任务及应用检查页面中重复的物理执行提醒，候选文件与验证报告中的原验证事实不变。
详情进入任务的用例改为断言不再显示该提醒，同时在真实检查响应中核对执行状态仍未评估、执行资格仍未验证；保留任务幂等、刷新恢复和草稿验证。

## 验证

先运行缺项提示、替代入口移除及历史提示的失败测试，再实现对应修复。

- 任务浏览器 19 项通过；历史错误兼容及提示收尾后，实际错误和正常生成 2 项复跑通过。
- 生成错误分类与保存事实保留 6 项通过，覆盖缺项、单值约束冲突、首次生成、保存后生成、结果保存失败、未知校验失败及读取不写数据。
- 候选文件集合的规约检查 95 项通过，场景基准 55/55；全仓 lint、涉及 Python 文件的类型检查通过。
- 完整设计浏览器 50 项通过，覆盖统一入口、可信与自定义流程及真实历史导航。
- 消费、发布、离线生成、任务服务及组件最终相关回归 122 项通过，无失败、错误或跳过。

复跑包含重复覆盖，不累加为独立用例；合成测试夹具只用于验证产品逻辑，不代表真实模型、机器人或现场执行验证。
未执行全量回归及全仓覆盖率门禁。

## 复现

安装仓库固定开发及 UI 依赖、Playwright Chromium，Node 必须在 PATH，可使用 Playwright 随包 Node。
使用 `scripts/check.py` 的 `isolated_env` 构造临时工作区；浏览器套件分别在独立进程运行，不读取真实模型凭据或业务资产。

```bash
python -m pytest tests/unit/test_robotics_publication.py tests/unit/test_robotics_task_generation_feedback.py tests/unit/test_robotics_offline_candidates.py tests/unit/test_robotics_offline_ui.py tests/unit/test_robotics_task_service.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_task_save_feedback.py tests/unit/test_robotics_task_completion_feedback.py -q
python -m pytest tests/frontend/robotics-task-browser.py -q
python -m pytest tests/frontend/robotics-task-browser.py -k 'saved_configuration_failure or optional_notes_do_not_block' -q
python -m pytest tests/frontend/robotics-design-browser.py -q
python -m pytest tests/meta -q
python -m ruff check .
python -m mypy domains/robotics/consumer.py domain_runs/robotics_task_service.py tests/unit/test_robotics_task_generation_feedback.py tests/frontend/robotics-task-browser.py tests/frontend/robotics-design-browser.py --no-error-summary
python scripts/scenario_bench.py
```

逐次日志、截图及候选差异只留本地或 CI；可用上述命令复核当前检出版本。

## 后续范围修订

2026-11-20 详情回归修正中，详情浏览器 29 项及设计浏览器 50 项通过，单一入口与后端执行状态断言均保留；复现及同轮边界见 [IR-033 本轮回归](IR-033-application-guidance-and-return-navigation.md#本轮回归补充)。

2026-11-20：未选中配置继续阻断任务生成的行为由 [IR-036](IR-036-task-configuration-validation-scope.md) 修正；GEN-FEEDBACK-01 当前范围随之更新，本轮历史结果不作为新范围通过的证据。
