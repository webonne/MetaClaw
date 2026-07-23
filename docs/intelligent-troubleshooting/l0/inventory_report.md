# L0 知识家底盘点报告

> 源记录由 `build_sop_kb.py` 从《故障与措施》解析；下列指标由 `clean_sop_kb.py` 按 D1
> `(system,error_code)` 唯一路由键和保守动作分类重新计算。当前仍有 106 处阻断问题，候选数字用于排队，
> 不代表已可直接放权。所有条目 `status = candidate`，需人工审核后 `approved`。

## 概览

| 指标 | 数值 |
|---|---|
| 唯一 D1 路由键（含 3 个待裁决冲突键） | **146** |
| 有恢复方案 | **90 · 62%** |
| 纯「联系某人」式 | 3（占有恢复方案的 3%） |
| **只读自动化候选**（至少一个明确 `auto_readonly` 步骤） | **30 · 21%** |
| 带结构化日志样本（可直接做 evidence 依据） | 23 · 16% |

### 覆盖率漏斗
```
146 错误码
  └─ 62% 有恢复方案 (90)
       └─ 21% 只读自动化候选 (30)  ← L1/L2 候选池（尚有阻断项）
            └─ P0/P1 中的候选: 18  ← 先完成 owner 审核再激活
```

### 按告警级别
| 级别 | 数量 |
|---|---|
| P0 | 18 |
| P1 | 19 |
| P2 | 10 |
| 未标注 | 99 |

## 关键结论（对应主要矛盾）

1. 按“只有明确只读步骤才能算自动化候选”的安全口径，候选为 **30/146 · 21%**。旧口径的
   32% 把部分写操作/未知动作也计入，已停止使用；接近 **4/5 的路由键仍需补全或人工处置**。
2. **几乎所有条目缺两样**：`evidence_dql`（0% 已填）和 `anomaly_criteria`（0% 已填）——这两项是激活确定性主干的关键，是知识运营的头号 backlog。
3. **只有 16% 带结构化日志样本**——这些是最容易补 evidence_dql 的，优先从它们下手。

## 首批审核 backlog（18 个 P0/P1 只读自动化候选）

这 18 个路由键至少含一个明确只读步骤；仍需先处理质量阻断、补 `evidence_dql` +
`anomaly_criteria`，才能进入影子模式。`101014` 本身处于路由键冲突中，裁决前不可激活：

| 错误码 | 级别 | 服务 | 原因 |
|---|---|---|---|
| `101004` | P0 | 客服侧 | IM登录错误 |
| `101010` | P0 | 客户IM | Pulsar Producer发送失败，任务反复重入队 |
| `101014` | P0 | 客户IM | Pulsar Producer发送失败，任务反复重入队（路由键待裁决） |
| `101015` | P0 | 客服侧 | 获取微信用户 / Redis Lua 队列操作异常 |
| `201003` | P0 | 渠道侧 | CSP登录错误 |
| `201011` | P0 | 客户IM | MongoDB / Redis / FSM 状态写入失败 |
| `401007` | P0 | CTI侧 | CSP登录错误 |
| `901002` | P0 | 客服侧 | 微信获取手机号错误 / 微信限制错误 |
| `IM1010` | P0 | 客户IM | Kafka/MQ Producer 写入失败 |
| `IM2002` | P0 | 客户IM | BatchInsertChat2Cache 写入 Redis 失败 |
| `IM3002` | P0 | 客户IM | BatchInsertChat2DB / toMongo 写入失败 |
| `301002` | P1 | 客服侧 | 创建工单 / ICARE 创建工单异常 |
| `501001` | P1 | CTI侧 | ICARE获取用户编码错误 |
| `901004` | P1 | 客服侧 | 微信获取OpenID错误 |
| `Workorder_CustomerDetailFail_004` | P1 | csdp-wechat |  |
| `Workorder_CustomerListFail_003` | P1 | csdp-wechat | 数据库异常 |
| `Workorder_EmergencyCreateFail_005` | P1 | csdp-wechat |  |
| `Workorder_UpgradeServiceFail_006` | P1 | csdp-wechat | ICARE操作异常 |

## 下一步

1. 先处理 [`quality_report.md`](./quality_report.md) 的 3 个路由键冲突；旧解析器造成的 IP / 组件版本 /
   调用参数截断需回源表恢复，不能靠猜测补值。
2. 用 `clean_sop_kb.py` 合并步骤并重算 completeness，再由 owner 补 level/scenario。
3. 选 1 个（建议 `903001` 数据库访问异常）补 `evidence_dql` + `anomaly_criteria`，走通 L0→L1 竖切。
4. 用 20–30 条历史故障建回归集，接影子模式。
