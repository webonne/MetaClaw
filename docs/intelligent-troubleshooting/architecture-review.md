# 架构走读复核 —— metaclaw_troubleshooting MVP

> 记忆文档（供后续 AI / 工程师直接接续）。以第一性原理对照 D1–D7 与信任工程基线，
> 逐文件走读并实跑测试后的结论。**结论：无架构性偏差，多处比蓝图更严谨。**
>
> - 复核范围：`metaclaw_troubleshooting/{models,orchestrator,workflow,module,ports,api,repository,fixtures}.py`、`migrations/*.sql`、`l0/` 清洗与质量闸门。
> - 验证：`tests/test_troubleshooting_mvp.py` + `tests/test_troubleshooting_persistence.py` → **38 passed + 7 subtests**。
> - 复核基线版本：合并提交 `003b677`（PR #1 from chedou/zhinengpaizhang-dev）。

---

## 一、逐条基线核对（全部对齐）

| 基线 | 关键证据（代码位置） | 判定 |
|---|---|---|
| **D1 双路脊柱** | `orchestrator.diagnose`：`completeness==SYMPTOM` / 无 error_code / 无 SOP → `_fallback`（`RouteMode.LLM_FALLBACK`）；命中 → `RouteMode.DETERMINISTIC` | ✓ |
| **确定性优先 · LLM 不生成恢复动作** | 确定性路径**全程零 LLM**；根因/结论来自 `sop.diagnosis_rules` 匹配信号（`_synthesize`）；判异常是纯代码规则引擎（`_criterion_matches`，5 种类型化规则）；动作全部来自 `sop.actions`（`_safe_actions`） | ✓✓ |
| **强制引用证据 + abstain** | `_evaluate_criteria` 仅采纳"非 MISSING 且规则命中"的证据为 signal；无匹配规则 / 必需证据失败 / 草案 SOP → 一律 `abstained=True` + `Confidence.LOW` + 转人工 | ✓✓ |
| **写操作闸门（D5 红线）** | `MANUAL_WRITE` 强制 `requires_approval + PENDING + BLOCKED`；`/execute` → `ProductionWriteDisabled` 恒 **409**；`write_execution_enabled` 恒 `False`；`approve_action` 仅记"系统未执行"；`close(RECOVERED)` 要求无 pending 写 + 已批准写具备"成功且恢复验证"的外部结果 | ✓✓ |
| **契约一致性** | `Diagnosis` 强 schema（`contract_version`）；`(system,error_code)` 复合键 + `SopKeyCollisionError` fail-closed；`ActionType` 三分级 + `MANUAL_UNKNOWN`；`completeness` 驱动路由；`TransferContextSnapshot` 带 case/run/trace/证据/根因/置信 | ✓ |
| **两层 SOP** | `SopRepository`=结构化 KB 查询 seam；`EvidenceCollector`=DQL seam（现 fixture，未来 MCP）；凭证不入模型（本 MVP 无 prompt） | ✓ |
| **MetaClaw 边界** | orchestrator 自跑循环 + 状态机 + 校验；MetaClaw reasoning/knowledge 仅设计空位、未接、诚实 stub | ✓ |
| **取证 fail-closed（R3 降级）** | `_collect_evidence` 在外部工具边界 `except Exception` → `MISSING`，不抛 500；必需失败 → 降级人工取证 | ✓ |
| **幂等键** | `repository._idempotency_key` = `(system, error_code, service, 5 分钟桶)`；演练 `rehearsal` 排除；无 error_code 不去重 | ✓ |
| **安全默认** | `__main__` 拒绝非 loopback 绑定（待 RBAC/SSO）；`capabilities` 诚实自曝 `trusted_identity=False / publisher_connected=False / write_execution_enabled=False` | ✓ |

## 二、超预期、值得表扬

1. **`anomaly_criteria` 做成类型化规则引擎**（`numeric_gte / missing_or_lte / ratio_of_sum_gt / multiple_gt / contains_and_in` 判别联合）——比蓝图的"判据表"更工程化、可测、零 LLM。
2. **全链路 fail-closed + 诚实能力上报**：故意不建空 Adapter 接口"假装已集成 MetaClaw"。
3. **事务性 Outbox**（租约 claim/ack/retry，migration v2）做知识发布，进程重启可恢复。
4. **主动纠正上一轮 L0 口径与 bug**：把"可自动化"从松散的 32%（含写操作）收紧为"只读自动化 21%（30/146）"；并抓出旧 `build_sop_kb.py` 的 103 处字符丢失（`limit/skip`、IP、手机号被当步骤号）+ 3 处路由键一码多义冲突，设为 blocker 拒绝落盘。

## 三、待推进缺口（GAP 清单 · 供后续接续）

> 均非"错"，而是相对完整设计的**已知缺口**，且多数已诚实声明、以 fail-safe 占位。

| ID | 缺口 | 严重度 | 现状 | 下一步 |
|---|---|---|---|---|
| **G1** | 未命中路的 ReAct 式 agent（自主探索 + DQL 白名单沙箱）未实现 | 🟡 中（最大功能缺口，但 fail-safe） | `_fallback` 仅 abstain + 等人工 | 后续阶段实现"套笼子"的受控 LLM 分诊 |
| **G2** | MetaClaw 未真接线（LLM 走代理、上下文预算、Skill Evolver 沉淀） | 🟡 中 | 设计空位，已声明 | 出现真实调用方时抽取 reasoning/knowledge Adapter，Publisher 消费现有 Outbox |
| **G3** | `actor` 是请求体标签、非可信身份 | 🟡 中 | loopback-only 兜底，capabilities 自曝 | 放开网络前接 RBAC/SSO |
| **G4** | `route_to_team` 依赖 `owner_team`，KB 多数为空 | 🟢 低 | 有 human_contact 动作但转派目标可能空 | 知识补全 owner 归属 |
| **G5** | `EvidenceCollector.status` 与规则引擎 `triggered_signals` 两套判断 | 🟢 低 | 权威应为规则引擎 | 确认工作台展示"是否异常"以规则引擎为准，避免口径打架 |
| **G6** | L0 数据 blocker 未清（3 路由键冲突 + 103 处字符丢失） | 🟢 低（数据侧） | 清洗器已 fail-closed 拒绝落盘 | owner 裁决一码多义 + 回源表恢复被截断字符 |
| **G7** | 证据源当前与 Guance/fixture 耦合（`EvidenceQuery.query` 是 DQL 串） | 🟡 中 | 已出 D8 设计（多平台 OAL），未重构 | 落 `EvidenceRequest`/`SourceAdapter`/`Router` + 归一词汇表；见 `observability-abstraction-design.md` |

## 四、总评

**方向完全正确，无架构性偏差。** 实施在「安全边界、确定性优先、人机闸门、契约一致性」四条生命线上全部忠实且严谨，并诚实标注每一处未接部分（G1/G2/G3，均在设计预期的后续阶段、当前以 fail-safe 占位）。可放心往下走。

真正的下一步不是改架构，而是：清 L0 的 G6 → 内网观测云验证 `903001` 的 `evidence_dql`/`anomaly_criteria` → 按阶段补 G1（未命中 ReAct）/ G2（MetaClaw 接线）/ G3（可信身份）。
