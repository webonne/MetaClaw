# SOP KB 数据质量报告

> 检查对象：`sop_kb.json`。工具只自动执行可逆清洗；路由键冲突与旧解析器造成的数据丢失必须人工回源确认。

## 汇总

| 指标 | 数量 |
|---|---:|
| 源记录 | 196 |
| 拆码后候选记录 | 199 |
| 有码候选记录 | 149 |
| 唯一路由键 | 146 |
| 无错误码记录 | 50 |
| 多错误码单元格 | 3 |
| 冲突路由键（阻断） | 3 |
| 缺系统标识（阻断） | 0 |
| 已有恢复方案的路由键 | 90 |
| 纯联系人式路由键 | 3 |
| 带结构化日志的路由键 | 23 |
| P0 路由键 | 18 |
| P1 路由键 | 19 |
| P2 路由键 | 10 |
| 未标级别路由键 | 99 |
| 只读自动化候选路由键（未解阻断） | 30 |
| 其中 P0/P1 路由键（未解阻断） | 18 |
| 缺告警级别 | 102 |
| 缺故障场景 | 96 |
| 本轮新增脱敏替换 | 0 |
| 清洗前恢复步骤 | 683 |
| 合并后步骤（拆码前） | 483 |
| 候选输出步骤（拆码后） | 500 |

## 问题分类

| 问题 | 数量 | 是否阻断 |
|---|---:|---|
| `BROKEN_CALL` | 5 | 是 |
| `KEY_COLLISION` | 3 | 是 |
| `MISSING_LEVEL` | 102 | 否 |
| `MISSING_SCENARIO` | 96 | 否 |
| `MULTI_CODE_EXPANDED` | 3 | 否 |
| `TRUNCATED_CONTACT` | 2 | 是 |
| `TRUNCATED_IPV4` | 90 | 是 |
| `TRUNCATED_VERSION` | 6 | 是 |

## 阻断项

- `BROKEN_CALL`（5 处）：疑似被旧分隔规则截断的 limit/skip 调用，需回源表恢复。 影响 `UNCODED@csdp#24`、`UNCODED@csdp#64`
- `101014`：同一 (system,error_code) 对应多个业务上下文，禁止自动合并：客户IM / CTI排队系统 / CTI管家/一线调度 / Pulsar投递失败导致调度阻塞；客服侧 / 登录 / 一键授权登录 / -
- `101040`：同一 (system,error_code) 对应多个业务上下文，禁止自动合并：三方 / 企信宝 / ...... / 接口是否正常；客服侧 / 登录 / 工单分享码登录 / -
- `101034`：同一 (system,error_code) 对应多个业务上下文，禁止自动合并：三方 / 企信宝 / ...... / 接口是否正常；客服侧 / 设备认证 / 查询企业名称 / -
- `TRUNCATED_CONTACT`（2 处）：疑似被旧分隔规则截断的联系人手机号，需回源表恢复。 影响 `UNCODED@csdp#110`、`904001`
- `TRUNCATED_IPV4`（90 处）：疑似被旧分隔规则截断的 IPv4 地址，需回源表恢复。 影响 `901001`、`UNCODED@csdp#110`、`101034`、`101040`、`904001`、`UNCODED@csdp#128`、`UNCODED@csdp#129`、`UNCODED@csdp#130`、`UNCODED@csdp#131`、`UNCODED@csdp#132`、`UNCODED@csdp#140`、`UNCODED@csdp#155`、`UNCODED@csdp#156`、`UNCODED@csdp#157`、`UNCODED@csdp#158`、`UNCODED@csdp#159`
- `TRUNCATED_VERSION`（6 处）：疑似被旧分隔规则截断的组件版本或路径，需回源表恢复。 影响 `UNCODED@csdp#129`、`UNCODED@csdp#131`、`UNCODED@csdp#140`

## 人工复核队列

- `BROKEN_CALL`：`UNCODED@csdp#24`、`UNCODED@csdp#64`
- `MISSING_LEVEL`：`101013`、`101014`、`101011`、`101066`、`101017`、`902002`、`101020`、`101027`、`101029`、`101031`、`101064`、`101078`、`101040`、`101080`、`101089`、`101103`、`101104`、`101047`、`1004`、`101056` …
- `MISSING_SCENARIO`：`101013`、`101014`、`101011`、`101066`、`902002`、`101020`、`101027`、`101029`、`101031`、`101064`、`101078`、`101040`、`101080`、`101089`、`101103`、`101104`、`101047`、`901006`、`1004`、`101056` …
- `MULTI_CODE_EXPANDED`：`201001 / 101007`、`101010 / 101014`、`101034 / 101040`
- `TRUNCATED_CONTACT`：`UNCODED@csdp#110`、`904001`
- `TRUNCATED_IPV4`：`901001`、`UNCODED@csdp#110`、`101034`、`101040`、`904001`、`UNCODED@csdp#128`、`UNCODED@csdp#129`、`UNCODED@csdp#130`、`UNCODED@csdp#131`、`UNCODED@csdp#132`、`UNCODED@csdp#140`、`UNCODED@csdp#155`、`UNCODED@csdp#156`、`UNCODED@csdp#157`、`UNCODED@csdp#158`、`UNCODED@csdp#159`
- `TRUNCATED_VERSION`：`UNCODED@csdp#129`、`UNCODED@csdp#131`、`UNCODED@csdp#140`

## 处理原则

1. `KEY_COLLISION` 未由系统自动合并，因为它会破坏 D1 的确定性路由前提。
2. `TRUNCATED_IPV4` / `TRUNCATED_VERSION` / `TRUNCATED_CONTACT` / `BROKEN_CALL` 表示旧解析器已经丢字符，只能从未脱敏源表恢复。
3. token 脱敏可独立安全执行；结构化清洗输出在存在阻断项时默认拒绝落盘。
