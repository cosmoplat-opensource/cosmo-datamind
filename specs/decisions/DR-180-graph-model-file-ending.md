# DR-180 · 图谱模型源码的文件结尾

状态：accepted。
实现交付状态：delivered。

## 背景

交互式本体图谱的 `model.js` 在最后一个右花括号之后多留了一行空行。模块仍可加载，但 Git 在检查相对主分支的新增文件时报告 `new blank line at EOF`。

## 决定

最后一行代码保留行尾换行符，并在此结束文件。验收同时检查文件结尾和图谱模型的分页展开行为，见 [IR-041](../iterations/IR-041-graph-model-file-ending.md)。
