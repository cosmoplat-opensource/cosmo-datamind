# 限界上下文:复核与清洗(cross_check)、本体治理与问数(governance) · 决策:DR-065 · 模型:relation_lifecycle.json
功能: 先定有效行,再判唯一与重叠;问数时同样带上这些条件
  父键「不唯一」多半不是关系错了:软删除、版本重复、租户作用域、码表分组。
  复核据此升级的关系,问数时必须带上同样的条件,否则会重复计数。

  @REQ-VR-01
  场景: 区域编号按工厂复用,复核后按租户作用域成立
    假如 区域表里工厂 "F1" 与 "F2" 各有编号 A0 到 A9 的区域
    而且 过站明细全部属于工厂 "F1"
    当 交叉复核候选关系「过站 → 区域」
    那么 复核结论为 "valid_rows_candidate",规则为 "tenant_scoped"
    而且 应用复核后关系从 "candidate" 变为 "verified"

  @REQ-VR-02
  场景: 按租户作用域成立的关系,问数不带工厂会被拦下
    假如 本体里「过站 → 区域」按 "factory_code" 分租户成立
    当 问数生成的 SQL 为 "SELECT a.ca_name, COUNT(*) FROM wip w JOIN area a ON a.ca_id = w.area_sn GROUP BY 1"
    那么 口径校验拦截,原因提到 "factory_code"
    当 问数生成的 SQL 为 "SELECT a.ca_name, COUNT(*) FROM wip w JOIN area a ON a.ca_id = w.area_sn AND a.factory_code = w.factory_code GROUP BY 1"
    那么 口径校验放行

  @REQ-VR-03
  场景: 类型目录有已删除行,问数须排除已删除行
    假如 本体里「设备 → 设备类型」在排除已删除行后成立
    当 问数生成的 SQL 为 "SELECT t.name, COUNT(*) FROM device d JOIN device_type t ON t.type_code = d.type_code GROUP BY 1"
    那么 口径校验拦截,原因提到 "is_del"
    而且 给模型的 JOIN 提示包含 "device_type.is_del = 0"
