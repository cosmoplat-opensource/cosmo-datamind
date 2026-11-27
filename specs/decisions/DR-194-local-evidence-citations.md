# DR-194 · 公开文档不以本地文件作为证据

状态：accepted。
实现交付状态：delivered。

## 上下文

[DR-160](DR-160-product-repository-material-boundary.md) 把审查报告、执行日志等开发过程材料限定为本地副本，不随产品仓库提交。原有检查只拦截指向这些材料的 Markdown 链接。IR-016 和 IR-017 的交付清单仍在代码格式中写出 `docs/audits/` 下三个未跟踪文件的路径，并把它们列为已完成的证据；DR-022 也以同样方式引用本地审计清单。读者在仓库中打不开这些文件，无法据此核对。

## 决定

- 迭代记录不再写本地文件路径，改为说明验证了什么、对应哪些验收，并给出可在新克隆中执行的复现命令；同时注明原始记录仅保留本地，不随仓库发布。
- DR-022 保留原有事实，只把本地清单路径改为“仅保留本地”的说明。
- 规约检查 `tests/meta/test_repository_materials.py` 同时检查代码格式中的路径：公开 Markdown 中出现的本地专用文件路径会使检查失败。只写目录名（如 `docs/audits/`）用于说明材料边界时仍然允许。

## 关联

[IR-055 迭代与复现](../iterations/IR-055-local-evidence-citations.md) ／ [验收映射](../acceptance.json)中的 REPO-EVIDENCE-01。
