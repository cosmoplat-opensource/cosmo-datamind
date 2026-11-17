# 开发分支规约编号迁移

以 master `47dbab0` 的 DR-140 为基准；迁移前开发分支为 `0db54e1`。

| 原开发编号 | 合并后编号 | 文件 |
|---|---|---|
| DR-059 | DR-141 | [DR-141-unified-domain-semantic-graph](decisions/DR-141-unified-domain-semantic-graph.md) |
| DR-060 | DR-142 | [DR-142-robotics-source-recipes-and-reverse-build](decisions/DR-142-robotics-source-recipes-and-reverse-build.md) |
| DR-061 | DR-143 | [DR-143-parameter-contracts-calibration-and-readiness](decisions/DR-143-parameter-contracts-calibration-and-readiness.md) |
| DR-062 | DR-144 | [DR-144-behavior-tree-compilation-as-ontology-application](decisions/DR-144-behavior-tree-compilation-as-ontology-application.md) |
| DR-063 | DR-145 | [DR-145-local-domain-registration-and-application-ui](decisions/DR-145-local-domain-registration-and-application-ui.md) |
| DR-064 | DR-146 | [DR-146-persistent-build-runs-and-artifact-manifests](decisions/DR-146-persistent-build-runs-and-artifact-manifests.md) |
| DR-065 | DR-147 | [DR-147-robotics-migration-and-compatibility-validation](decisions/DR-147-robotics-migration-and-compatibility-validation.md) |
| DR-066 | DR-148 | [DR-148-minimal-robotics-task-workflow](decisions/DR-148-minimal-robotics-task-workflow.md) |
| DR-067 | DR-149 | [DR-149-offline-candidates-and-readable-versions](decisions/DR-149-offline-candidates-and-readable-versions.md) |
| DR-068 | DR-150 | [DR-150-robotics-readonly-graph-views](decisions/DR-150-robotics-readonly-graph-views.md) |
| DR-069 | DR-151 | [DR-151-robotics-task-application](decisions/DR-151-robotics-task-application.md) |
| DR-070 | DR-152 | [DR-152-robotics-dual-build](decisions/DR-152-robotics-dual-build.md) |
| DR-071 | DR-153 | [DR-153-application-contract-registry](decisions/DR-153-application-contract-registry.md) |
| DR-072 | DR-154 | [DR-154-robotics-application-adaptation](decisions/DR-154-robotics-application-adaptation.md) |
| DR-073 | DR-155 | [DR-155-ontology-detail-experience](decisions/DR-155-ontology-detail-experience.md) |
| DR-074 | DR-156 | [DR-156-deepqa-application-compatibility](decisions/DR-156-deepqa-application-compatibility.md) |
| DR-075 | DR-157 | [DR-157-ontology-asset-lifecycle](decisions/DR-157-ontology-asset-lifecycle.md) |

master 的 DR-001～140 保持原编号。开发分支 IR-016～029 与 master IR-001～015 不冲突，保留编号并更新其 DR 引用。新增合并迭代为 IR-030，关联 DR-158。

旧报告中的历史结论只代表当时版本；本次合并验证另行记录，不将旧 GREEN 报告视为当前合并版本已通过。

本地方案引用的 draw.io 架构图与两份 HTML 界面原型一并复制到临时分支，避免方案引用悬空；它们仅为文档附件，不接入运行界面。
