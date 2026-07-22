# IT 智能排障系统 · 设计文档

基于 MetaClaw 元学习平台构建的 IT 智能排障能力，目标是把故障处理从
「人工翻系统 + 经验判断」升级为「告警驱动 · 智能路由 · 自动取证 · 人机协同诊断 · 知识闭环」。

## 文档

- [执行摘要（一页纸）](./executive-summary.html) — 给团队/领导的两分钟版：做什么、怎么安全、
  承诺什么、第一步与所需支持。
- [架构与演进蓝图 v0.3](./architecture-blueprint.html) — 完整设计（15 节）：分层架构、双路脊柱
  （确定性 / LLM 兜底）、全过程运转图、三契约、观测云 DQL 取证、L0→L5 演进、部门级平台化、
  编排层与信任工程（自建 orchestrator + MCP、workflow vs ReAct）、上线取信与放权阶梯、
  知识运营机制、决策记录（D1–D6）、风险与修正。

### 交互原型（单文件、零依赖，浏览器直接打开）

- [版式 A · 单故障详情页](./console-prototype.html) — IM 卡片 + 故障上下文 Web 台。
- [版式 B · 值班驾驶舱](./console-prototype-b.html) — 三栏应用式（左队列/中处置/右证据）。
- [故障工作台（列表→详情）](./console-workbench.html) — 版式 A 的列表入口版；含系统维度、
  手动录入（贴日志/现象→LLM 抽取）、按错误码的自主档徽标（影子/建议/自动）。

## L0 知识底座（已启动）

- [`l0/inventory_report.md`](./l0/inventory_report.md) — 家底盘点：146 错误码、60% 有恢复方案、
  ~32% 可自动化候选、26 个 P0/P1 首批激活对象。
- [`l0/sop_kb.json`](./l0/sop_kb.json) — 从《故障与措施》解析出的结构化 SOP 库（`status=candidate`，
  恢复步骤按 action_type 分类；Bearer/JWT 已脱敏）。
- [`l0/build_sop_kb.py`](./l0/build_sop_kb.py) — 解析脚本。

## 当前状态

架构已收敛（v0.3，D1–D6 已锁定）。L0 知识底座已启动。
下一步：审核候选条目 → 给 `903001` 补 `evidence_dql`/`anomaly_criteria` → 接观测云真实取证 →
建历史回归集接影子模式。仍待攻坚（知识与运营侧，随 L0 推进）：知识资产补全、审核 SLA。
