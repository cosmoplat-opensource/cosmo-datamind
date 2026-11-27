# IR-054 · 决策记录交付状态只写状态值

状态：delivered。关联 [DR-193](../decisions/DR-193-decision-status-format.md) 和[验收映射](../acceptance.json)中的 DR-STATUS-01。

## 验收

- [x] 新增规约检查在修改前准确报出 DR-152、DR-154、DR-160、DR-167、DR-168 和 `specs/map.md` 共 6 处不符合项，修改后通过。
- [x] 五份 DR 的交付状态改为单独的状态句；原有的适用范围、验证范围、未执行的检查和复现命令单独保留，测试数量和通过结论移除。
- [x] 其余 DR 的状态句不受影响，全部规约检查通过。

## 定向验证

在仓库根目录用 Bash 复现。

```bash
python -m pytest tests/meta/
```
