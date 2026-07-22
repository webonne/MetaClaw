# CLAUDE.md

本仓库是 **MetaClaw**（元学习代理平台）。本地会话当前的**活跃工作**是：在 MetaClaw 之上设计并落地一个
**IT 智能排障系统**（首个域 CSDP 工单/客服链路）。

## 接续这项工作，先读

1. **`docs/intelligent-troubleshooting/HANDOFF.md`** —— 完整会话记忆：6 个已锁定决策（D1–D6）、架构骨架、
   主要矛盾、已交付清单、下一步。**必读，一份就够上手。**
2. `docs/intelligent-troubleshooting/architecture-blueprint.html` —— 完整蓝图 v0.3（15 节）。
3. `docs/intelligent-troubleshooting/l0/` —— 已落地的 L0 结构化 SOP 库 + 家底盘点 + 903001 取证草案。

## 关键约束（细节见 HANDOFF）

- 确定性优先、写操作永远人工确认；LLM 只在需判断时被叫，且调用走 MetaClaw 代理。
- 编排：自建轻量 orchestrator + 工具走 MCP；SOP 分两层（结构化 KB / MetaClaw skill）。
- 系统天花板 = 知识质量（实测 ~32% 可自动化）；不承诺"上线即全自动"，走影子→放权阶梯逐格毕业。
- 观测云 DQL 端点为内网（`*.prd.sangfor.com`），需内网环境联调。
- 源表 xlsx 含真实 token/IP/人名，未入库；`sop_kb.json` 已脱敏。

## 方法论 skills

`.claude/skills/` 装了 qiushi-skill（矛盾分析 / 调查研究 / 批评与自我批评 等），可用 `/<name>` 调用。

## 纪律

开发在分支 `claude/session-moz2pc`；不擅自开 PR；改蓝图保持 §编号连续。
