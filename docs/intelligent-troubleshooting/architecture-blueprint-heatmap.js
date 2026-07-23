(() => {
  const columns = [
    { label: '蓝图 / 决策', sub: '边界是否定清' },
    { label: '结构化资产', sub: '模型 · KB · 契约' },
    { label: '本地后端', sub: '代码 · API · 状态机' },
    { label: '工作台', sub: '用户是否可操作' },
    { label: '真实系统', sub: '内网 · 外部平台' },
    { label: '生产验证', sub: '回归 · 影子 · 指标' },
  ];
  const S = (status, brief, detail) => ({ status, brief, detail });
  const rows = [
    { id: 'intake', name: '接入与故障上下文', scope: 'webhook · 手动录入 · completeness', cells: [
      S('done', '双入口已锁定', 'webhook + 手动录入、completeness 驱动路由已写入蓝图与 D1 边界。'),
      S('done', 'IncidentContext 已定义', 'IncidentContext、case_id、run_id、trace_id 与复合幂等字段已进入 1.3 合同。'),
      S('partial', 'API 可接，抽取未接', 'POST 诊断与手动录入可用；日志/现象尚未调用真实 LLM 抽取 error_code。'),
      S('partial', '手动录入可操作', '页面能录入、识别错误码并关联疑似重复；告警接入状态尚无独立视图。'),
      S('todo', '未接告警平台', '尚未连接真实告警 webhook，也未验证上游字段稳定性。'),
      S('todo', '未验真实 payload', '尚未用真实告警验证 error_code、trace_id 与 service 的完整度。'),
    ] },
    { id: 'routing', name: '幂等去重与确定性路由', scope: '(system,error_code) · 5 分钟桶', cells: [
      S('done', 'D1 已锁定', '命中复合键走确定性路径，未命中才进入受控 LLM 路径。'),
      S('done', '复合键合同完整', 'SopEntry.routing_key、IncidentContext 与服务端幂等键已统一。'),
      S('done', '路由与去重已实现', 'SOP 精确查表，SQLite 以 system/error_code/service/5 分钟桶复合键持久化去重。'),
      S('done', '路由结果可见', '工作台展示路由模式、SOP key，并对手动录入给出疑似重复关联。'),
      S('partial', '仅 903001 运行态', '真实 146 个候选路由键尚未接入运行时，当前主链只验证 903001。'),
      S('todo', '无真实命中率', '尚无真实告警路由准确率、误路由率或长尾覆盖数据。'),
    ] },
    { id: 'kb', name: 'L0 SOP 库与质量闸门', scope: '146 路由键 · fail-closed', cells: [
      S('done', '生命周期已定', 'candidate → approved → deprecated 与 D1 唯一键规则已锁定。'),
      S('done', '146 键已结构化', '清洗器、sop_kb.json、inventory_report 与 quality_report 已形成资产链。'),
      S('done', '阻断闸门已实现', 'KEY_COLLISION、字段损坏与动作风险分类会阻止覆盖 canonical KB。'),
      S('partial', '仅展示候选结果', '工作台能显示闭环产生的知识候选，但没有完整的 SOP 运营台。'),
      S('partial', '运行时未接 canonical KB', '真实 KB 仍有 106 个阻断项，尚未替换演练用 InMemorySopRepository。'),
      S('blocked', '需 owner 回源裁决', '3 个路由冲突和 103 个疑似旧解析器损坏项，需业务 owner 与源表处理。'),
    ] },
    { id: 'dql', name: 'DQL 模板与异常判据', scope: 'evidence_dql · anomaly_criteria', cells: [
      S('done', '确定性取证已定', '已命中错误码的取证模板与异常阈值归到确定性侧，LLM 不猜恢复动作。'),
      S('done', '903001 草案已具备', '903001 已有 4 项查询模板、判据和恢复动作分级。'),
      S('partial', 'fixture 判据可运行', '数值阈值、比例、组合信号和缺失降级均有代码与测试，但证据是 fixture。'),
      S('done', '证据与判据可视', '工作台展示 DQL、观测值、异常状态、根因和置信度。'),
      S('blocked', '观测云字段待核实', '数据源、字段名、索引、阈值与查询延迟必须在内网跑真实 DQL。'),
      S('blocked', '无法标定准确率', '真实 DQL 未通前，无法验证异常判定的精确率与有害错误。'),
    ] },
    { id: 'evidence', name: 'Evidence / MCP 工具层', scope: 'SOP 查询 · 观测云采集 · 降级', cells: [
      S('done', '工具边界已定', 'orchestrator 调 MCP，凭证不进模型，证据失败必须降级。'),
      S('partial', 'Protocol 已定义', 'SopRepository 与 EvidenceCollector 端口已存在，MCP 请求响应契约仍待固化。'),
      S('partial', 'fixture collector 可用', '四类证据、超时降级和来源标记已实现，尚无真实连接器。'),
      S('done', '证据看板已落地', '日志、指标、链路和影响证据可展开核对，缺失态也能呈现。'),
      S('blocked', '真实 MCP 未接', '观测云 MCP 与 SOP 查询 MCP 仍依赖内网、认证方式和字段核实。'),
      S('blocked', '无故障高峰验证', '尚未验证观测云慢/挂时的真实降级、超时与容量表现。'),
    ] },
    { id: 'orchestrator', name: '确定性 Orchestrator 主链', scope: 'route → collect → judge → diagnosis', cells: [
      S('done', 'code-planned 已锁定', '流程由代码驾驶，LLM 只在判断与表达处被调用。'),
      S('done', '输入输出合同完整', 'IncidentContext、SopEntry、EvidenceResult、Diagnosis 已串联。'),
      S('done', '903001 主链已跑通', '确定性路由、自动取证、判据、abstain、降级和状态机均有实现。'),
      S('done', '整条链可演练', '一键演练可从故障接入走到关闭与知识候选。'),
      S('partial', '适配器仍是 fixture', '编排核心可替换端口，但数据源和外部协同尚未真实接入。'),
      S('partial', '本地回归已过', '专项与非阻塞回归通过；尚未进入历史回放和真实影子流量。'),
    ] },
    { id: 'llm', name: 'MetaClaw 受控 LLM 兜底', scope: '抽取 · 判读 · 表达 · abstain', cells: [
      S('done', '五条信任约束已定', '确定性优先、证据引用、结构化校验、置信度与上下文预算已写入蓝图。'),
      S('partial', 'fallback 合同已有', 'RouteMode.LLM_FALLBACK、低置信与 abstain 字段已进入 Diagnosis。'),
      S('partial', '只做安全退让', '未知码会进入 needs_investigation 并隐藏恢复动作，尚未真实调用 MetaClaw。'),
      S('partial', '低置信语义可见', '页面能展示受控分诊和证据不足，但没有真实模型过程。'),
      S('todo', 'MetaClaw base_url 未接', '尚未接入代理、skill 注入、结构化输出校验和白名单工具。'),
      S('todo', '无模型评测集', '尚未测抽取准确率、证据引用完整率、错误置信度与成本。'),
    ] },
    { id: 'contract', name: '统一 Diagnosis / Case / Run 合同', scope: 'API-first · IM/Web 共用', cells: [
      S('done', 'D3 已锁定', '核心只产出诊断结论对象，IM 与 Web 都是同一合同的视图。'),
      S('done', '合同升级到 1.3', 'case/run、转派、外部结果、关闭和知识候选均已结构化。'),
      S('done', '读写 API 已实现', '创建、读取、确认、转派、审批、结果登记和关闭接口齐全。'),
      S('done', '页面由 API 驱动', '工作台适配真实 Diagnosis，不再只靠静态样例。'),
      S('partial', '本地持久化已完成', 'Case/Run/证据/审批/转派/关闭/审计已落 SQLite 且可重启恢复；尚未对接主 MetaClaw 或共享数据库。'),
      S('partial', '恢复合同已验证', 'schema v1→v2、事务回滚、并发隔离和重启恢复已有测试；缺共享部署、向后兼容和多进程压测。'),
    ] },
    { id: 'approval', name: '人工确认与动作风险控制', scope: '只读 · 联系 · 写操作', cells: [
      S('done', '写操作永不自动', 'D5 明确写操作必须人工确认，按错误码逐格放权。'),
      S('done', '风险字段已结构化', 'action_type、requires_approval、approval_status、pending_writes 已进入合同。'),
      S('done', '审批闸门已实现', '未确认不能审批，未审批不能登记结果，execute 固定返回 409。'),
      S('done', '审批原因强制留痕', '页面明确“系统未执行”，原因必填，动作按 action_id 精确绑定。'),
      S('todo', '无真实身份与权限', 'actor 目前是请求字段，尚未接 RBAC、SSO、值班身份和审批授权。'),
      S('partial', '安全边界已测', '跳步、错误恢复标记与多结果覆盖有回归；缺安全审计与权限渗透测试。'),
    ] },
    { id: 'transfer', name: '结构化转派与协同', scope: '上下文快照 · 责任团队', cells: [
      S('done', '协同语义已定', '转派必须携带 case/run、trace、证据、根因与置信度。'),
      S('done', 'TransferRecord 已定义', '目标团队、说明、actor、时间和上下文快照均结构化。'),
      S('done', '状态机与接口已实现', '确认后可转派、可重新转派，关闭后拒绝再操作。'),
      S('done', '转派页面可操作', '目标团队与说明必填，路由团队和时间线同步更新。'),
      S('todo', '未接工单 / IM', '当前只在本地记录，没有真正发送工单、群消息或值班通知。'),
      S('todo', '无协同 SLA 数据', '尚未验证接收率、跨团队等待时间、退回与升级链路。'),
    ] },
    { id: 'recovery', name: '外部结果与恢复验证', scope: '最新结果 · 硬门槛', cells: [
      S('done', '闭环门槛已定', '“已恢复”必须有真实外部结果与恢复验证，不能靠操作成功自证。'),
      S('done', 'Outcome / Closure 已定义', '结果状态、说明、恢复标记、关闭类型与反馈均结构化。'),
      S('done', 'latest-outcome 已实现', '按每个写动作的最新结果判断，失败会覆盖旧成功并阻止恢复关闭。'),
      S('done', '结果与关闭可操作', '可登记外部结果、选择关闭类型，未恢复时复选框禁用。'),
      S('todo', '恢复探针未接', '业务探测、指标回落与外部执行结果仍由人工填写，未自动采集。'),
      S('partial', '关键边界已浏览器验收', '本地完整闭环与成功后失败场景已验证；缺真实恢复探针准确性。'),
    ] },
    { id: 'closure', name: '关闭归档与知识候选', scope: 'AuditEvent · KnowledgeCandidate', cells: [
      S('done', '知识闭环已定', '关闭结果回流候选，不直接覆盖 approved SOP。'),
      S('done', '候选快照完整', '证据、动作、实际结果、根因、摘要和反馈均写入 KnowledgeCandidate。'),
      S('done', '关闭状态机已实现', '支持 recovered、false_positive、transferred_out、unresolved 与候选创建。'),
      S('done', '闭环结果一屏可见', '六阶段进度、恢复状态、知识候选 ID 和完整时间线可核对。'),
      S('partial', '归档已持久化', '关闭、候选、时间线和事务 Outbox 已落 SQLite 并通过重启恢复；知识审核服务仍未接。'),
      S('partial', '本地恢复链完整', 'API、浏览器、迁移和重启恢复已验证；缺审计不可抵赖、保留期与检索验证。'),
    ] },
    { id: 'knowledge-ops', name: '知识生命周期与 Owner 运营', scope: '审核 · 晋升 · SLA · KPI', cells: [
      S('done', 'D2 / D6 已锁定', '三来源、候选晋升、贡献者受益与专家审核机制已写入蓝图。'),
      S('done', '候选与质量报告已有', 'candidate 状态、清洗资产、风险报告与优先队列均存在。'),
      S('partial', '候选已可靠入队', '候选生成后进入持久化事务 Outbox，支持租约 claim/ack/失败重试；尚无 Publisher、approve/deprecate 服务。'),
      S('partial', '无完整运营台', '当前只显示候选 ID，没有 owner 队列、diff 审核和 SLA 视图。'),
      S('blocked', 'Owner 与裁决未落位', '冲突键、损坏字段、审核责任人与晋升 SLA 需要组织决策。'),
      S('blocked', '无运营效果数据', '覆盖率、审核时长、复用次数与贡献激励尚未运行。'),
    ] },
    { id: 'trust', name: '历史回归、影子与放权阶梯', scope: 'S0 → S3 · 按错误码毕业', cells: [
      S('done', 'D5 已锁定', '历史回放 + 真实影子 + 分级放权，有害错误零容忍。'),
      S('partial', '口径有，样本没有', '门槛和 ground truth 口径已定义，20–30 条历史样本尚未准备。'),
      S('todo', '无回放执行器', '尚无历史事件导入、批量运行、基线比较与回归闸门。'),
      S('partial', '自主档徽标已展示', '页面能表达影子/建议/只读自动，但不是由真实指标驱动。'),
      S('todo', '未接实时影子流量', '没有与真人并行运行、只记录不展示的生产旁路。'),
      S('todo', '无毕业指标', '准确率、有害错误、人工采纳率和 MTTR 改善尚未标定。'),
    ] },
    { id: 'platform', name: '生产平台化能力', scope: '持久化 · 权限 · 密钥 · 可观测', cells: [
      S('done', 'D7 集成形态已锁定', '产品入口一体、领域 Module 与运行时分开；统一启动、Adapter 复用和 loopback 准入已写入蓝图。'),
      S('partial', 'P2 工程资产已具备', 'SQLite schema/migration、事务 Outbox、包内静态页与能力合同已落地；RBAC、统一部署与真实 Adapter 仍缺。'),
      S('done', '持久运行时已落地', 'SQLite schema v2、就绪/能力接口、重启恢复与线程池安全路由均已实现并测试。'),
      S('partial', '包内工作台可用', '页面随 wheel 安装并显示持久化/Outbox 状态，但没有登录、权限、组织空间、审计检索和运维入口。'),
      S('todo', '未部署共享环境', '当前仅 127.0.0.1，本地分支尚未推送或进入测试环境。'),
      S('todo', '无生产 SLO / 演练', '尚未做容量、故障恢复、安全、密钥轮换、告警和发布回滚验证。'),
    ] },
  ];
  const statusMeta = {
    done: { label: '已完成' }, partial: { label: '部分落地' },
    todo: { label: '待实现' }, blocked: { label: '外部阻塞' },
  };
  const matrix = document.getElementById('hmMatrix');
  if (!matrix) return;
  const add = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const corner = add('div', 'hm-corner');
  corner.append(add('b', '', '能力域'));
  corner.append(add('small', '', '15 项 · 点击色块看证据'));
  matrix.append(corner);
  columns.forEach(column => {
    const head = add('div', 'hm-col');
    head.append(add('b', '', column.label));
    head.append(add('small', '', column.sub));
    matrix.append(head);
  });
  rows.forEach((row, rowIndex) => {
    const label = add('div', 'hm-row-label');
    label.dataset.hmRow = row.id;
    label.append(add('span', 'idx', String(rowIndex + 1).padStart(2, '0')));
    const copy = add('div');
    copy.append(add('b', '', row.name));
    copy.append(add('small', '', row.scope));
    label.append(copy);
    matrix.append(label);
    row.cells.forEach((cell, columnIndex) => {
      const button = add('button', 'hm-cell');
      button.type = 'button';
      button.dataset.status = cell.status;
      button.dataset.hmRow = row.id;
      button.setAttribute('aria-label', `${row.name}，${columns[columnIndex].label}：${statusMeta[cell.status].label}。${cell.detail}`);
      button.title = cell.detail;
      button.append(add('span', 'state', statusMeta[cell.status].label));
      button.append(add('span', 'brief', cell.brief));
      button.addEventListener('click', () => inspect(row, columns[columnIndex], cell));
      matrix.append(button);
    });
  });
  function inspect(row, column, cell) {
    const inspector = document.getElementById('hmInspector');
    inspector.textContent = '';
    const title = add('div');
    title.append(add('div', 'path', `${row.name} / ${column.label}`));
    title.append(add('h4', '', cell.brief));
    inspector.append(title);
    inspector.append(add('p', '', cell.detail));
    inspector.append(add('span', 'status', statusMeta[cell.status].label));
  }
  const allCells = rows.flatMap(row => row.cells);
  const count = status => allCells.filter(cell => cell.status === status).length;
  const decisionDone = rows.filter(row => row.cells[0].status === 'done').length;
  const localDone = rows.reduce((total, row) => total + row.cells.slice(2, 4).filter(cell => cell.status === 'done').length, 0);
  const productionDone = rows.reduce((total, row) => total + row.cells.slice(4, 6).filter(cell => cell.status === 'done').length, 0);
  document.getElementById('hmDecision').textContent = `${decisionDone} / ${rows.length}`;
  document.getElementById('hmLocal').textContent = `${localDone} / ${rows.length * 2}`;
  document.getElementById('hmProduction').textContent = `${productionDone} / ${rows.length * 2}`;
  const distribution = `${count('done')} 完成 · ${count('partial')} 部分 · ${count('todo')} 待做 · ${count('blocked')} 阻塞`;
  document.getElementById('hmDistribution').textContent = distribution;
  document.getElementById('hmDecision').title = `全矩阵：${distribution}`;
  document.querySelectorAll('[data-hm-filter]').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-hm-filter]').forEach(item => item.classList.toggle('active', item === button));
      const mode = button.dataset.hmFilter;
      rows.forEach(row => {
        const statuses = row.cells.map(cell => cell.status);
        const visible = mode === 'all' || (mode === 'gap' && statuses.some(status => status === 'todo' || status === 'blocked')) ||
          (mode === 'blocked' && statuses.includes('blocked'));
        document.querySelectorAll(`[data-hm-row="${row.id}"]`).forEach(item => item.classList.toggle('hm-row-hidden', !visible));
      });
    });
  });
})();
