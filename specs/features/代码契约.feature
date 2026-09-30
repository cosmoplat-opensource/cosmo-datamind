# 限界上下文:取证(evidence)、裁决(adjudication) · 决策:DR-065
功能: 从上传的代码里读出系统实际怎么用数据
  两种场景最有力的证据都在代码里。连接契约只顶替命名证据,重叠率与父键唯一的门槛不变。

  @REQ-EVI-01
  场景: 键名不同但代码里就这么连,关系通过裁决
    假如 湖仓里过站明细的 zone_sn 全都能在区域表的 zone_id 中找到
    而且 上传的后端代码包里有:
      """
      SELECT t.wd_sn, t.wd_zone_sn   <!-- 线别ID -->
      FROM t_pass_record t
      LEFT JOIN t_line_zone t4 ON t4.ZONE_ID = t.wd_zone_sn
      """
    当 数据裁决「过站发生于区域」
    那么 这条关系为 "verified"
    而且 证据里记下代码契约 "t_line_zone.ZONE_ID = t_pass_record.wd_zone_sn"

  @REQ-EVI-01
  场景: 没有代码契约时,键名不同的关系停在候选
    假如 湖仓里过站明细的 zone_sn 全都能在区域表的 zone_id 中找到
    当 数据裁决「过站发生于区域」
    那么 这条关系为 "candidate"

  @REQ-EVI-02
  场景: 代码包里的内网地址与测试环境主机不进入证据
    假如 上传的后端代码包里有:
      """
      spring.datasource.url: jdbc:mysql://10.9.8.7:3306/mes
      // 测试环境 https://test.example.internal:8443/api
      """
    当 读取上传的资料
    那么 读到的文本不含 "10.9.8.7" 与 "test.example.internal"

  @REQ-EVI-03
  场景: 代码注释给出字段中文含义
    假如 上传的后端代码包里有:
      """
      SELECT A.wd_zone_sn,   <!-- 线别ID -->
             A.wd_lane      -- 上面那个 join 为什么要引入
      FROM t_pass_record A
      """
    当 读取上传的资料
    那么 字段 "wd_zone_sn" 的中文含义是 "线别ID"
    而且 开发者评注不作为字段含义
