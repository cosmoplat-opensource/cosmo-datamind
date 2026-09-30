# DR-068 · 逻辑删除的「未删除」取值按实际数据记录

- **状态 / Status**: accepted
- **日期 / Date**: 2026-10-01
- **关联 / Refs**: [[DR-063-auto-cross-check-and-qa-cleaning]]、[[DR-065-end-to-end-parity-code-contracts-and-scoped-keys]]、
  [[DR-067-scenario-regression-benchmark]];实证=`tests/unit/test_live_rows.py`、`tests/scenarios/fixtures.py`

## 上下文 / Context

逻辑删除有三处各自判断「哪些行未删除」:构建体检把 NULL、0、'0'、'N'、'n'、'false'、空串都算未删除;
复核脚本少了空串,删除列只认四个固定列名;问数的关联提示与口径校验则固定为「删除标记 = 0」。
删除标记写成 'N'/'Y' 的库上,口径校验会拦下正确的 SQL,提示给出的条件(`= 0`)会把全部行过滤掉;
用 NULL 表示未删除的库上,`= 0` 会漏掉这部分行。

## 决定 / Decision

- 删除列名规则与表示「未删除」的取值写入 `specs/models/valid_rows_rules.json` 的 `soft_delete.detect`,
  `ontology_domain` 读取后供构建体检使用;复核脚本独立发布,自带同样的常量,由单测核对一致。
- 复核认定 `soft_delete` 时,记录删除列在父表中实际出现的取值:`live_values`(未删除)与 `deleted_values`(已删除),
  随 `evidence.valid_rows` 写入本体。
- 问数的关联提示按 `live_values` 生成条件(`= 0`、`= 'N'`,含 NULL 时为 `(列 IS NULL OR 列 = …)`)。
  口径校验认可等价写法:`=` / `IN` 未删除值,`<>` / `!=` / `NOT IN` 已删除值,`COALESCE`/`IFNULL`/`NVL` 包裹后比较;
  未删除取值含 NULL 时,须写 `IS NULL` 或用 `COALESCE` 类函数保留 NULL 行。
- 早期证据没有 `live_values` 时按 `= 0` 处理,行为与此前一致。

## 后果 / Consequences

- 场景基准的元数据驱动业务库加入一张用 'N'/'Y' 标记删除的能源类型目录,检查增至 26 项:按 `= 0` 过滤的 SQL 被拦下,
  按 `= 'N'` 过滤的答案与标准答案一致。首次运行时基准检出复核结果写回本体时丢失了 `live_values`,已修正。
- 口径校验仍按正则识别写法,子查询、视图中的过滤条件不识别,会被拦下并提示改写。

## 备选与不做 / Alternatives

- **一律用 `COALESCE(列, 0) NOT IN (未删除取值)` 这类通用条件**:不做。条件冗长,且取值集合固定时仍可能与实际标记不符;
  按观察到的取值生成更准确。
