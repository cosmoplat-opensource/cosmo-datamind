# IR-024 新旧机器人图谱统一为实例展示 — DR-070 / DR-068

2026-11-11 用户授权按内容审计结论先统一图谱展示口径。

关系图统一绘制实例及实例间关系，不将类别、对象属性、数据属性定义混入画布。
新设计沿用旧 UR 的业务／技能／其他分组集合及类型中文名，不再把所有节点标为 business。
新增“参数与其他对象”范围和实例类型筛选；统计显示当前数量／实例总数。
存在业务实例时默认业务对象与配置；不存在时优先技能与流程，并说明该本体尚无业务实例。
选择空业务分组时明确说明类别声明不等于已抽取实例，不虚构或补造业务对象。
类别、属性与完整本体继续保存在原版本，可在详情页查看或下载。
已有图谱 API 的泛化节点种类保留兼容；页面从中选择 individual 投影，新设计节点附带真实 types/type_labels/group。

验证：71 项相关单元检查、14 项设计浏览器检查、18 项旧构建浏览器检查通过，已审阅测试未修改。
当前服务真实浏览器验证新图默认 57/358、旧图默认 14/437；全部视图分别 358/358、437/437。
按 SkillInvocation 筛选得到 25 个调用，按 ParameterBinding 得到 129 个绑定；空业务分组说明、节点列表和不兼容任务入口拦截均正确。
新图实例分组为 skills=57、other=301；旧图仍 business=14、skills=71、other=352。
前后 OWL 摘要一致；未修改用户本体、未重新构建或调用真实模型。
确认没有 queued/running 运行后平滑重载现有 Gunicorn，在线验证无页面异常、无 POST。
语法、ruff、mypy、diff 检查通过；本次未重复执行完整后端回归套件。

- [机器记录](IR-024-unified-instance-view-results.json)
- [单元结果](../../.check-results/IR-024-instance-view/unit.xml)
- [新设计浏览器](../../.check-results/IR-024-instance-view/design-browser.log)
- [旧本体浏览器](../../.check-results/IR-024-instance-view/legacy-browser.log)
- [分组与不变性诊断](../../.check-results/IR-024-instance-view/projection-result.json)
- [在线验证](../../.check-results/IR-024-instance-view/live-result.json)
