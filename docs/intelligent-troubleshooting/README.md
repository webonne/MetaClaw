# IT 智能排障系统 · 设计文档

基于 MetaClaw 元学习平台构建的 IT 智能排障能力，目标是把故障处理从
「人工翻系统 + 经验判断」升级为「告警驱动 · 智能路由 · 自动取证 · 人机协同诊断 · 知识闭环」。

## 文档

- [执行摘要（一页纸）](./executive-summary.html) — 给团队/领导的两分钟版：做什么、怎么安全、
  承诺什么、第一步与所需支持。
- [架构与演进蓝图 v0.3](./architecture-blueprint.html) — 完整设计（16 节）：分层架构、双路脊柱
  （确定性 / LLM 兜底）、全过程运转图、三契约、观测云 DQL 取证、L0→L5 演进、部门级平台化、
  编排层与信任工程（自建 orchestrator + MCP、workflow vs ReAct）、上线取信与放权阶梯、
  知识运营机制、决策记录（D1–D7）、风险与修正，以及 15 个能力域 × 6 个落地阶段的热力矩阵。
- [架构走读复核](./architecture-review.md) — 对 `metaclaw_troubleshooting` MVP 的第一性原理复核：
  逐条基线对齐（无架构性偏差，38 测试通过）+ G1–G7 待推进缺口清单。
- [MetaClaw 集成设计](./metaclaw-integration-design.md) — D7 的实施合同：产品入口一体、领域 Module 与运行时
  隔离，通过 Adapter 复用 reasoning / knowledge，并明确统一启动、配置、鉴权、持久化与分阶段准入。
- [证据源开放适配设计](./observability-abstraction-design.md) — D8：多平台 Observability Abstraction Layer。
  SOP 存平台无关意图，绑定放注册表、按 system+signal 路由、各适配器归一到 canonical 字段；观测云是首个适配器，
  Zabbix/Prometheus/日志平台可零改 SOP 插入。

### 交互原型与 MVP 工作台

- [版式 A · 单故障详情页](./console-prototype.html) — IM 卡片 + 故障上下文 Web 台。
- [版式 B · 值班驾驶舱](./console-prototype-b.html) — 三栏应用式（左队列/中处置/右证据）。
- [故障工作台（列表→详情）](./console-workbench.html) — 正式页面位于 Python 包内的
  `metaclaw_troubleshooting/static/console-workbench.html`，文档链接仅负责跳转；通过 `/workbench` 访问时使用排障 API。
  支持隔离演练、确认结论、结构化转派、生产写操作人工批准、
  外部处置结果登记、恢复验证和关闭沉淀。MetaClaw 只记录批准与外部结果，不连接生产写执行器。

## 运行首条竖切 MVP

当前竖切固定使用 `903001` fixture，目的是先验证端到端合同与安全边界，不代表观测云 DQL 已在内网核实。
在仓库根目录运行：

```bash
uv run --no-project --with fastapi --with uvicorn python -m metaclaw_troubleshooting
```

默认使用可恢复的 SQLite 数据库 `~/.metaclaw/troubleshooting.db`；可用 `--database /path/to/file.db`
指定其他路径。服务在 RBAC/SSO 完成前只允许监听 loopback 地址。

然后访问：

- 工作台：`http://127.0.0.1:18080/workbench`
- API 文档：`http://127.0.0.1:18080/docs`
- 健康检查：`http://127.0.0.1:18080/healthz`
- 就绪检查：`http://127.0.0.1:18080/readyz`
- 运行能力：`http://127.0.0.1:18080/v1/troubleshooting/capabilities`

点击工作台顶部的「一键创建 903001 主流程演练」，可走完以下隔离闭环：

```text
故障接入 → 自动取证 → 诊断确认 → 结构化转派 → 人工批准
→ 外部处置结果登记 → 恢复验证 → 关闭归档 → 知识候选审核
```

演练使用独立的 `CSDP-REHEARSAL` 路由和唯一 case/run，不会把 canonical `CSDP:903001` 草案 SOP
改成已审核状态。核心接口包括：

- `POST /v1/troubleshooting/rehearsals/903001`：创建合成演练；
- `POST .../confirm`、`POST .../transfer`：人工确认与携带上下文的结构化转派；
- `POST .../actions/{action_id}/approve`：仅记录人工批准；
- `POST .../actions/{action_id}/record-outcome`：登记 MetaClaw 外部的人工处置结果和恢复验证；
- `POST .../close`：关闭归档，可生成 `candidate` 状态的知识候选；
- `POST .../execute`：固定返回 `409`，证明页面和 API 都不能误执行生产写操作。

状态机拒绝跳过诊断确认、动作审批、外部结果或恢复验证；转派快照携带 case/run、trace、根因、置信度和
证据 ID。知识候选预填证据、推荐动作、实际处置结果、根因与关闭摘要，只进入审核队列，不直接覆盖 SOP。

D7 P1 + P2 已完成：HTTP 入口只负责协议转换，诊断创建、查询与状态命令统一进入
`TroubleshootingModule`；`DiagnosisRepository` 负责副本隔离、复合幂等和并发命令原子更新，领域错误同时
返回稳定机器码与可读说明。SQLite schema migration v2 已持久化完整 Diagnosis 聚合与知识发布 Outbox；
候选和聚合同事务提交，消费端具备租约 claim、ack、失败重试与幂等约束。`KnowledgePublisher` 只会在 P3
出现真实 MetaClaw Adapter 时抽取，避免用空接口制造“已集成”的假象。当前 `actor` 尚未接可信身份，
启动命令会拒绝非 loopback 地址。

默认 `903001` SOP 保持 `draft/verified=false`：工作台只展示影子取证，隐藏正式根因与恢复动作；只有测试中显式构造
或隔离演练中构造 `approved/verified=true` 的合成 SOP，才会验证“人工批准但不执行”的合同。取证工具超时会降级为
人工取证，不返回 500。

运行竖切测试：

```bash
uv run --no-project --python 3.12 --with fastapi --with httpx --with pytest \
  python -m pytest -q tests/test_troubleshooting_mvp.py tests/test_troubleshooting_persistence.py
```

## L0 知识底座（已启动）

- [`l0/inventory_report.md`](./l0/inventory_report.md) — 清洗后家底盘点：146 个 D1 路由键、62% 有恢复方案、
  30/146（约 21%）只读自动化候选、18 个 P0/P1 首批审核对象。
- [`l0/sop_kb.json`](./l0/sop_kb.json) — 从《故障与措施》解析出的结构化 SOP 库（`status=candidate`，
  恢复步骤按 action_type 分类；Bearer/JWT 已脱敏）。
- [`l0/build_sop_kb.py`](./l0/build_sop_kb.py) — 解析脚本。
- [`l0/clean_sop_kb.py`](./l0/clean_sop_kb.py) — 保守清洗与质量闸门：合并被换行切碎的步骤、重算
  completeness、补充 token 脱敏，并在 `(system,error_code)` 冲突时拒绝静默落盘。
- [`l0/quality_report.md`](./l0/quality_report.md) — 当前数据的阻断项与人工复核队列。

在仓库根目录复查当前 KB：

```bash
python3 docs/intelligent-troubleshooting/l0/clean_sop_kb.py \
  --report docs/intelligent-troubleshooting/l0/quality_report.md
```

清洗器默认只报告；只要存在路由冲突或疑似丢字符，传 `--output` 也会拒绝写结构化结果。源表恢复后可用
`python3 docs/intelligent-troubleshooting/l0/build_sop_kb.py /path/to/f.xlsx --quality-report /tmp/sop-quality.md`
重新生成并通过同一质量闸门。

## 当前状态

架构已收敛（v0.3，D1–D7 已锁定）。L0 知识底座已启动；`903001` 的本地 fixture 竖切已打通：
确定性路由、4 项只读取证、数据驱动判据、统一 `Diagnosis` 合同、服务端复合幂等、工作台/API 联动，
以及从诊断确认、结构化转派、人工批准、外部处置登记、恢复验证到关闭归档和知识候选的完整演练闭环。

P2 已让这条闭环具备工程恢复能力：包内静态页与 SQL migrations 可随 wheel 安装，SQLite schema v2
保存 Case/Run、Evidence、Approval、Transfer、Outcome、Closure、AuditEvent 与 KnowledgeCandidate；进程重启后
状态可精确恢复，知识候选进入具备租约、确认与失败重试的事务 Outbox。`/readyz`、capabilities、503 错误 ID
和日志关联已可用于本地运维定位；专项验证为 38 项测试通过（另含 7 个 subtests）。

该竖切仍是开发态：观测云字段与阈值尚未联调核实，真实 Evidence/MCP 适配器与受控 LLM fallback 尚未接入，
生产写执行器明确保持断开。

清洗/质量闸门已落地，当前发现 3 个路由键对应多个业务上下文，且旧解析器已造成 103 处疑似
字符丢失（IP / 组件版本 / 联系人手机号 / `limit`、`skip` 调用），需 owner 裁决并回源表恢复；工具默认拒绝把
带阻断项的结构化结果覆盖 canonical KB。下一步：处理质量报告阻断项 → 审核候选条目 → 内网核实
`903001` 的 `evidence_dql`/`anomaly_criteria` → 接观测云真实取证 → 建历史回归集接影子模式。
