# L0 知识家底盘点报告

> 由 `build_sop_kb.py` 从《故障与措施》自动解析生成。所有条目 `status = candidate`，需人工审核后 `approved`。
> 敏感凭证（Bearer/JWT token）已脱敏为 `<BEARER_TOKEN>`；内网 IP / 服务名 / URL 保留（运营所需）。

## 概览

| 指标 | 数值 |
|---|---|
| 唯一错误码（有码条目） | **146** |
| 有恢复方案 | **89 · 60%** |
| 纯「联系某人」式 | 3（占有恢复方案的 3%） |
| **可自动化候选**（有码 + 有可执行/只读步骤 + 非纯联系人） | **47 · 32%** |
| 带结构化日志样本（可直接做 evidence 依据） | 21 · 14% |

### 覆盖率漏斗
```
146 错误码
  └─ 60% 有恢复方案 (89)
       └─ 32% 可自动化候选 (47)  ← L1/L2 首批目标
            └─ P0/P1 中的可自动化候选: 26  ← 建议最先激活
```

### 按告警级别
| 级别 | 数量 |
|---|---|
| P0 | 16 |
| P1 | 18 |
| P2 | 10 |
| 未标注 | 102 |

## 关键结论（对应主要矛盾）

1. **可自动化候选约 32%**，比早先粗估的 ~20% 略好，但仍意味着 **2/3 的错误码需要补全**（evidence_dql / anomaly_criteria / 恢复方案）——知识侧确是天花板。
2. **几乎所有条目缺两样**：`evidence_dql`（0% 已填）和 `anomaly_criteria`（0% 已填）——这两项是激活确定性主干的关键，是知识运营的头号 backlog。
3. **只有 14% 带结构化日志样本**——这些是最容易补 evidence_dql 的，优先从它们下手。

## 首批激活 backlog（26 个 P0/P1 可自动化候选）

给这批错误码补 `evidence_dql` + `anomaly_criteria`，即可进入影子模式验证、逐步毕业到自动档：

| 错误码 | 级别 | 服务 | 原因 |
|---|---|---|---|
| `101004` | P0 | 客服侧 | IM登录错误 |
| `101010
101014` | P0 | 客户IM | Pulsar Producer发送失败，任务反复重入队 |
| `101015` | P0 | 客服侧 | 通过用户ID获取微信用户错误 / 获取微信用户失败 /  |
| `101024` | P0 | 客服侧 | 保存用户和微信用户错误 / 客户注册逻辑错误 |
| `201001
101007` | P0 | csdp-wechat | 创建工单逻辑错误 |
| `201003` | P0 | 渠道侧 | CSP登录错误 |
| `201011` | P0 | 客户IM | MongoDB查询失败/Redis分布式锁不可用/FSM |
| `401007` | P0 | CTI侧 | CSP登录错误 |
| `901002` | P0 | 客服侧 | 微信获取手机号错误 / 微信限制错误 |
| `904001` | P0 | 三方 |  |
| `IM1010` | P0 | 客户IM | Kafka/MQ Producer SendMessag |
| `IM2002` | P0 | 客户IM | BatchInsertChat2Cache 写入 Red |
| `IM3002` | P0 | 客户IM | BatchInsertChat2DB 写入 MongoD |
| `IM5003` | P0 | 客户IM | 业务逻辑异常 |
| `101034
101040` | P1 | 三方 |  |
| `2000001` | P1 | csdp-wechat | partner_user 表查询失败或用户数据为空 |
| `301002` | P1 | 客服侧 | 创建工单错误 / 创建工单单号未空 / ICARE创建工 |
| `4001006` | P1 | csdp-wechat | 工单数据为空 |
| `501001` | P1 | CTI侧 | ICARE获取用户编码错误 |
| `801008` | P1 | csdp-wechat | 主数据中客户数据不存在 |
| `801009` | P1 | csdp-wechat | 主数据中道数据不存在 |
| `901004` | P1 | 客服侧 | 微信获取OpenID错误 |
| `Workorder_CustomerDetailFail_004` | P1 | csdp-wechat |  |
| `Workorder_CustomerListFail_003` | P1 | csdp-wechat | 数据库异常 |
| `Workorder_EmergencyCreateFail_005` | P1 | csdp-wechat |  |
| `Workorder_UpgradeServiceFail_006` | P1 | csdp-wechat | ICARE操作异常 |

## 下一步

1. 人工审核这批 candidate 条目（尤其把被换行切碎的 recovery_steps 合并、补 level/scenario）。
2. 选 1 个（建议 `903001` 数据库访问异常）补 `evidence_dql` + `anomaly_criteria`，走通 L0→L1 竖切。
3. 用 20–30 条历史故障建回归集，接影子模式。
