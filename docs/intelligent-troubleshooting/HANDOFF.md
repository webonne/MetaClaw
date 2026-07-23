# 交接 / 会话记忆 —— IT 智能排障系统

> 本文件是一次完整设计会话的记忆快照，供**本地 Claude 无缝接续**。读完这份 + `architecture-blueprint.html`
> 就能掌握全部决策与现状。原会话分支 `claude/session-moz2pc` 已合入 `zhinengpaizhang-dev`；后续以用户
> 当前明确选择的分支为准。

---

## 一、这是什么

在 **MetaClaw**（元学习代理平台，本仓库）之上做一个 **IT 智能排障系统**。首个落地域：**CSDP** 工单/客服链路。
目标：把故障处理从「人工翻系统 + 经验判断」→「告警/工单驱动 · 智能路由 · 自动取证 · 人机协同 · 知识闭环」。

**MetaClaw 能复用的**：代理层（`api_server.py` 单轮把 skill 注入 system prompt）、Skills、长期记忆、Skill Evolver、candidate→promotion。
**关键实现事实**：MetaClaw 的 skill 注入是**单轮拼进 system prompt**，工具调用在客户端执行——它**注入方法+学习，但不跑多步循环、不执行工具**。

---

## 二、八个已锁定决策（D1–D8）

| # | 决策 | 要点 |
|---|---|---|
| **D1** | 确定性/智能边界 = 「(system,error_code) 是否命中」 | 命中走确定性查表+DQL取证；未命中才上 LLM |
| **D2** | 知识库可演进 | candidate→approved→deprecated；复用 MetaClaw memory 机制 |
| **D3** | 中长期收敛「故障上下文 Web 台」 | 强制 API-first：核心只产出「诊断结论对象」，IM/Web 都是视图 |
| **D4** | 编排 = 自建 orchestrator（LLM 走 MetaClaw）+ 工具走 MCP | orchestrator 跑循环/调 MCP/输出校验；每次 LLM 调用经 MetaClaw；SOP 分两层 |
| **D5** | 上线取信 = 影子 + 历史回归集 + 放权阶梯 | S0 影子→S1 建议→S2 只读自动取证→S3 半自动；写操作永不自动；按错误码逐格毕业 |
| **D6** | 知识运营 = 沉淀嵌进流程 + 贡献者受益 + 专家只审核 | 三来源（存量挖掘/增量沉淀/主动补全 backlog）；覆盖率进 KPI |
| **D7** | MetaClaw 集成 = 产品一体、Module 与运行时分开 | 同仓同包、统一启动；排障先独立进程，通过 reasoning / knowledge Adapter 复用 MetaClaw；RBAC/可信身份完成前保持 loopback |
| **D8** | 证据源开放适配（Observability Abstraction Layer） | SOP 存平台无关「意图」(EvidenceRequest)，绑定放注册表(D-A)，按 system+signal 路由(D-B)，各适配器归一到 canonical `observed` 字段(D-C)；观测云是首个适配器，Zabbix/Prometheus/日志平台可插拔加入、零改 SOP。详见 `observability-abstraction-design.md` |

---

## 三、架构骨架（详见蓝图 §3–§12）

- **六层**：①接入 ②路由 ③编排 ④能力(工具) ⑤协同/交付 ⑥闭环。MetaClaw 只在 ③⑥ 出力。
- **双路脊柱**：命中→确定性 workflow（code-planned）；未命中→ReAct 式 agent（套笼子：DQL 白名单+低置信+人工确认）。
- **三契约**：`IncidentContext`（含 intake_source/completeness/raw_input）→ `SopEntry`（key=(system,error_code)，含 evidence_dql/anomaly_criteria/action_type）→ `Diagnosis`（唯一对外契约）。
- **两层 SOP**：结构化 KB（确定性数据，orchestrator 用 MCP 工具查，凭证不入模型）＋ MetaClaw skill（方法论，注入 prompt）。
- **取证 = 可插拔多平台（D8）**：SOP 存平台无关意图，各平台适配器归一到 canonical `observed` 字段供规则引擎判读。
  **观测云是首个适配器**——DQL 语法 `命名空间::数据源:(字段){过滤}[时间范围]`（L::日志 M::指标 T::链路 D::拨测），
  端点 `df-openapi.prd.sangfor.com/api/v1/df/query_data_v1`，`DF-API-KEY` 鉴权（内网，沙箱不可达）。后续可接 Zabbix/Prometheus/日志平台。
- **信任工程五约束**：①确定性优先(LLM 不生成恢复动作) ②强制引用证据 ③结构化输出+校验闸门 ④置信度校准+abstain ⑤上下文预算。
- **intake**：webhook + 手动录入（贴日志/现象→LLM 抽取 error_code）；completeness 驱动路由；幂等键 (system,error_code,service,时间桶) + 疑似重复提示。

---

## 四、主要矛盾（务必记住）

**系统天花板 = 知识质量，不是技术。** L0 清洗后按 D1 唯一路由键重算：146 个键，62% 有恢复方案，
仅 **30/146 · 约 21%** 含明确只读自动化步骤（旧 32% 口径误计了部分写操作/未知动作），且仍有质量阻断；
`evidence_dql`/`anomaly_criteria` 几乎全为空。所以**不承诺"上线即全自动"**，走"先证明、逐格放权、越用越全"。
补齐是**持久战**：高频优先、和放权阶梯咬合。

---

## 五、已交付（都在本分支）

```
docs/intelligent-troubleshooting/
├── README.md                     # 索引
├── HANDOFF.md                    # 本文件
├── executive-summary.html        # 给领导一页纸
├── architecture-blueprint.html   # 蓝图 v0.3（16 节，D1–D7 + 落地热力矩阵）
├── architecture-review.md        # 实施走读复核（无偏差 + G1–G7 缺口清单）
├── metaclaw-integration-design.md # D7：MetaClaw 产品集成与分阶段实施合同
├── observability-abstraction-design.md # D8：证据源开放适配（多平台 OAL）契约
├── console-prototype.html        # 原型 A：单故障详情
├── console-prototype-b.html      # 原型 B：值班驾驶舱
├── console-workbench.html        # 文档入口：跳转到包内正式工作台
└── l0/
    ├── sop_kb.json               # 146 错误码结构化 SOP 库（脱敏，status=candidate）
    ├── inventory_report.md       # 家底盘点
    ├── build_sop_kb.py           # 解析脚本（需源表 f.xlsx，未入库，见下）
    ├── clean_sop_kb.py           # 保守清洗、脱敏、质量闸门 CLI
    ├── quality_report.md         # 当前阻断项与人工复核队列
    └── activated/903001.md       # 首个取证草案（evidence_dql+anomaly_criteria，待联调核实）

metaclaw_troubleshooting/
├── static/console-workbench.html # wheel 内正式工作台：列表→详情 + 主流程处置
└── migrations/
    ├── 001_initial.sql           # Diagnosis 聚合、复合幂等与知识 Outbox
    └── 002_outbox_delivery.sql   # 租约 claim / ack / failure retry
```
- **原型均单文件零依赖**，浏览器直接打开。（本会话环境 Artifact 在线发布被拦，故走 git + 文件推送。）
- **方法论 skills** 已装在 `.claude/skills/`（qiushi-skill：矛盾分析/调查研究/批评与自我批评等，下个会话可 `/` 调用）。
- **L0 清洗/质量闸门已补齐**：旧解析器会误把 IP、`.limit(10)` / `.skip(0)` 中的数字当步骤号，
  新解析器已用回归测试锁住“不丢字符”；当前 KB 中的 4 处残余 token 形态已再次脱敏。
- **当前数据阻断**：`101014`、`101034`、`101040` 在拆分多码单元格后均对应多个业务上下文，
  与 D1 `(system,error_code)` 唯一路由前提冲突；另有 103 处疑似被旧解析器截断的 IP、组件版本、
  联系人手机号、`limit/skip` 调用。清洗器把两类问题都设为阻断并拒绝自动落盘，详见 `quality_report.md`。
- **D7 P1 + P2 已落地**：HTTP 已收口到 `TroubleshootingModule`；`DiagnosisRepository` 统一承担
  副本隔离、5 分钟桶幂等和命令原子更新。运行时默认使用 `SQLiteDiagnosisRepository`，schema migration v2
  持久化完整 Diagnosis 聚合与知识发布 Outbox；关闭事务与候选入队原子提交，消费合同支持租约 claim、ack、
  失败重试和唯一候选约束。工作台与 SQL 已进入 wheel package-data，`/readyz`、capabilities、503 错误 ID
  可追踪；重启恢复和 wheel 安装已实测。`KnowledgePublisher` 仍等 P3 真实 MetaClaw Adapter 再抽取。
  当前 `actor` 仍是请求体审计标签而非可信身份，官方启动命令在 P5 认证前硬拒绝非 loopback。

---

## 六、下一步（❗需内网/人力，沙箱做不了）

1. **争取内网联调环境** → 核实 `903001.md` 里 `«待核实»` 的观测云数据源/字段名与查询延迟。
2. **审核 sop_kb.json** → 先裁决质量报告中的 3 个路由键冲突，再由各系统 owner 补 level/scenario；
   recovery_steps 已可由清洗器保守合并（拆码前 683 → 483；拆码后候选共 500 个步骤），阻断项未解决前
   不覆盖 canonical KB。
3. **给 903001 补好 evidence_dql/anomaly_criteria** → 走通 L0→L1 竖线 → 建 20–30 条历史回归集 → 接影子模式。
4. **把覆盖率/可自动化率纳入考核** → 驱动知识补全。

## 本地 Claude 可直接继续的活（不需内网）

- 把 `903001.md` 的模式**复制到其他高频码**（901002 微信 / 2000001 渠道 / 801008 主数据…backlog 见 inventory_report）。
- 用 owner 结论处理 `quality_report.md` 的 3 个 `KEY_COLLISION`，再执行结构化清洗落库。
- 起草 P3 的 `MetaClawReasoningAdapter` / `MetaClawKnowledgeAdapter` 外部合同；只有真实调用方出现时才抽取
  `ReasoningGateway` / `KnowledgePublisher` Protocol，并让 Publisher 消费现有租约 Outbox。
- 起草 **观测云 MCP server** 与 **SOP 查询 MCP server** 的接口定义。

## ⚠️ 敏感数据说明

源表《故障与措施》xlsx **含真实 Bearer/JWT token、内网 IP、人名**，**未入库**（`f.xlsx` 在用户本地）。
`sop_kb.json` 已脱敏（Bearer/JWT→`<BEARER_TOKEN>`，查询/JSON token→`<TOKEN>`，IP/人名保留）。
**若把源表纳入版本管理，务必先脱敏 token。** 已进入 Git 历史的旧快照仍可能保留本次修复前的 token，
如确认属于有效凭证，应立即轮换；未经明确授权不要擅自改写 Git 历史。

---

## 六·五、架构走读复核结论（2026-07-23，独立复核会话）

对 `metaclaw_troubleshooting` MVP 做了第一性原理走读 + 实跑测试（**38 passed + 7 subtests**）。
**结论：无架构性偏差，多处比蓝图更严谨**（`anomaly_criteria` 类型化规则引擎、全链路 fail-closed、
诚实不造空接口假装集成）。四条生命线（安全边界 / 确定性优先 / 人机闸门 / 契约一致性）全部对齐。

**完整结论见 [`architecture-review.md`](./architecture-review.md)**。未推进缺口（后续接续用）：

- **G1** 未命中路 ReAct 式 agent（自主探索 + DQL 白名单沙箱）未实现，`_fallback` 仅 abstain 占位 🟡
- **G2** MetaClaw 未真接线（LLM 走代理 / 上下文预算 / Skill Evolver），设计空位已声明 🟡
- **G3** `actor` 是请求体标签、非可信身份，靠 loopback-only 兜底，放网前须接 RBAC/SSO 🟡
- **G4** `route_to_team` 依赖 `owner_team`（KB 多为空）🟢 ·
  **G5** collector.status 与规则引擎 signal 两套判断，展示应以规则引擎为准 🟢 ·
  **G6** L0 数据 blocker（3 路由键冲突 + 103 处字符丢失）待 owner 裁决 + 回源表恢复 🟢
- **G7** 证据源当前与 Guance/fixture 耦合；已出 **D8** 多平台 OAL 设计，待重构 `EvidenceQuery→EvidenceRequest` + `SourceAdapter`/`Router` + 归一词汇表 🟡

均为 fail-safe 缺口、在设计预期的后续阶段，不改架构即可继续。

---

## 七、工作纪律（沿用）

- 旧开发分支 `claude/session-moz2pc` 已合入 `zhinengpaizhang-dev`；不要依据旧快照擅自切回，
  以后以用户当前明确选择的分支为准。
- 新提交沿用仓库现有提交说明约定；不要冒用并未参与本轮工作的 Co-Authored-By 身份。
- 不擅自开 PR。
- 改蓝图注意 §编号连续（当前 01–16）；改完可用 git 提交，浏览器验证渲染。
