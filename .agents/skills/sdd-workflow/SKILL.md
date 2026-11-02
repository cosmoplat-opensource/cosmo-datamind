---
name: sdd-workflow
description: 规约驱动开发（SDD）+ TDD 协作流程。当用户要按"先写规约、再写代码、测试兜底"的范式开发新功能时使用。覆盖：首次初始化 specs 目录→问题识别→写 DR（决策记录）→写 IR（迭代记录）→写 acceptance 映射→TDD 红（失败测试）→TDD 绿（实现）→全量回归→人审交付。适用于"人定方向、AI 写代码"的协作开发场景。
---

# SDD 规约驱动开发工作流

以"人定方向、AI 执行、测试兜底"为核心。人负责判断和取舍，AI 负责写代码和测试。

---

## 首次初始化（只做一次）

如果项目还没有 `specs/` 目录和测试骨架，先做初始化。

### 建目录结构

```
project/
├── specs/
│   ├── acceptance.json          # 复制 references/acceptance-template.json
│   ├── decisions/              # 空目录
│   └── iterations/             # 空目录
├── tests/
│   └── unit/                   # 空目录
├── test_all.py                 # 复制 references/test-all-template.py
└── scripts/
    └── check.py                # 复制 references/check-template.py
```

---

## 10 个环节

### ① 问题识别（人主导，100% 人工）

**做什么**：明确"现在有什么问题、为什么需要做新东西"。

**AI 动作**：
- 读现有代码，盘点现状
- 列备选方案优劣
- **不做决定**——"选哪个"是人拍板

**产出**：一段清晰的问题描述，包含：
- 现状是什么
- 哪里不行
- 不解决会怎样

**示例**：
> "现在指标的聚合方式在查询时按名称正则猜，日期列现场找。指标转不成稳定 SQL，也无法与任何参照结果比对。"

---

### ② 写 DR 决策记录（人主导，100% 人工）

**做什么**：把"选哪个方案、为什么、代价是什么"写成 DR 文件。

**AI 动作**：
- 按 `references/dr-template.md` 生成 DR 骨架
- 人填上下文、决定、后果
- AI 帮忙分析影响面

**产出**：`specs/decisions/DR-NNN-kebab-name.md`

**DR 三段式**：
1. **上下文 Context**：为什么需要做这个决定
2. **决定 Decision**：选了什么方案（逐条列）
3. **后果 Consequences**：影响什么、有什么代价

**关键约束**：
- 只记设计决定，不复述实现逻辑
- 每句独立成行（便于 diff）
- 实现者能据此生成或审计代码即充分

---

### ③ 写 IR 迭代记录（人定范围，AI 写骨架）

**做什么**：把 DR 变成"这一轮具体做什么、交付什么、验收什么"。

**AI 动作**：
- 按 `references/ir-template.md` 生成 IR 骨架
- 人填 Goal（这一轮范围）和 Acceptance（什么叫做完）
- AI 写 Deliverables（交付物清单）和 Tasks（任务分解）

**产出**：`specs/iterations/IR-NNN-kebab-name.md`

**IR 四段式**：
1. **目标 Goal**：这一轮要达成什么
2. **交付 Deliverables**：`[ ]` 复选框列清单
3. **任务 Tasks**：编号，每项一次提交
4. **验收 Acceptance**：`[ ]` 复选框，什么叫做完

**关键约束**：
- 一个 DR 可能跨多个 IR
- Tasks 每条独立可提交
- Acceptance 是人定的，不是 AI 自己想的

---

### ④ 更新 acceptance.json（人定 criterion，AI 填 tests）

**做什么**：把"业务要求"翻译成"哪几个测试守着这条要求"。

**AI 动作**：
- 人写 criterion（业务要求，一句话）
- AI 填 tests 路径（具体哪个 pytest 函数守这条要求）
- 按 `references/acceptance-template.json` 格式

**产出**：acceptance.json 多一条 requirement。

**编号规范**（可自定义）：
- ALG-XX：算法相关
- MET-XX：指标相关
- SEC-XX：安全相关
- SYS-XX：系统相关

---

### ⑤ TDD 红：先写失败测试（AI 写代码，人 review）

**做什么**：按 Tasks 写 pytest 测试，跑它是红的（功能还没实现）。

**AI 动作**：
- 按 Tasks 逐条写 `tests/unit/test_xxx.py`
- 用 `assert` 写断言
- 跑 `pytest tests/unit/test_xxx.py -v` → 确认红
- **人 review 测试是否覆盖到位**

**关键约束**：
- 测试函数命名 `test_*`
- 文件名 `test_*.py`
- 边界情况要列全（空值/异常/并发/权限）
- 人审后发现漏了，AI 补测试

---

### ⑥ TDD 绿：写实现代码（AI 写，低人工）

**做什么**：写最少的代码让所有测试转绿。

**AI 动作**：
- 按 DR 约束写实现代码
- 跑 `pytest tests/unit/test_xxx.py -v` → 转绿
- **不能改测试**——只能改实现代码让测试过

**关键约束**：
- 严格遵守 DR 的决定
- 文件头加 DR 编号注释（如 `"""xxx —— DR-NNN。"""`）

---

### ⑦ 跑全量回归（AI 做，低人工）

**做什么**：跑整个测试套件，确保没破坏现有功能。

**AI 动作**：
```bash
python scripts/check.py --suite all
```

**有红时**：
- AI 定位失败原因
- 修复实现代码
- 再跑 → 全绿

**人介入时机**：AI 修不好时，人来看。

---

### ⑧ 代码注释关联 DR（AI 做，0% 人工）

**做什么**：在文件头加 DR 编号，让代码可追溯。

**AI 动作**：
- 每个新增文件头加：`"""功能描述 —— DR-NNN。"""`
- 关键函数注释里加：`# 依据 DR-NNN 第 X 条`

---

### ⑨ 人审交付（人主导，100% 人工）

**做什么**：人确认这一轮做完了。

**人动作**：
1. 看 IR 的 Deliverables 清单，逐个打勾 `[x]`
2. 跑 `python scripts/check.py --suite all` 确认全绿
3. 看 Acceptance 项，逐个打勾
4. 把 IR 状态从 `in-progress` 改成 `delivered`

**AI 动作**：
- 把 Deliverables 和 Acceptance 的 `[ ]` 改成 `[x]`

---

### ⑩ 循环：回到 ①

**做什么**：人发现新问题，回到 ①。

---

## 人机分工总结

| 环节 | 谁主导 | 人工干预 | 产出 |
|---|---|---|---|
| ① 问题识别 | 人 | 100% | 问题描述 |
| ② 写 DR | 人 | 100% | DR-XXX.md |
| ③ 写 IR | 人定范围，AI 写骨架 | 高 | IR-XXX.md |
| ④ 更新 acceptance | 人定 criterion，AI 填 tests | 中 | acceptance.json |
| ⑤ TDD 红 | AI 写，人审 | 中 | 红的测试套件 |
| ⑥ TDD 绿 | AI | 低 | 绿的代码 |
| ⑦ 全量回归 | AI | 低 | 测试报告 |
| ⑧ 代码注释 | AI | 0% | 可追溯代码 |
| ⑨ 人审交付 | 人 | 100% | delivered IR |
| ⑩ 循环 | 人 | — | 下一轮 |

## 核心原则

1. **DR 是给 AI 的上下文，不是正确性保证**——AI 做对了没有靠测试
2. **测试的"写什么"是人定的，"怎么写"是 AI 干的**
3. **不能改测试来让代码过**——只能改代码让测试过
4. **每个文件头必须关联 DR 编号**——半年后回来知道为什么这么写
