#!/usr/bin/env python3
"""Master data for the 108-table demo database (simulation generator, DR-076).

One coherent business: a modified-plastics compounder (示范新材料科技有限公司) with four product
lines, real compound recipes (kg per ton of product), twin-screw compounding lines grouped into
workshops and factories, a functional organisation (sales regions plus production, quality,
equipment, procurement, warehouse, finance, HR, safety, planning and R&D), customers in
plastics-consuming industries, and resin/additive suppliers.  All names are fictitious
(示范×× for the enterprise, 示例+行业+字母 for business partners).  Standard library only.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

START = date(2025, 1, 1)
END = date(2026, 6, 30)
DAYS = (END - START).days + 1
VAT = 0.13


def days() -> list[date]:
    return [START + timedelta(days=i) for i in range(DAYS)]


# ───────── 组织 ─────────
ENTERPRISE = {"enterprise_id": 1, "enterprise_name": "示范新材料科技有限公司", "credit_code": "91370200MA00000001",
              "legal_rep": "示范法人", "address": "山东省青岛市黄岛区示范路1号", "industry": "改性塑料制造"}
BUSINESS_UNITS = [(1, "通用改性事业部", "profit_center"), (2, "工程塑料事业部", "profit_center"), (3, "特种材料事业部", "profit_center")]
FACTORIES = [(1, "青岛一厂", 1, "青岛市黄岛区", 52000.0, date(2012, 6, 18)),
             (2, "青岛二厂", 2, "青岛市城阳区", 36000.0, date(2016, 9, 1)),
             (3, "合肥工厂", 3, "合肥市经开区", 28000.0, date(2019, 4, 26))]
WORKSHOPS = [(1, "PP改性车间", 1, "青岛一厂A栋"), (2, "PA改性车间", 1, "青岛一厂B栋"), (3, "ABS/PC合金车间", 2, "青岛二厂A栋"),
             (4, "阻燃材料车间", 2, "青岛二厂B栋"), (5, "特种工程塑料车间", 3, "合肥工厂A栋")]
REGIONS = [(1, "华东"), (2, "华南"), (3, "华北"), (4, "西南"), (5, "华中"), (6, "西北")]
# 部门:6 个区域销售部 + 11 个职能部门(只有销售部门带区域)
DEPARTMENTS = [(1, "华东销售部", 1), (2, "华南销售部", 2), (3, "华北销售部", 3), (4, "西南销售部", 4), (5, "华中销售部", 5),
               (6, "西北销售部", 6), (7, "生产部", None), (8, "质量部", None), (9, "设备部", None), (10, "采购部", None),
               (11, "仓储物流部", None), (12, "财务部", None), (13, "人力资源部", None), (14, "安全环保部", None),
               (15, "计划部", None), (16, "研发中心", None), (17, "总经办", None)]

# ───────── 产品与配方 ─────────
PRODUCT_LINES = [(1, "PP改性系列"), (2, "PA改性系列"), (3, "ABS/PC合金系列"), (4, "特种工程塑料系列")]
MATERIALS = [  # id, code, name, category, spec, unit_price(元/吨,不含税), safety_stock(吨), lead_days, supplier
    (1, "MAT001", "PP均聚 T30S", 1, "熔指3.0 g/10min", 7600.0, 80.0, 5, 1),
    (2, "MAT002", "PP共聚 K8003", 1, "熔指2.5 g/10min,抗冲共聚", 8300.0, 60.0, 5, 1),
    (3, "MAT003", "PA6切片 YH800", 2, "相对粘度2.8", 13800.0, 40.0, 7, 2),
    (4, "MAT004", "PA66切片 EPR27", 2, "相对粘度2.7", 19500.0, 30.0, 10, 3),
    (5, "MAT005", "ABS 757K", 2, "通用注塑级", 11500.0, 40.0, 7, 4),
    (6, "MAT006", "PC 1100", 2, "中粘度", 15800.0, 30.0, 10, 4),
    (7, "MAT007", "PBT 1100-211", 2, "特性粘度1.0", 12600.0, 20.0, 10, 3),
    (8, "MAT008", "PPS 1130A", 3, "线性,中粘度", 42000.0, 8.0, 21, 5),
    (9, "MAT009", "PPA AE-4142", 3, "半芳香聚酰胺", 46000.0, 6.0, 21, 5),
    (10, "MAT010", "无碱短切玻纤 ECS10-4.5", 4, "直径10μm,长4.5mm", 5600.0, 120.0, 4, 6),
    (11, "MAT011", "滑石粉 TA-1250", 4, "1250目", 2100.0, 60.0, 4, 7),
    (12, "MAT012", "溴系阻燃剂 DBDPE", 5, "溴含量≥82%", 29000.0, 10.0, 10, 8),
    (13, "MAT013", "三氧化二锑", 5, "纯度≥99.5%", 68000.0, 4.0, 14, 9),
    (14, "MAT014", "POE增韧剂 8150", 5, "辛烯含量25%", 16500.0, 15.0, 10, 10),
    (15, "MAT015", "复配抗氧剂 1010/168", 5, "1:1 复配", 38000.0, 3.0, 14, 11),
]
MATERIAL_CATEGORIES = [(1, "通用树脂", 6), (2, "工程塑料树脂", 6), (3, "特种工程塑料树脂", 6), (4, "增强与填充", None), (5, "助剂", None),
                       (6, "树脂", None)]
# 产品:id, 名称, 产品线, 规格, 配方 {物料: kg/吨成品}, 指导价(元/吨,不含税), 可生产车间
PRODUCTS = [
    (1, "PP-T20 滑石粉填充PP", 1, "PP+20%滑石粉,家电结构件", {1: 778, 11: 200, 15: 4, 14: 0}, 9800.0, [1]),
    (2, "PP-GF30 玻纤增强PP", 1, "PP+30%玻纤,汽车风扇罩", {1: 690, 10: 300, 15: 4}, 11800.0, [1]),
    (3, "PP-EP 增韧PP", 1, "共聚PP+POE,保险杠", {2: 846, 14: 150, 15: 4}, 11200.0, [1]),
    (4, "PP-FR V0 阻燃PP", 1, "UL94 V-0,电器外壳", {1: 716, 12: 200, 13: 60, 15: 4, 11: 20}, 16500.0, [4]),
    (5, "PA6-GF30 增强尼龙6", 2, "PA6+30%玻纤,电动工具", {3: 692, 10: 300, 15: 4, 14: 4}, 19800.0, [2]),
    (6, "PA66-GF30 增强尼龙66", 2, "PA66+30%玻纤,汽车发动机周边", {4: 692, 10: 300, 15: 4, 14: 4}, 25500.0, [2]),
    (7, "PA6-ST 超韧尼龙6", 2, "PA6+POE,运动器材", {3: 816, 14: 180, 15: 4}, 18600.0, [2]),
    (8, "PA66-FR 阻燃尼龙66", 2, "UL94 V-0,连接器", {4: 702, 12: 180, 13: 60, 10: 54, 15: 4}, 31000.0, [4]),
    (9, "ABS-HG 高光ABS", 3, "高光泽,家电面板", {5: 990, 15: 4, 14: 6}, 13600.0, [3]),
    (10, "PC/ABS 7000 合金", 3, "PC/ABS=70/30,笔记本外壳", {6: 680, 5: 290, 14: 26, 15: 4}, 18900.0, [3]),
    (11, "ABS-FR V0 阻燃ABS", 3, "UL94 V-0,插座面板", {5: 776, 12: 160, 13: 60, 15: 4}, 17800.0, [4]),
    (12, "PC-GF20 增强PC", 3, "PC+20%玻纤,结构件", {6: 796, 10: 200, 15: 4}, 21500.0, [3]),
    (13, "PBT-GF30 增强PBT", 4, "PBT+30%玻纤,连接器", {7: 696, 10: 300, 15: 4}, 18200.0, [5]),
    (14, "PPS-GF40 增强PPS", 4, "PPS+40%玻纤,耐高温结构件", {8: 596, 10: 400, 15: 4}, 39000.0, [5]),
    (15, "PPA-GF35 高温尼龙", 4, "PPA+35%玻纤,汽车热管理", {9: 646, 10: 350, 15: 4}, 48000.0, [5]),
]
SCRAP_RATE = 0.012          # 配方外的工艺损耗(开机料、过渡料)
CONVERSION_COST = {1: 900.0, 2: 1300.0, 3: 1100.0, 4: 1500.0}   # 元/吨,按产品线(人工+制造费用)
# 市场份额权重:PP 约四成、PA 约四分之一、合金约两成、阻燃一成、特塑约 5%
PRODUCT_WEIGHT = {1: 18.6, 2: 16, 3: 16, 4: 2, 5: 22, 6: 17.6, 7: 11, 8: 1, 9: 5, 10: 5, 11: 1.5, 12: 2.2, 13: 1.25, 14: 0.6, 15: 0.5}

# 质量特性:名称, 单位, (名义, 下限, 上限) 按产品
def rework_window(end: datetime, runtime_hours: float) -> tuple[datetime, datetime, date]:
    """不合格批次的返工:完工 6 小时后开始,用时为原运行时长的 30%(至少 2 小时),完工次日放行。"""
    start = end + timedelta(hours=6)
    finish = start + timedelta(hours=max(2.0, runtime_hours * 0.3))
    return start, finish, finish.date() + timedelta(days=1)


def quality_spec(prod_id: int) -> list[tuple[str, str, float, float, float]]:
    name = PRODUCTS[prod_id - 1][1]
    filled = "GF" in name or "T20" in name
    tensile = {1: 32, 2: 85, 3: 22, 4: 30, 5: 150, 6: 175, 7: 55, 8: 120, 9: 45, 10: 55, 11: 40, 12: 95, 13: 125, 14: 160, 15: 190}[prod_id]
    impact = {1: 3.5, 2: 9, 3: 40, 4: 4, 5: 11, 6: 12, 7: 85, 8: 8, 9: 20, 10: 50, 11: 12, 12: 10, 13: 8, 14: 9, 15: 10}[prod_id]
    mfr = {1: 12, 2: 6, 3: 15, 4: 10, 5: 20, 6: 18, 7: 25, 8: 15, 9: 22, 10: 18, 11: 20, 12: 9, 13: 14, 14: 30, 15: 16}[prod_id]
    density = {1: 1.05, 2: 1.12, 3: 0.89, 4: 1.18, 5: 1.36, 6: 1.37, 7: 1.05, 8: 1.55, 9: 1.04, 10: 1.15, 11: 1.20, 12: 1.33, 13: 1.53, 14: 1.65, 15: 1.47}[prod_id]
    spec = [("拉伸强度", "MPa", tensile, tensile * 0.9, tensile * 1.15),
            ("缺口冲击强度", "kJ/m²", impact, impact * 0.8, impact * 1.3),
            ("熔体流动速率", "g/10min", mfr, mfr * 0.75, mfr * 1.25),
            ("密度", "g/cm³", density, round(density - 0.02, 3), round(density + 0.02, 3))]
    if filled:
        ash = 20.0 if "T20" in name else float(name.split("GF")[1][:2])
        spec.append(("灰分", "%", ash, ash - 1.5, ash + 1.5))
    if "FR" in name:
        spec.append(("阻燃等级(1.6mm)", "级", 0.0, 0.0, 0.0))
    return spec


SUPPLIER_CATEGORIES = [(1, "树脂供应商", None), (2, "助剂供应商", None), (3, "增强填充供应商", None), (4, "包装材料供应商", None),
                       (11, "通用树脂", 1), (12, "工程塑料树脂", 1), (21, "阻燃剂", 2), (22, "增韧剂与抗氧剂", 2)]
SUPPLIERS = [  # id, 名称, 类别, 省, 市, 信用, 合作起始, 账期(天), 准时率, 合格率
    (1, "示例化工A销售有限公司", 11, "山东", "淄博", "A", date(2013, 3, 1), 30, 0.95, 0.99),
    (2, "示例化工B集团股份有限公司", 12, "浙江", "宁波", "A", date(2014, 6, 1), 45, 0.92, 0.985),
    (3, "示例化工C(上海)有限公司", 12, "上海", "上海", "AA", date(2015, 2, 1), 60, 0.96, 0.995),
    (4, "示例材料A股份有限公司", 12, "江苏", "镇江", "A", date(2016, 8, 1), 45, 0.9, 0.98),
    (5, "示例特材B有限公司", 12, "广东", "珠海", "AA", date(2019, 5, 1), 60, 0.88, 0.99),
    (6, "示例玻纤C股份有限公司", 3, "浙江", "桐乡", "A", date(2013, 1, 1), 30, 0.97, 0.99),
    (7, "示例矿业D有限公司", 3, "辽宁", "海城", "B", date(2017, 4, 1), 30, 0.85, 0.96),
    (8, "示例阻燃E有限公司", 21, "山东", "潍坊", "A", date(2016, 3, 1), 45, 0.93, 0.985),
    (9, "示例锑业F有限公司", 21, "湖南", "娄底", "B", date(2018, 7, 1), 30, 0.86, 0.97),
    (10, "示例弹性体G有限公司", 22, "江苏", "南京", "A", date(2015, 9, 1), 45, 0.94, 0.99),
    (11, "示例助剂H有限公司", 22, "江苏", "南通", "A", date(2014, 11, 1), 30, 0.95, 0.99),
    (12, "示例包装I有限公司", 4, "山东", "青岛", "B", date(2018, 1, 1), 30, 0.97, 0.995),
]
CUSTOMER_INDUSTRY_PRODUCTS = {
    "汽车": [2, 3, 6, 15, 13, 14, 10],
    "家电": [1, 4, 9, 11, 3, 10],
    "电子电气": [8, 13, 11, 10, 12, 4, 14],
    "电动工具": [5, 7, 6, 12],
    "建材": [1, 4, 11, 9],
}
# 客户:名称, 行业, 省, 市, 区域, 规模(吨/月), 付款条件(天), 付款行为(拖延天数均值), 银承比例, 生命周期
CUSTOMERS = [
    ("示例汽车A有限公司", "汽车", "上海", "上海", 1, 260, 60, 8, 0.4, "active"),
    ("示例汽车B股份有限公司", "汽车", "广东", "广州", 2, 220, 90, 15, 0.5, "active"),
    ("示例汽车C有限公司", "汽车", "广西", "柳州", 2, 150, 60, 25, 0.3, "risk"),
    ("示例汽车D零部件有限公司", "汽车", "湖北", "武汉", 5, 180, 60, 5, 0.3, "active"),
    ("示例汽车E有限公司", "汽车", "重庆", "重庆", 4, 140, 90, 10, 0.6, "active"),
    ("示例家电A集团股份有限公司", "家电", "广东", "佛山", 2, 300, 60, 3, 0.6, "active"),
    ("示例家电B股份有限公司", "家电", "广东", "珠海", 2, 200, 60, 6, 0.5, "active"),
    ("示范智家股份有限公司", "家电", "山东", "青岛", 3, 280, 30, 2, 0.2, "active"),
    ("示例家电C有限公司", "家电", "安徽", "合肥", 1, 160, 60, 12, 0.4, "dormant"),
    ("示例家电D有限公司", "家电", "浙江", "宁波", 1, 120, 30, 4, 0.3, "active"),
    ("示例电子A集团有限公司", "电子电气", "广东", "深圳", 2, 90, 60, 7, 0.2, "active"),
    ("示例电子B股份有限公司", "电子电气", "江苏", "苏州", 1, 110, 60, 9, 0.3, "active"),
    ("示例电气C有限公司", "电子电气", "浙江", "温州", 1, 80, 30, 3, 0.1, "active"),
    ("示例通讯D有限公司", "电子电气", "广东", "东莞", 2, 60, 60, 30, 0.2, "lost"),
    ("示例电子E有限公司", "电子电气", "四川", "成都", 4, 70, 60, 8, 0.2, "active"),
    ("示例工具A有限公司", "电动工具", "江苏", "常州", 1, 100, 30, 4, 0.2, "active"),
    ("示例工具B股份有限公司", "电动工具", "浙江", "金华", 1, 90, 60, 6, 0.3, "active"),
    ("示例工具C有限公司", "电动工具", "山东", "济南", 3, 60, 30, 5, 0.2, "new"),
    ("示例建材A有限公司", "建材", "河南", "郑州", 5, 120, 60, 20, 0.5, "active"),
    ("示例建材B股份有限公司", "建材", "陕西", "西安", 6, 90, 60, 18, 0.4, "active"),
    ("示例建材C有限公司", "建材", "河北", "石家庄", 3, 70, 30, 40, 0.3, "risk"),
    ("示例电气F有限公司", "电子电气", "湖南", "长沙", 5, 75, 60, 6, 0.2, "active"),
    ("示例汽车F有限公司", "汽车", "陕西", "西安", 6, 110, 60, 9, 0.4, "active"),
    ("示例家电E有限公司", "家电", "四川", "绵阳", 4, 100, 60, 7, 0.3, "new"),
    ("示例电子G有限公司", "电子电气", "北京", "北京", 3, 85, 60, 6, 0.2, "active"),
]

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘")
GIVEN = ["伟", "芳", "娜", "敏", "静", "磊", "强", "军", "洋", "勇", "艳", "杰", "涛", "明", "超", "霞", "平", "刚", "桂英", "建华",
         "志强", "海燕", "晓东", "丽娟", "文博", "子涵", "雨辰", "思远", "宇航", "佳怡", "浩然", "欣怡", "俊杰", "梦瑶", "嘉豪", "雅琪"]

# 各线性能稼动率基线(老线、难加工料的线更低)
LINE_PERF = {1: 0.90, 2: 0.88, 3: 0.86, 4: 0.85, 5: 0.84, 6: 0.82, 7: 0.86, 8: 0.84, 9: 0.80, 10: 0.78, 11: 0.77, 12: 0.75}
EQUIPMENT_TYPES = [(1, "双螺杆挤出机", "生产设备", "示例机械A", "TSE-65"), (2, "高速混合机", "辅助设备", "示例机械B", "SHR-500"),
                   (3, "失重式喂料机", "辅助设备", "示例机械C", "LWF-150"), (4, "切粒机", "辅助设备", "示例机械D", "SP-300"),
                   (5, "除湿干燥机", "辅助设备", "示例机械E", "DH-400"), (6, "自动包装机", "辅助设备", "示例机械F", "BZ-25")]
# 传感器:按设备类型三只;名称, 类型, 单位, 正常区间, 报警上限
SENSORS_BY_TYPE = {
    1: [("熔体温度", "温度", "℃", 225.0, 275.0, 290.0), ("熔体压力", "压力", "MPa", 3.0, 9.0, 11.0), ("主机电流", "电流", "A", 180.0, 320.0, 360.0)],
    2: [("混合温度", "温度", "℃", 30.0, 70.0, 85.0), ("主轴电流", "电流", "A", 40.0, 90.0, 110.0), ("轴承振动", "振动", "mm/s", 1.0, 4.5, 7.1)],
    3: [("喂料速率", "流量", "kg/h", 600.0, 1600.0, 1800.0), ("料斗料位", "料位", "%", 20.0, 90.0, 98.0), ("电机电流", "电流", "A", 5.0, 15.0, 18.0)],
    4: [("切刀转速", "转速", "rpm", 600.0, 1200.0, 1350.0), ("电机电流", "电流", "A", 20.0, 45.0, 55.0), ("轴承振动", "振动", "mm/s", 1.0, 4.5, 7.1)],
    5: [("露点温度", "温度", "℃", -45.0, -30.0, -20.0), ("干燥温度", "温度", "℃", 75.0, 95.0, 110.0), ("风机电流", "电流", "A", 8.0, 20.0, 24.0)],
    6: [("称重偏差", "重量", "kg", -0.1, 0.1, 0.2), ("包装速度", "速度", "包/h", 150.0, 300.0, 330.0), ("电机电流", "电流", "A", 3.0, 9.0, 11.0)],
}
# 各产品的熔体温度工艺窗口(℃):按基体树脂,阻燃牌号因阻燃剂热敏取下限侧
PRODUCT_MELT = {1: (200.0, 230.0), 2: (210.0, 240.0), 3: (200.0, 230.0), 4: (190.0, 210.0), 5: (235.0, 260.0), 6: (270.0, 290.0),
                7: (230.0, 255.0), 8: (265.0, 280.0), 9: (210.0, 240.0), 10: (240.0, 270.0), 11: (200.0, 220.0), 12: (280.0, 300.0),
                13: (240.0, 260.0), 14: (300.0, 330.0), 15: (320.0, 340.0)}
# 车间熔体温度传感器的正常区间覆盖本车间所产各牌号(下限 − 5、上限 + 5),报警限再高 15 ℃
MELT_BY_WORKSHOP = {ws: (min(PRODUCT_MELT[p][0] for p in PRODUCT_MELT if PRODUCTS[p - 1][6][0] == ws) - 5.0,
                         max(PRODUCT_MELT[p][1] for p in PRODUCT_MELT if PRODUCTS[p - 1][6][0] == ws) + 5.0,
                         max(PRODUCT_MELT[p][1] for p in PRODUCT_MELT if PRODUCTS[p - 1][6][0] == ws) + 20.0) for ws in range(1, 6)}


def sensor_range(type_id: int, name: str, workshop_id: int) -> tuple[float, float, float]:
    """传感器的正常区间与报警上限;熔体温度按车间所加工的树脂取值。"""
    if name == "熔体温度":
        return MELT_BY_WORKSHOP[workshop_id]
    for n, _t, _u, lo, hi, alarm in SENSORS_BY_TYPE[type_id]:
        if n == name:
            return lo, hi, alarm
    raise KeyError(name)


SPARE_PARTS = [(1, "SP001", "挤出机螺纹元件", 1, "件", 18500.0, 4, 12), (2, "SP002", "挤出机加热圈", 1, "个", 680.0, 20, 60),
               (3, "SP003", "熔体压力传感器", 1, "个", 4200.0, 4, 12), (4, "SP004", "混合机刮刀", 2, "套", 2600.0, 3, 10),
               (5, "SP005", "喂料机螺杆", 3, "根", 5200.0, 2, 8), (6, "SP006", "切粒机动刀", 4, "把", 3600.0, 6, 20),
               (7, "SP007", "切粒机定刀", 4, "把", 2400.0, 6, 20), (8, "SP008", "干燥机分子筛", 5, "套", 9800.0, 2, 6),
               (9, "SP009", "包装机皮带", 6, "条", 520.0, 6, 24), (10, "SP010", "通用轴承 6312", 2, "个", 360.0, 20, 80)]
DOWNTIME_REASONS = [(1, "D001", "设备故障", "设备"), (2, "D002", "换产清机", "工艺"), (3, "D003", "计划保养", "计划"),
                    (4, "D004", "物料短缺", "物料"), (5, "D005", "质量异常", "质量"), (6, "D006", "能源中断", "能源"),
                    (7, "D007", "人员不足", "人力"), (8, "D008", "滤网更换", "工艺")]
HAZARDS = [(1, "HZ001", "熔体高温烫伤", "物理性", "PP改性车间挤出区", "medium"), (2, "HZ002", "切粒机刀具伤害", "物理性", "PA改性车间切粒区", "high"),
           (3, "HZ003", "粉尘爆炸", "化学性", "阻燃材料车间配料间", "high"), (4, "HZ004", "叉车碰撞", "物理性", "ABS/PC合金车间成品区", "medium"),
           (5, "HZ005", "阻燃剂粉尘吸入", "化学性", "阻燃材料车间配料间", "medium"), (6, "HZ006", "噪声暴露", "物理性", "PP改性车间挤出区", "low"),
           (7, "HZ007", "高处坠落", "物理性", "PA改性车间料仓平台", "high"), (8, "HZ008", "电气触电", "物理性", "特种工程塑料车间配电室", "high"),
           (9, "HZ009", "重复性搬运劳损", "人机工程", "ABS/PC合金车间包装区", "low"), (10, "HZ010", "有限空间窒息", "化学性", "特种工程塑料车间料仓内部", "high")]
HAZARD_WORKSHOP = {1: 1, 2: 2, 3: 4, 4: 3, 5: 4, 6: 1, 7: 2, 8: 5, 9: 3, 10: 5}
RISK_LEVELS = [(1, "L1", "可接受", "风险低,现有控制措施有效", "保持现有控制,定期复查"),
               (2, "L2", "可容忍", "风险中等,需关注", "制定改进措施,三个月内落实"),
               (3, "L3", "不可容忍", "风险较高,须限期整改", "立即制定整改计划,一个月内完成"),
               (4, "L4", "重大", "重大风险,可能导致严重后果", "立即停止作业,整改验证后方可恢复")]
ACCOUNTS = [(1, "1001", "库存现金", "asset", None, 1, "debit"), (2, "1002", "银行存款", "asset", None, 1, "debit"),
            (3, "1122", "应收账款", "asset", None, 1, "debit"), (4, "1403", "原材料", "asset", None, 1, "debit"),
            (5, "1405", "库存商品", "asset", None, 1, "debit"), (6, "1601", "固定资产", "asset", None, 1, "debit"),
            (7, "1602", "累计折旧", "asset", None, 1, "credit"), (8, "2202", "应付账款", "liability", None, 1, "credit"),
            (9, "2221", "应交税费", "liability", None, 1, "credit"), (10, "4001", "实收资本", "equity", None, 1, "credit"),
            (11, "5001", "生产成本", "cost", None, 1, "debit"), (12, "5101", "制造费用", "cost", None, 1, "debit"),
            (13, "6001", "主营业务收入", "revenue", None, 1, "credit"), (14, "6401", "主营业务成本", "cost", None, 1, "debit"),
            (15, "6601", "销售费用", "expense", None, 1, "debit"), (16, "6602", "管理费用", "expense", None, 1, "debit"),
            (17, "1121", "应收票据", "asset", None, 1, "debit"), (18, "1604", "在建工程", "asset", None, 1, "debit"),
            (19, "2211", "应付职工薪酬", "liability", None, 1, "credit")]
# 职能岗位编制(部门, 岗位, 人数);销售六区各 1 名经理、3 名代表在前,接替离职者的新代表在最后
ROLE_PLAN = [(7, "生产主管", 4), (7, "班组长", 48), (8, "质检员", 5), (8, "质量工程师", 2), (9, "设备工程师", 3),
             (9, "维修技师", 15), (10, "采购员", 3), (11, "仓管员", 3), (12, "会计", 3), (13, "人事专员", 2),
             (14, "安全工程师", 3), (15, "计划员", 2), (16, "研发工程师", 3), (17, "总经理", 1)]


def employee_roles(emp_ids: list[int]) -> dict[int, str]:
    """按建档顺序还原员工岗位(与 build_master 的建档顺序一致)。"""
    order = [r for _ in range(6) for r in ("销售经理", "销售代表", "销售代表", "销售代表")]
    order += [role for _d, role, n in ROLE_PLAN for _ in range(n)]
    ids = sorted(emp_ids)
    return {eid: (order[k] if k < len(order) else "销售代表") for k, eid in enumerate(ids)}


@dataclass
class Master:
    rng: random.Random
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    roles: dict[int, str] = field(default_factory=dict)              # 员工 → 岗位
    leaver: dict[str, Any] = field(default_factory=dict)             # 离职的销售代表与接替者
    recipes: dict[int, dict[int, float]] = field(default_factory=dict)
    line_products: dict[int, list[int]] = field(default_factory=dict)
    tech_factory: dict[int, int] = field(default_factory=dict)     # 维修技师 → 所在工厂

    def put(self, table: str, rows: list[dict[str, Any]]) -> None:
        self.tables[table] = rows

    def get(self, table: str) -> list[dict[str, Any]]:
        return self.tables[table]

    def by(self, table: str, key: str) -> dict[Any, dict[str, Any]]:
        return {r[key]: r for r in self.tables[table]}


def build_master(seed: int) -> Master:
    rng = random.Random(f"{seed}:master")
    m = Master(rng)
    m.put("dim_enterprise", [dict(ENTERPRISE)])
    m.put("dim_business_unit", [{"bu_id": i, "bu_name": n, "enterprise_id": 1, "bu_type": t} for i, n, t in BUSINESS_UNITS])
    m.put("dim_factory", [{"factory_id": i, "factory_name": n, "bu_id": b, "location": loc, "area_sqm": a, "established_date": d.isoformat()}
                          for i, n, b, loc, a, d in FACTORIES])
    m.put("dim_workshop", [{"workshop_id": i, "workshop_name": n, "location": loc} for i, n, _f, loc in WORKSHOPS])
    m.put("dim_region", [{"region_id": i, "region_name": n} for i, n in REGIONS])
    m.put("dim_department", [{"dept_id": i, "dept_name": n, "region_id": r} for i, n, r in DEPARTMENTS])
    m.put("dim_coa", [{"coa_id": 1, "coa_code": "CN_GAAP_2006", "coa_name": "中国企业会计准则科目表", "accounting_standard": "企业会计准则"}])
    m.put("dim_account", [{"account_id": a, "account_code": c, "account_name": n, "account_type": t, "parent_id": p, "is_leaf": leaf, "direction": d}
                          for a, c, n, t, p, leaf, d in ACCOUNTS])

    # 员工:每个区域 4 名销售(含 1 名经理),其余为各职能岗位
    used: set[str] = set()

    def name() -> str:
        while True:
            n = rng.choice(SURNAMES) + rng.choice(GIVEN)
            if n not in used:
                used.add(n)
                return n
    emps: list[dict[str, Any]] = []
    eid = 0
    roles: dict[int, str] = {}

    def add(dept: int, role: str, hire_from: int, hire_to: int) -> int:
        nonlocal eid
        eid += 1
        hire = date(hire_from, 1, 1) + timedelta(days=rng.randrange((date(hire_to, 12, 31) - date(hire_from, 1, 1)).days))
        emps.append({"emp_id": eid, "emp_name": name(), "dept_id": dept, "hire_date": hire.isoformat(), "status": "active"})
        roles[eid] = role
        return eid
    for region in range(1, 7):
        add(region, "销售经理", 2012, 2018)
        for _ in range(3):
            add(region, "销售代表", 2015, 2023)
    for dept, role, n in ROLE_PLAN:
        for _ in range(n):
            add(dept, role, 2010, 2024)
    # 一名销售代表 2026-03 离职,一名 2025-07 入职接替(用于检验离职人员与新入职人员的统计)
    reps = [e for e in emps if roles[e["emp_id"]] == "销售代表"]
    leaver = reps[5]
    leaver["status"] = "inactive"
    newcomer = add(leaver["dept_id"], "销售代表", 2025, 2025)
    nc = next(e for e in emps if e["emp_id"] == newcomer)
    nc["hire_date"] = date(2025, 7, 1).isoformat()
    m.put("dim_employee", emps)
    m.roles = roles
    techs = [e["emp_id"] for e in emps if roles[e["emp_id"]] == "维修技师"]
    m.tech_factory = {eid: (1 if k < 6 else 2 if k < 11 else 3) for k, eid in enumerate(techs)}   # 维修班组按厂配置:6/5/4 人                                      
    m.leaver = {"emp_id": leaver["emp_id"], "date": date(2026, 3, 31), "successor": newcomer} 

    m.put("dim_product_line", [{"prod_line_id": i, "prod_line_name": n} for i, n in PRODUCT_LINES])
    m.put("dim_material_category", [{"category_id": i, "category_name": n, "parent_id": p} for i, n, p in MATERIAL_CATEGORIES])
    m.put("dim_supplier_category", [{"category_id": i, "category_name": n, "parent_id": p} for i, n, p in SUPPLIER_CATEGORIES])
    sup_rows = []
    for i, sname, cat, prov, city, credit, since, _terms, _ot, _q in SUPPLIERS:
        sup_rows.append({"supplier_id": i, "supplier_code": f"SUP{i:03d}", "supplier_name": sname, "category_id": cat, "province": prov,
                         "city": city, "contact_person": rng.choice(SURNAMES) + "经理", "contact_phone": f"0{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
                         "credit_level": credit, "cooperation_since": since.isoformat(), "status": "active"})
    m.put("dim_supplier", sup_rows)
    m.put("dim_material", [{"material_id": i, "material_code": c, "material_name": n, "category_id": cat, "spec": spec, "unit": "吨",
                            "unit_price": p, "safety_stock": ss, "lead_time_days": lt, "supplier_id": sup, "status": "active"}
                           for i, c, n, cat, spec, p, ss, lt, sup in MATERIALS])
    mat = {r["material_id"]: r for r in m.get("dim_material")}
    prods, boms, routings = [], [], []
    bom_lines: list[dict[str, Any]] = []
    for pid, pname, pl, spec, raw_recipe, guide, _ws in PRODUCTS:
        total = sum(v for v in raw_recipe.values() if v)
        recipe = {k: v * 1000.0 / total for k, v in raw_recipe.items() if v}   # 配方归一到 1000 kg/吨
        mat_cost = sum(qty / 1000.0 * mat[k]["unit_price"] for k, qty in recipe.items())
        tech = round(mat_cost + CONVERSION_COST[pl] * 0.9, 2)
        std = round(mat_cost * (1 + SCRAP_RATE) + CONVERSION_COST[pl], 2)
        guide = max(guide, math.ceil(std * 1.10 / 100) * 100)      # 指导价不低于标准成本的 110%
        prods.append({"prod_id": pid, "prod_name": pname.split(" ")[0], "prod_line_id": pl, "spec": f"{pname.split(' ', 1)[1]};{spec}",
                      "unit": "吨", "standard_cost": std, "tech_cost": tech, "guide_price": guide})
        boms.append({"bom_id": pid, "bom_no": f"BOM-P{pid:02d}-V1", "product_id": pid, "version": "V1.0", "status": "active",
                     "effective_date": date(2024, 1, 1).isoformat(), "yield_rate": round(100 / (1 + SCRAP_RATE), 2)})
        for n, (k, qty) in enumerate(sorted(recipe.items(), key=lambda x: -x[1]), start=1):
            bom_lines.append({"bom_line_id": len(bom_lines) + 1, "bom_id": pid, "line_no": n, "material_id": k,
                              "qty_per_ton": round(qty, 4), "scrap_rate": round(SCRAP_RATE * 100, 2)})
        routings.append({"routing_id": pid, "routing_no": f"RT-P{pid:02d}-V1", "product_id": pid, "version": "V1.0", "status": "active"})
    m.put("dim_product", prods)
    m.put("dim_bom", boms)
    m.put("dim_bom_line", bom_lines)
    m.put("dim_routing", routings)
    m.recipes = {pid: {l["material_id"]: l["qty_per_ton"] for l in bom_lines if l["bom_id"] == pid} for pid in range(1, 16)}
    pricing = []
    for p in prods:
        for y, factor, eff, exp in ((2025, 1.0, date(2025, 1, 1), date(2025, 12, 31)), (2026, 1.03, date(2026, 1, 1), date(2026, 12, 31))):
            pricing.append({"pricing_id": len(pricing) + 1, "product_id": p["prod_id"], "price_type": "standard",
                            "unit_price": round(p["guide_price"] * factor, 2), "currency": "CNY", "effective_date": eff.isoformat(),
                            "expired_date": exp.isoformat(), "status": "active" if y == 2026 else "inactive"})
    m.put("dim_pricing", pricing)

    # 产线、设备、传感器、工作中心、工装、班组
    line_rows, eq_rows, sensor_rows, wc_rows, tool_rows, team_rows = [], [], [], [], [], []
    lines_plan = [(1, "PP挤出1线", 1, 2.0), (2, "PP挤出2线", 1, 1.8), (3, "PP挤出3线", 1, 1.6), (4, "PA挤出1线", 2, 1.3),
                  (5, "PA挤出2线", 2, 1.1), (6, "PA挤出3线", 2, 1.0), (7, "合金挤出1线", 3, 1.4), (8, "合金挤出2线", 3, 1.1),
                  (9, "阻燃挤出1线", 4, 0.75), (10, "阻燃挤出2线", 4, 0.6), (11, "特塑挤出1线", 5, 0.5), (12, "特塑挤出2线", 5, 0.45)]
    ws_products = {ws: [p[0] for p in PRODUCTS if ws in p[6]] for ws in range(1, 6)}
    leaders = [e["emp_id"] for e in emps if roles[e["emp_id"]] == "班组长"]
    for lid, lname, ws, cap in lines_plan:
        names = "/".join(PRODUCTS[p - 1][1].split(" ")[0] for p in ws_products[ws])
        line_rows.append({"line_id": lid, "line_name": lname, "workshop_id": ws, "product_type": names, "capacity_tph": cap, "status": "运行",
                          "_perf": LINE_PERF[lid]})
        types = [2, 3, 1, 4, 5 if ws in (2, 3, 5) else 6]
        for k, t in enumerate(types, start=1):
            eq_id = len(eq_rows) + 1
            fac = WORKSHOPS[ws - 1][2]
            est = FACTORIES[fac - 1][5]
            install = est + timedelta(days=rng.randrange(30, 900))
            eq_rows.append({"equipment_id": eq_id, "equipment_code": f"EQ{lid:02d}{k:02d}", "equipment_name": f"{EQUIPMENT_TYPES[t - 1][1]}-{lname}",
                            "type_id": t, "line_id": lid, "serial_no": f"SN{install.year}{rng.randint(10000, 99999)}",
                            "manufacturer": EQUIPMENT_TYPES[t - 1][3], "install_date": install.isoformat(),
                            "design_life_years": 15 if t == 1 else 10, "power_kw": {1: 315.0 * cap / 1.6, 2: 75.0, 3: 7.5, 4: 37.0, 5: 45.0, 6: 11.0}[t],
                            "status": "running"})
            for s, (sname, stype, unit, _lo, _hi, _al) in enumerate(SENSORS_BY_TYPE[t], start=1):
                lo, hi, alarm = sensor_range(t, sname, ws)
                sensor_rows.append({"sensor_id": len(sensor_rows) + 1, "sensor_code": f"SN{lid:02d}{k:02d}{s}", "sensor_name": f"{sname}-{lname}",
                                    "equipment_id": eq_id, "sensor_type": stype, "unit": unit,
                                    "accuracy": round((hi - lo) * 0.005, 4), "_lo": lo, "_hi": hi, "_alarm": alarm})
        for k, (wname, wtype) in enumerate([("配料混合工位", "生产工位"), ("挤出造粒工位", "生产工位"), ("检验包装工位", "包装工位")], start=1):
            wc_rows.append({"wc_id": len(wc_rows) + 1, "wc_code": f"WC{lid:02d}{k:02d}", "wc_name": f"{lname}-{wname}", "line_id": lid,
                            "wc_type": wtype, "capacity_hr_day": 22.0, "status": "active"})
        for k, (tname, life) in enumerate([("螺纹元件组", 8000), ("切粒刀组", 1500), ("过滤网组", 300)], start=1):
            tool_rows.append({"tool_id": len(tool_rows) + 1, "tool_code": f"TL{lid:02d}{k:02d}", "tool_name": f"{tname}-{lname}",
                              "tool_type": tname, "line_id": lid, "total_life": life, "used_life": 0, "status": "in_use"})
        for _k, shift in enumerate("甲乙丙丁"):                      # 四班三运转:每线四个班组
            team_rows.append({"team_id": len(team_rows) + 1, "team_name": f"{lname}{shift}班", "shift_type": "四班三运转",
                              "leader_id": leaders[len(team_rows)], "line_id": lid})
    m.put("dim_production_line", line_rows)
    m.put("dim_equipment_type", [{"type_id": i, "type_name": n, "category": c, "manufacturer": mf, "model": md} for i, n, c, mf, md in EQUIPMENT_TYPES])
    m.put("dim_equipment", eq_rows)
    m.put("dim_sensor", sensor_rows)
    m.put("dim_work_center", wc_rows)
    m.put("dim_tool", tool_rows)
    m.put("dim_team", team_rows)
    m.line_products = {lid: ws_products[ws] for lid, _n, ws, _c in lines_plan} 
    ops = []
    for pid, pname, _pl, _spec, _recipe, _g, wss in PRODUCTS:
        hygro = any(x in pname for x in ("PA", "PC", "PBT", "PPA", "PPS"))
        steps = [("配料称量", 1, 6.0, 5.0), ("高速混合", 1, 8.0, 10.0)] + ([("原料干燥", 1, 15.0, 20.0)] if hygro else []) + \
                [("挤出造粒", 2, 32.0, 30.0), ("冷却切粒", 2, 0.0, 10.0), ("检验包装", 3, 5.0, 5.0)]
        first_line = [lp[0] for lp in lines_plan if lp[2] == wss[0]][0]
        for seq, (op, wc_k, cyc, setup) in enumerate(steps, start=1):
            ops.append({"operation_id": len(ops) + 1, "routing_id": pid, "seq_no": seq * 10, "op_name": op,
                        "wc_id": (first_line - 1) * 3 + wc_k, "standard_cycle_min": cyc, "setup_time_min": setup,
                        "description": f"{PRODUCTS[pid - 1][1].split(' ')[0]}:{op}(每吨标准作业 {cyc} 分钟)"})
    m.put("dim_operation", ops)
    m.put("dim_downtime_reason", [{"reason_id": i, "reason_code": c, "reason_name": n, "category": cat} for i, c, n, cat in DOWNTIME_REASONS])
    m.put("dim_spare_part", [{"part_id": i, "part_code": c, "part_name": n, "equipment_type_id": t, "unit": u, "unit_price": p,
                              "min_stock": lo, "max_stock": hi, "current_stock": hi} for i, c, n, t, u, p, lo, hi in SPARE_PARTS])
    m.put("dim_hazard", [{"hazard_id": i, "hazard_code": c, "hazard_name": n, "hazard_category": cat, "location": loc, "risk_level": lv}
                         for i, c, n, cat, loc, lv in HAZARDS])
    m.put("dim_risk_level", [{"level_id": i, "level_code": c, "level_name": n, "description": d, "action_required": a} for i, c, n, d, a in RISK_LEVELS])
    m.put("dim_carrier", [{"carrier_id": i, "carrier_code": f"CAR{i:03d}", "carrier_name": n, "transport_mode": mode,
                           "contact_person": rng.choice(SURNAMES) + "调度", "contact_phone": f"0532-{rng.randint(1000000, 9999999)}", "status": "active"}
                          for i, n, mode in [(1, "示例物流A有限公司", "road"), (2, "示例物流B有限公司", "road"), (3, "示例海运C有限公司", "sea"),
                                             (4, "示例铁路物流D有限公司", "rail")]])
    wh = [(1, "WH001", "一厂原料仓", 1, "raw", 6000.0), (2, "WH002", "一厂成品仓", 1, "finished", 4000.0), (3, "WH003", "一厂备件仓", 1, "spare", 300.0),
          (4, "WH004", "二厂原料仓", 2, "raw", 3500.0), (5, "WH005", "二厂成品仓", 2, "finished", 2500.0), (6, "WH006", "合肥原料仓", 3, "raw", 1500.0),
          (7, "WH007", "合肥成品仓", 3, "finished", 1200.0)]
    m.put("dim_warehouse", [{"warehouse_id": i, "warehouse_code": c, "warehouse_name": n, "factory_id": f, "warehouse_type": t,
                             "location": FACTORIES[f - 1][1], "capacity_ton": cap} for i, c, n, f, t, cap in wh])
    locs = []
    for w in m.get("dim_warehouse"):
        zones = "AB" if w["warehouse_type"] == "spare" else "ABCD"
        for z in zones:
            for row in range(1, 4 if w["warehouse_type"] != "spare" else 3):
                locs.append({"location_id": len(locs) + 1, "location_code": f"L{w['warehouse_id']}{z}{row:02d}",
                             "location_name": f"{w['warehouse_name']}{z}区-{row}排", "warehouse_id": w["warehouse_id"], "zone": z,
                             "capacity_ton": round(w["capacity_ton"] / (len(zones) * 3), 1)})
    m.put("dim_storage_location", locs)
    # 成本中心:与部门一致;生产成本中心的事业部取车间所在工厂的事业部
    m.put("dim_cost_center", [
        {"cc_id": 1, "cc_code": "CC001", "cc_name": "PP改性车间", "dept_id": 7, "bu_id": 1, "cc_type": "production"},
        {"cc_id": 2, "cc_code": "CC002", "cc_name": "PA改性车间", "dept_id": 7, "bu_id": 1, "cc_type": "production"},
        {"cc_id": 3, "cc_code": "CC003", "cc_name": "ABS/PC合金车间", "dept_id": 7, "bu_id": 2, "cc_type": "production"},
        {"cc_id": 4, "cc_code": "CC004", "cc_name": "阻燃材料车间", "dept_id": 7, "bu_id": 2, "cc_type": "production"},
        {"cc_id": 5, "cc_code": "CC005", "cc_name": "特种工程塑料车间", "dept_id": 7, "bu_id": 3, "cc_type": "production"},
        {"cc_id": 6, "cc_code": "CC006", "cc_name": "质量部", "dept_id": 8, "bu_id": 2, "cc_type": "quality"},
        {"cc_id": 7, "cc_code": "CC007", "cc_name": "设备部", "dept_id": 9, "bu_id": 2, "cc_type": "equipment"},
        {"cc_id": 8, "cc_code": "CC008", "cc_name": "销售部-南方(华东/华南/西南)", "dept_id": 2, "bu_id": 1, "cc_type": "sales"},
        {"cc_id": 9, "cc_code": "CC009", "cc_name": "销售部-北方(华北/华中/西北)", "dept_id": 3, "bu_id": 1, "cc_type": "sales"},
        {"cc_id": 10, "cc_code": "CC010", "cc_name": "研发中心", "dept_id": 16, "bu_id": 3, "cc_type": "rd"}])
    m.put("dim_profit_center", [{"pc_id": i, "pc_code": f"PC{i:03d}", "pc_name": n.replace("事业部", "利润中心"), "bu_id": i} for i, n, _t in BUSINESS_UNITS])
    return m
