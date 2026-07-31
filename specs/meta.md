# META · Spec 的 Spec

## Intent
定义 DataMind `specs/` 的结构与记录格式(SDD/规约驱动开发)。格式沿用 spex/`@sublang/spex` 约定,
与上游平台 `../上游本体引擎/specs/meta.md` 保持一致口径,便于同一 AI 代理跨仓消费。

## 组织 / Organization
| 路径 | 内容 | 命名 |
|---|---|---|
| `decisions/` | 决定记录 DR(ADR 格式) | `DR-<NNN>-<kebab>.md` |
| `iterations/` | 迭代记录 IR | `IR-<NNN>-<kebab>.md` |
| `test/` | 系统/集成验收项 | `<kebab>.md`(对应 `test_all.py`) |
| `map.md` | 索引入口 | — |
| `meta.md` | 本文件 | — |

## 记录格式 / Record format
- **DR**(ADR):`状态 / 日期 / 关联`,`## 上下文`、`## 决定`、`## 后果`。只记设计决定与约束,不复述实现逻辑;当实现者能据此生成或审计代码即充分。
- **IR**:`## 目标 Goal`、`## 交付 Deliverables`(带 `[ ]`/`[x]` 复选)、`## 任务 Tasks`(编号,每项一次提交)、`## 验收 Acceptance`。
- 语言简洁,优先用要点与表格而非长段落。散文段落中每句独立成行(便于 diff)。
- 单元测试属实现,不在 spec 立项;`test/` 只写系统/集成级验收(对应 `test_all.py`)。

## 与平台 specs 的边界
- DataMind 的 DR/IR 前缀与平台**独立编号**(平台 DR-001…DR-011 描述引擎;DataMind DR-001… 描述本产品)。
- 引用平台决定时写全称(如「平台 DR-001 元模型」),不与本仓编号混用。

## 消费提示
```
Consult @specs/map.md to find relevant context.
```
