# -*- coding: utf-8 -*-
"""场景回归基准的两个合成库(DR-067)。

两种数据形态各一个小规模的库,构造实际遇到过的问题场景。数据全部合成,名称中性;
「标准答案」由生成数据时直接计算,不经被测代码。

- 多厂多层湖仓:同一实体的多厂副本(表族)、区域编号按工厂复用(租户作用域)、工单号版本重复、
  字典编码按分组复用(码表分组)、两张表的自增 id 碰巧重叠(假关系)、键名与父键不同但代码里写着连接
  (代码契约)、字段含义只写在代码注释里。
- 元数据驱动业务库:设备表按类别装着多种概念(类别拆分)、类型目录里同一编码有已删除行(逻辑删除)、
  计量关系大量引用已不存在的设备(孤儿记录)。
"""
from __future__ import annotations

import io
import random
import sqlite3
import zipfile
from collections import Counter

FACTORIES = [("f1", "t", "F1"), ("f2", "tb", "F2")]
ZONES = [f"Z{i}" for i in range(8)]


def _create(path, tables):
    con = sqlite3.connect(path)
    for name, (ddl, rows) in tables.items():
        con.execute(f'CREATE TABLE "{name}" ({ddl})')
        if rows:
            con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(rows[0]))})', rows)
    con.commit()
    con.close()


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


# ── 多厂多层湖仓 ──────────────────────────────────────────────────────

LAKEHOUSE_CODE = {
    "mapper/PassRecordMapper.xml": """<mapper namespace="trace">
<select id="listByLine" resultType="PassVo">
  SELECT t.wd_sn,
         t.wd_zone_sn,                       <!-- 线别ID -->
         z.zone_name AS wd_zone_name,        <!-- 线别名称 -->
         t.wd_order_no                       <!-- 工单号 -->
  FROM t_pass_record t
  LEFT JOIN t_line_zone z ON z.ZONE_ID = t.wd_zone_sn AND z.factory_code = t.factory_code
  LEFT JOIN sys_dict_item d ON d.code = t.wd_side_type AND d.dict_code = 'SIDE_TYPE'
  <where> t.wd_sn = #{sn} </where>
</select>
</mapper>""",
    "vo/PassVo.java": """public class PassVo {
    /**
     * 产品序列号
     */
    private String wdSn;
}""",
}


def lakehouse(path):
    rng = random.Random(7)
    tables = {}
    zone_rows = [(z, f"{fac}线{z[1:]}", fac) for _, _, fac in FACTORIES for z in ZONES]
    tables["dim_line_zone"] = ("zone_id TEXT, zone_name TEXT, factory_code TEXT", zone_rows)
    orders = [f"MO{i:04d}" for i in range(60)]
    order_rows = [(o, f"订单{o}", "2024-12-01 08:00:00") for o in orders]
    order_rows += [(o, f"订单{o}", "2024-12-05 08:00:00") for o in orders[:2]]          # 版本重复:同名再次入仓
    tables["dwd_work_order"] = ("work_order_no TEXT, order_name TEXT, create_time TEXT", order_rows)
    passes, truth = [], Counter()
    for i in range(300):
        fac = "F1" if i < 200 else "F2"
        z = ZONES[rng.randrange(len(ZONES))]
        passes.append((f"S{i:04d}", z, orders[rng.randrange(len(orders))], str(1 + i % 2), fac, "2024-12-06 10:00:00"))
        truth[f"{fac}线{z[1:]}"] += 1
    tables["dwd_pass_record"] = ("sn TEXT, zone_sn TEXT, order_no TEXT, side_type TEXT, factory_code TEXT, create_time TEXT",
                                 passes)
    dict_rows = [("SIDE_TYPE", "1", "正面"), ("SIDE_TYPE", "2", "反面"), ("LANE_TYPE", "1", "单轨"),
                 ("LANE_TYPE", "2", "双轨"), ("DEFECT_FLAG", "0", "合格"), ("DEFECT_FLAG", "1", "不良")]
    tables["sys_dict_item"] = ("dict_code TEXT, code TEXT, name TEXT", dict_rows)
    # 两张毫不相干的表,自增 id 恰好都是 1..30
    tables["dwd_job_log"] = ("id INTEGER, msg TEXT", [(i, "ok") for i in range(1, 31)])
    tables["dim_shift"] = ("id INTEGER, shift_name TEXT", [(i, f"班次{i}") for i in range(1, 31)])
    # 各厂贴源副本:同一实体按「层级_厂区_约定_词根」复制
    for f, conv, _ in FACTORIES:
        tables[f"ods_{f}_{conv}_line_zone"] = ("zone_id TEXT, zone_name TEXT, factory_code TEXT", [])
        tables[f"ods_{f}_{conv}_work_order"] = ("work_order_no TEXT, order_name TEXT, create_time TEXT", [])
        tables[f"ods_{f}_{conv}_pass_record"] = ("sn TEXT, zone_sn TEXT, order_no TEXT, side_type TEXT, factory_code TEXT", [])
        tables[f"ods_{f}_{conv}_item_master"] = ("item_code TEXT, item_name TEXT", [])
        tables[f"ods_{f}_{conv}_proc_route"] = ("route_code TEXT, route_name TEXT", [])
    _create(path, tables)
    return {
        "db": path,
        "code_zip": _zip(LAKEHOUSE_CODE),
        "proposal": {
            "objects": [{"name": "过站", "table": "dwd_pass_record", "kind": "event"},
                        {"name": "线别", "table": "dim_line_zone", "kind": "object"},
                        {"name": "工单", "table": "dwd_work_order", "kind": "object"},
                        {"name": "字典", "table": "sys_dict_item", "kind": "ice"},
                        {"name": "作业日志", "table": "dwd_job_log", "kind": "ice"},
                        {"name": "班次", "table": "dim_shift", "kind": "object"}],
            "relations": [{"source": "过站", "target": "线别", "verb": "发生于"},
                          {"source": "过站", "target": "工单", "verb": "属于", "child_key": "order_no",
                           "parent_key": "work_order_no"},
                          {"source": "过站", "target": "字典", "verb": "使用面别", "child_key": "side_type",
                           "parent_key": "code"},
                          {"source": "作业日志", "target": "班次", "verb": "记录于", "child_key": "id", "parent_key": "id"}]},
        "truth_pass_by_line": dict(truth),
        "expect": {("过站", "线别"): ("verified", "tenant_scoped"), ("过站", "工单"): ("verified", "keep_latest"),
                   ("过站", "字典"): ("verified", "scoped"), ("作业日志", "班次"): ("candidate", None)},
    }


# ── 元数据驱动业务库 ──────────────────────────────────────────────────

def business(path):
    types = [("EMTR", "电能表", 0), ("EMTR", "电能表", 1), ("FLOW", "液体流量计", 0), ("FLOW", "液体流量计", 1),
             ("INV", "光伏逆变器", 0)]
    devices = [(f"D{i}", "EMTR", "E1" if i % 2 else "E2") for i in range(40)]
    devices += [(f"F{i}", "FLOW", "E1") for i in range(12)] + [(f"V{i}", "INV", "E2") for i in range(8)]
    measure = [(i, 0, f"D{i}", "2") for i in range(20)] + [(100 + i, 0, f"GONE{i}", "2") for i in range(60)]
    tables = {
        "meta_device_type": ("dev_type_code TEXT, dev_type_name TEXT, is_del INTEGER", types),
        "inst_device": ("dev_no TEXT, mete_dev_type_code TEXT, ent_no TEXT", devices),
        "enterprise": ("ent_no TEXT, ent_name TEXT", [("E1", "甲厂"), ("E2", "乙厂")]),
        "measure_relation": ("id INTEGER, rel_obj_type INTEGER, rel_obj_no TEXT, energy_type TEXT", measure),
        # 删除标记用字符:'N' 未删除、'Y' 已删除;「电」有一条已删除的旧行
        "meta_energy_type": ("energy_type TEXT, energy_name TEXT, del_flag TEXT",
                             [("1", "水", "N"), ("2", "电", "N"), ("2", "电", "Y"), ("4", "蒸汽", "N")]),
    }
    _create(path, tables)
    return {
        "db": path,
        "proposal": {
            "objects": [{"name": "计量器具", "table": "inst_device", "kind": "object"},
                        {"name": "器具类型", "table": "meta_device_type", "kind": "ice"},
                        {"name": "企业", "table": "enterprise", "kind": "object"},
                        {"name": "计量关系", "table": "measure_relation", "kind": "ice"},
                        {"name": "能源类型", "table": "meta_energy_type", "kind": "ice"}],
            "relations": [{"source": "计量器具", "target": "器具类型", "verb": "属于类型",
                           "child_key": "mete_dev_type_code", "parent_key": "dev_type_code"},
                          {"source": "计量器具", "target": "企业", "verb": "归属", "child_key": "ent_no", "parent_key": "ent_no"},
                          {"source": "计量关系", "target": "计量器具", "verb": "引用", "child_key": "rel_obj_no",
                           "parent_key": "dev_no"},
                          {"source": "计量关系", "target": "能源类型", "verb": "计量能源", "child_key": "energy_type",
                           "parent_key": "energy_type"}]},
        "truth_devices_by_type": {"电能表": 40, "液体流量计": 12, "光伏逆变器": 8},
        "truth_measures_by_energy": {"电": len(measure)},
        "expect": {("计量器具", "器具类型"): ("verified", "soft_delete"), ("计量器具", "企业"): ("verified", None),
                   ("计量关系", "计量器具"): ("candidate", None), ("计量关系", "能源类型"): ("verified", "soft_delete")},
    }
