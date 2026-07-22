#!/usr/bin/env python3
"""Conservative cleanup and quality checks for the L0 SOP knowledge base.

The source workbook is not committed because it contains sensitive operational
data.  This tool therefore distinguishes between reversible normalization and
damage that cannot be repaired without returning to the workbook.  In
particular, routing-key collisions are reported as blocking issues instead of
being merged silently.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ACTION_TYPES = {
    "auto_readonly",
    "human_contact",
    "manual_unknown",
    "manual_write",
}
ACTION_RISK = {
    "auto_readonly": 1,
    "human_contact": 2,
    "manual_unknown": 3,
    "manual_write": 4,
}

NUMBERED_HEADING = re.compile(r"^\s*\d{1,2}(?:\.(?!\d)|[、)）])\s*\S")
SHELL_HEADING = re.compile(r"^\s*#\s*\S")
CURL_COMMAND = re.compile(r"(?<![A-Za-z0-9_])curl\b", re.IGNORECASE)
DB_MUTATION_START = re.compile(
    r"\b(?:updateMany|updateOne|insertMany|insertOne|deleteMany|deleteOne)\s*\(",
    re.IGNORECASE,
)
TRUNCATED_IPV4 = re.compile(r"(?:^|[^\d])(?:\d{1,3}\.){2,3}$")
TRUNCATED_VERSION = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*-\d+(?:\.\d+)+\.$")
TRUNCATED_CONTACT = re.compile(
    r"(?:联系\s*[\u4e00-\u9fff]{2,6}|(?:问题|异常)[，,:\uff1a]\s*[\u4e00-\u9fff]{2,4})\(\s*$"
)
BROKEN_CALL = re.compile(r"(?:\.limit|\.skip)\(\s*$", re.IGNORECASE)

JWT = re.compile(
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
)
BEARER = re.compile(
    r"(?i)(\bBearer\s+)(?!<BEARER_TOKEN>)([A-Za-z0-9._~+/=-]{12,})"
)
QUERY_TOKEN = re.compile(
    r"(?i)((?<![A-Za-z])token(?:=|%3d))(?!<TOKEN>)([A-Za-z0-9._~+/%%-]{12,})"
)
JSON_TOKEN = re.compile(
    r'''(?ix)
    (["']?token["']?\s*:\s*["'])
    (?!<TOKEN>)
    ([A-Za-z0-9._~+/=-]{12,})
    (["'])
    '''
)
SIMPLE_TOKEN = re.compile(
    r'''(?ix)
    (simpleToken["']?\s*[:=]\s*["']?)
    (?!<TOKEN>)
    ([0-9a-f]{16,})
    '''
)

READ_KEYWORDS = (
    "检查",
    "查看",
    "查询",
    "确认",
    "观察",
    "find",
    "status",
    "df ",
    "free ",
    "getindexes",
    "profile",
)
WRITE_KEYWORDS = (
    "重启",
    "回滚",
    "部署",
    "重新启动",
    "切换",
    "扩容",
    "重推",
    "重新触发",
    "触发",
    "同步",
    "放通",
    "补录",
    "修改",
    "更新",
    "调整",
    "增加",
    "写入",
    "插入",
    "删除",
    "drop",
    "delete",
    "update",
    "insert",
    "restart",
    "kill",
    "关闭",
)
CONTACT_KEYWORDS = ("联系", "电话", "紧急联系", "通知", "@")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    error_code: Optional[str] = None
    blocking: bool = False


@dataclass
class CleanResult:
    entries: List[Dict[str, Any]]
    issues: List[Issue]
    stats: Dict[str, int]


def split_error_codes(value: Any) -> List[str]:
    """Split only explicit line-separated codes.

    Slashes are deliberately not separators: the old parser used ``/`` as a
    numbered-list delimiter and corrupted IP addresses and method calls.
    """

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    return list(dict.fromkeys(parts))


def redact_text(value: str) -> Tuple[str, int]:
    """Mask token-shaped secrets while leaving existing placeholders intact."""

    text = value
    replacements = 0

    text, count = JWT.subn("<BEARER_TOKEN>", text)
    replacements += count
    text, count = BEARER.subn(r"\1<BEARER_TOKEN>", text)
    replacements += count
    text, count = QUERY_TOKEN.subn(r"\1<TOKEN>", text)
    replacements += count
    text, count = JSON_TOKEN.subn(r"\1<TOKEN>\3", text)
    replacements += count
    text, count = SIMPLE_TOKEN.subn(r"\1<TOKEN>", text)
    replacements += count
    return text, replacements


def redact_value(value: Any) -> Tuple[Any, int]:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        output = []
        count = 0
        for item in value:
            cleaned, item_count = redact_value(item)
            output.append(cleaned)
            count += item_count
        return output, count
    if isinstance(value, dict):
        output = {}
        count = 0
        for key, item in value.items():
            cleaned, item_count = redact_value(item)
            output[key] = cleaned
            count += item_count
        return output, count
    return value, 0


def classify_action(text: str) -> str:
    lowered = text.lower()
    if CURL_COMMAND.search(text):
        return "manual_write"
    if any(keyword in lowered for keyword in WRITE_KEYWORDS) or "systemctl restart" in lowered:
        return "manual_write"
    if any(keyword in lowered for keyword in CONTACT_KEYWORDS):
        return "human_contact"
    if any(keyword in lowered for keyword in READ_KEYWORDS):
        return "auto_readonly"
    return "manual_unknown"


def _normalize_step(step: Any) -> Optional[Dict[str, str]]:
    if isinstance(step, str):
        text = step
        action_type = ""
    elif isinstance(step, dict):
        text = str(step.get("text") or "")
        action_type = str(step.get("action_type") or "")
    else:
        return None

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None
    inferred_action = classify_action(text)
    if action_type not in ACTION_TYPES:
        action_type = inferred_action
    elif inferred_action == "manual_write":
        action_type = inferred_action
    elif inferred_action == "human_contact" and action_type == "auto_readonly":
        action_type = inferred_action
    # A lack of recognized keywords only infers manual_unknown; it must not
    # override an explicit classification.  Conversely, an existing
    # manual_unknown is never relaxed to readonly by keyword inference.
    return {"text": text, "action_type": action_type}


def _merge_group(group: Sequence[Dict[str, str]]) -> Dict[str, str]:
    text = "\n".join(item["text"] for item in group)
    action_type = max(
        (item["action_type"] for item in group),
        key=lambda value: ACTION_RISK[value],
    )
    return {"text": text, "action_type": action_type}


def _brace_delta(text: str) -> int:
    return text.count("{") + text.count("[") - text.count("}") - text.count("]")


def _delimiter_delta(text: str) -> int:
    return (
        text.count("{")
        + text.count("[")
        + text.count("(")
        - text.count("}")
        - text.count("]")
        - text.count(")")
    )


def _curl_accepts(text: str, previous: str, brace_depth: int) -> bool:
    stripped = text.lstrip()
    return (
        previous.rstrip().endswith("\\")
        or stripped.startswith("--")
        or brace_depth > 0
        or stripped.startswith(("{", "}", "[", "]"))
    )


def merge_recovery_steps(steps: Iterable[Any]) -> List[Dict[str, str]]:
    """Conservatively join headings with their continuation lines.

    Numbered sections remain separate executable units.  Shell comments are
    joined with the command immediately below them, and multi-line curl
    commands are kept intact.  Free-form adjacent prose is not guessed at.
    """

    normalized = [item for item in (_normalize_step(step) for step in steps) if item]
    output: List[Dict[str, str]] = []
    active: List[Dict[str, str]] = []
    mode: Optional[str] = None
    curl_brace_depth = 0
    structured_depth = 0

    def flush() -> None:
        nonlocal active, mode, curl_brace_depth, structured_depth
        if active:
            output.append(_merge_group(active))
        active = []
        mode = None
        curl_brace_depth = 0
        structured_depth = 0

    for item in normalized:
        text = item["text"]

        # A prior pass may already have formed a multi-line atomic step.  Do
        # not reinterpret its first line as a fresh heading or command.
        if "\n" in text:
            flush()
            output.append(item)
            continue

        if mode == "curl":
            if _curl_accepts(text, active[-1]["text"], curl_brace_depth):
                active.append(item)
                curl_brace_depth += _brace_delta(text)
                continue
            flush()

        if mode == "structured_command":
            active.append(item)
            structured_depth += _delimiter_delta(text)
            if structured_depth <= 0:
                flush()
            continue

        if NUMBERED_HEADING.match(text):
            flush()
            active = [item]
            mode = "numbered"
            continue

        if SHELL_HEADING.match(text):
            flush()
            active = [item]
            mode = "shell_heading"
            continue

        if CURL_COMMAND.search(text):
            flush()
            active = [item]
            mode = "curl"
            curl_brace_depth = _brace_delta(text)
            continue

        if DB_MUTATION_START.search(text):
            flush()
            active = [item]
            mode = "structured_command"
            structured_depth = _delimiter_delta(text)
            if structured_depth <= 0:
                flush()
            continue

        if mode == "numbered":
            active.append(item)
            continue

        if mode == "shell_heading":
            active.append(item)
            flush()
            continue

        output.append(item)

    flush()
    return output


def _normalize_scalar(value: Any) -> str:
    return " ".join(str(value or "").split())


def _has_real_code(error_code: str) -> bool:
    return bool(error_code) and not error_code.startswith("UNCODED@")


def recompute_completeness(entry: Dict[str, Any]) -> Dict[str, bool]:
    steps = entry.get("recovery_steps") or []
    action_types = [step.get("action_type") for step in steps if isinstance(step, dict)]
    has_recovery = bool(steps)
    contact_only = has_recovery and all(value == "human_contact" for value in action_types)
    has_auto_readonly = any(value == "auto_readonly" for value in action_types)
    has_manual_write = any(value == "manual_write" for value in action_types)
    return {
        "has_code": _has_real_code(str(entry.get("error_code") or "")),
        "has_recovery": has_recovery,
        "has_evidence_dql": bool(entry.get("evidence_dql")),
        "has_anomaly_criteria": bool(entry.get("anomaly_criteria")),
        "contact_only": contact_only,
        "requires_human_write": has_manual_write,
        "automatable_candidate": bool(
            _has_real_code(str(entry.get("error_code") or ""))
            and has_auto_readonly
            and not contact_only
        ),
    }


def _normalize_entry(entry: Dict[str, Any]) -> Tuple[Dict[str, Any], int, int, int]:
    cleaned, redactions = redact_value(copy.deepcopy(entry))
    before_steps = len(cleaned.get("recovery_steps") or [])

    for field in (
        "system",
        "service",
        "module",
        "function",
        "scenario",
        "level",
        "type",
        "owner_team",
        "origin",
        "status",
        "cause",
    ):
        if cleaned.get(field) is not None:
            cleaned[field] = _normalize_scalar(cleaned[field])

    cleaned["log_signature"] = str(cleaned.get("log_signature") or "").strip()
    cleaned["recovery_steps"] = merge_recovery_steps(cleaned.get("recovery_steps") or [])
    cleaned.setdefault("evidence_dql", [])
    cleaned.setdefault("anomaly_criteria", None)
    cleaned["completeness"] = recompute_completeness(cleaned)
    after_steps = len(cleaned["recovery_steps"])
    return cleaned, redactions, before_steps, after_steps


def _damage_issues(entry: Dict[str, Any]) -> List[Issue]:
    error_code = str(entry.get("error_code") or "")
    issues = []
    for step in entry.get("recovery_steps") or []:
        text = str(step.get("text") or "").strip()
        for line in text.splitlines():
            if TRUNCATED_IPV4.search(line.strip()):
                issues.append(
                    Issue(
                        code="TRUNCATED_IPV4",
                        error_code=error_code,
                        blocking=True,
                        message="疑似被旧分隔规则截断的 IPv4 地址，需回源表恢复。",
                    )
                )
            if TRUNCATED_VERSION.search(line.strip()):
                issues.append(
                    Issue(
                        code="TRUNCATED_VERSION",
                        error_code=error_code,
                        blocking=True,
                        message="疑似被旧分隔规则截断的组件版本或路径，需回源表恢复。",
                    )
                )
            if TRUNCATED_CONTACT.search(line.strip()):
                issues.append(
                    Issue(
                        code="TRUNCATED_CONTACT",
                        error_code=error_code,
                        blocking=True,
                        message="疑似被旧分隔规则截断的联系人手机号，需回源表恢复。",
                    )
                )
            if BROKEN_CALL.search(line.strip()):
                issues.append(
                    Issue(
                        code="BROKEN_CALL",
                        error_code=error_code,
                        blocking=True,
                        message="疑似被旧分隔规则截断的 limit/skip 调用，需回源表恢复。",
                    )
                )
    return issues


def _context(entry: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return tuple(
        str(entry.get(field) or "")
        for field in ("service", "module", "function", "scenario")
    )


def clean_entries(entries: Sequence[Dict[str, Any]]) -> CleanResult:
    output: List[Dict[str, Any]] = []
    issues: List[Issue] = []
    redactions = 0
    steps_before = 0
    normalized_steps = 0
    multi_code_entries = 0

    for source_entry in entries:
        cleaned, item_redactions, item_before, item_after = _normalize_entry(source_entry)
        redactions += item_redactions
        steps_before += item_before
        normalized_steps += item_after

        source_conflict_fields = sorted(
            {str(field) for field in cleaned.pop("_source_conflict_fields", []) if field}
        )

        codes = split_error_codes(cleaned.get("error_code"))
        if not codes:
            codes = [""]
        if len(codes) > 1:
            multi_code_entries += 1
            issues.append(
                Issue(
                    code="MULTI_CODE_EXPANDED",
                    error_code=" / ".join(codes),
                    message="一个单元格含多个错误码，已拆为独立候选；仍需检查是否语义相同。",
                )
            )

        for code in codes:
            expanded = copy.deepcopy(cleaned)
            expanded["error_code"] = code
            expanded["completeness"] = recompute_completeness(expanded)
            output.append(expanded)
            issues.extend(_damage_issues(expanded))
            if source_conflict_fields:
                issues.append(
                    Issue(
                        code="SOURCE_METADATA_CONFLICT",
                        error_code=code,
                        blocking=True,
                        message=(
                            "同一源上下文存在冲突字段，禁止静默取首值："
                            + "、".join(source_conflict_fields)
                        ),
                    )
                )
            if _has_real_code(code) and not expanded.get("system"):
                issues.append(
                    Issue(
                        code="MISSING_SYSTEM",
                        error_code=code,
                        blocking=True,
                        message="系统标识为空，无法构造 D1 (system,error_code) 路由键。",
                    )
                )
            if _has_real_code(code) and not expanded.get("level"):
                issues.append(
                    Issue(
                        code="MISSING_LEVEL",
                        error_code=code,
                        message="告警级别为空，需系统 owner 补齐。",
                    )
                )
            if _has_real_code(code) and not expanded.get("scenario"):
                issues.append(
                    Issue(
                        code="MISSING_SCENARIO",
                        error_code=code,
                        message="故障场景为空，需系统 owner 补齐。",
                    )
                )

    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for entry in output:
        key = (str(entry.get("system") or ""), str(entry.get("error_code") or ""))
        if key[0] and _has_real_code(key[1]):
            by_key[key].append(entry)

    collision_keys = 0
    for (_, error_code), candidates in by_key.items():
        if len(candidates) < 2:
            continue
        collision_keys += 1
        contexts = sorted({" / ".join(part or "-" for part in _context(item)) for item in candidates})
        issues.append(
            Issue(
                code="KEY_COLLISION",
                error_code=error_code,
                blocking=True,
                message=(
                    "同一 (system,error_code) 对应多个业务上下文，禁止自动合并："
                    + "；".join(contexts)
                ),
            )
        )

    automatable_candidate_entries = sum(
        1
        for entry in output
        if entry.get("completeness", {}).get("automatable_candidate")
    )
    automatable_candidate_keys = sum(
        1
        for candidates in by_key.values()
        if any(
            entry.get("completeness", {}).get("automatable_candidate")
            for entry in candidates
        )
    )
    p0_p1_automatable_keys = sum(
        1
        for candidates in by_key.values()
        if any(
            entry.get("completeness", {}).get("automatable_candidate")
            and entry.get("level") in {"P0", "P1"}
            for entry in candidates
        )
    )
    keys_with_recovery = sum(
        1
        for candidates in by_key.values()
        if any(entry.get("completeness", {}).get("has_recovery") for entry in candidates)
    )
    keys_with_log = sum(
        1 for candidates in by_key.values() if any(entry.get("log_signature") for entry in candidates)
    )
    contact_only_keys = sum(
        1
        for candidates in by_key.values()
        if any(entry.get("completeness", {}).get("has_recovery") for entry in candidates)
        and all(
            not entry.get("completeness", {}).get("has_recovery")
            or entry.get("completeness", {}).get("contact_only")
            for entry in candidates
        )
    )
    level_counts = Counter()
    level_priority = {"P0": 0, "P1": 1, "P2": 2}
    for candidates in by_key.values():
        levels = {str(entry.get("level") or "") for entry in candidates if entry.get("level")}
        if not levels:
            level_counts["unlabeled"] += 1
            continue
        selected = min(levels, key=lambda level: (level_priority.get(level, 99), level))
        level_counts[selected] += 1
    stats = {
        "source_entries": len(entries),
        "output_entries": len(output),
        "real_code_entries": sum(
            1 for entry in output if _has_real_code(str(entry.get("error_code") or ""))
        ),
        "unique_routing_keys": len(by_key),
        "uncoded_entries": sum(
            1 for entry in output if not _has_real_code(str(entry.get("error_code") or ""))
        ),
        "multi_code_entries": multi_code_entries,
        "collision_keys": collision_keys,
        "keys_with_recovery": keys_with_recovery,
        "keys_with_log": keys_with_log,
        "contact_only_keys": contact_only_keys,
        "p0_keys": level_counts["P0"],
        "p1_keys": level_counts["P1"],
        "p2_keys": level_counts["P2"],
        "unlabeled_level_keys": level_counts["unlabeled"],
        "automatable_candidate_entries": automatable_candidate_entries,
        "automatable_candidate_keys": automatable_candidate_keys,
        "p0_p1_automatable_keys": p0_p1_automatable_keys,
        "redactions": redactions,
        "recovery_steps_before": steps_before,
        "recovery_steps_after_normalization": normalized_steps,
        "output_recovery_steps": sum(
            len(entry.get("recovery_steps") or []) for entry in output
        ),
        "missing_level": sum(1 for entry in output if not entry.get("level")),
        "missing_scenario": sum(1 for entry in output if not entry.get("scenario")),
        "missing_system": sum(
            1
            for entry in output
            if _has_real_code(str(entry.get("error_code") or "")) and not entry.get("system")
        ),
        "blocking_issues": sum(1 for issue in issues if issue.blocking),
        "warnings": sum(1 for issue in issues if not issue.blocking),
    }
    return CleanResult(entries=output, issues=issues, stats=stats)


def redact_entries_only(entries: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    cleaned, count = redact_value(copy.deepcopy(list(entries)))
    return cleaned, count


def render_report(result: CleanResult, source: Path) -> str:
    issue_counts = Counter(issue.code for issue in result.issues)
    lines = [
        "# SOP KB 数据质量报告",
        "",
        f"> 检查对象：`{source.name}`。工具只自动执行可逆清洗；路由键冲突与旧解析器造成的数据丢失必须人工回源确认。",
        "",
        "## 汇总",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
    ]
    labels = [
        ("source_entries", "源记录"),
        ("output_entries", "拆码后候选记录"),
        ("real_code_entries", "有码候选记录"),
        ("unique_routing_keys", "唯一路由键"),
        ("uncoded_entries", "无错误码记录"),
        ("multi_code_entries", "多错误码单元格"),
        ("collision_keys", "冲突路由键（阻断）"),
        ("missing_system", "缺系统标识（阻断）"),
        ("keys_with_recovery", "已有恢复方案的路由键"),
        ("contact_only_keys", "纯联系人式路由键"),
        ("keys_with_log", "带结构化日志的路由键"),
        ("p0_keys", "P0 路由键"),
        ("p1_keys", "P1 路由键"),
        ("p2_keys", "P2 路由键"),
        ("unlabeled_level_keys", "未标级别路由键"),
        ("automatable_candidate_keys", "只读自动化候选路由键（未解阻断）"),
        ("p0_p1_automatable_keys", "其中 P0/P1 路由键（未解阻断）"),
        ("missing_level", "缺告警级别"),
        ("missing_scenario", "缺故障场景"),
        ("redactions", "本轮新增脱敏替换"),
        ("recovery_steps_before", "清洗前恢复步骤"),
        ("recovery_steps_after_normalization", "合并后步骤（拆码前）"),
        ("output_recovery_steps", "候选输出步骤（拆码后）"),
    ]
    for key, label in labels:
        lines.append(f"| {label} | {result.stats[key]} |")

    lines.extend(["", "## 问题分类", "", "| 问题 | 数量 | 是否阻断 |", "|---|---:|---|"])
    for code, count in sorted(issue_counts.items()):
        blocking = any(issue.blocking for issue in result.issues if issue.code == code)
        lines.append(f"| `{code}` | {count} | {'是' if blocking else '否'} |")

    blocking_issues = [issue for issue in result.issues if issue.blocking]
    lines.extend(["", "## 阻断项", ""])
    if blocking_issues:
        by_blocking_code: Dict[str, List[Issue]] = defaultdict(list)
        for issue in blocking_issues:
            by_blocking_code[issue.code].append(issue)
        for code, code_issues in sorted(by_blocking_code.items()):
            if code == "KEY_COLLISION":
                for issue in code_issues:
                    lines.append(f"- `{issue.error_code}`：{issue.message}")
                continue
            affected = list(dict.fromkeys(issue.error_code for issue in code_issues if issue.error_code))
            preview = "、".join(f"`{value}`" for value in affected[:20])
            suffix = " …" if len(affected) > 20 else ""
            lines.append(
                f"- `{code}`（{len(code_issues)} 处）：{code_issues[0].message} 影响 {preview}{suffix}"
            )
    else:
        lines.append("- 无。")

    grouped_examples: Dict[str, List[str]] = defaultdict(list)
    for issue in result.issues:
        if issue.code == "KEY_COLLISION" or not issue.error_code:
            continue
        if issue.error_code not in grouped_examples[issue.code]:
            grouped_examples[issue.code].append(issue.error_code)

    lines.extend(["", "## 人工复核队列", ""])
    if grouped_examples:
        for code, error_codes in sorted(grouped_examples.items()):
            preview = "、".join(f"`{value}`" for value in error_codes[:20])
            suffix = " …" if len(error_codes) > 20 else ""
            lines.append(f"- `{code}`：{preview}{suffix}")
    else:
        lines.append("- 无。")

    lines.extend(
        [
            "",
            "## 处理原则",
            "",
            "1. `KEY_COLLISION` 未由系统自动合并，因为它会破坏 D1 的确定性路由前提。",
            "2. `TRUNCATED_IPV4` / `TRUNCATED_VERSION` / `TRUNCATED_CONTACT` / "
            "`BROKEN_CALL` 表示旧解析器已经丢字符，只能从未脱敏源表恢复。",
            "3. token 脱敏可独立安全执行；结构化清洗输出在存在阻断项时默认拒绝落盘。",
            "",
        ]
    )
    return "\n".join(lines)


def write_json_atomic(path: Path, value: Any, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_report(path: Path, result: CleanResult, source: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(result, source), encoding="utf-8")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=base / "sop_kb.json")
    parser.add_argument("--output", type=Path, help="Write normalized JSON to this path")
    parser.add_argument("--report", type=Path, help="Write a Markdown quality report")
    parser.add_argument(
        "--redact-only",
        action="store_true",
        help="Only apply token redaction; requires --output and never changes structure",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when blocking issues are found",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    entries = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise SystemExit("SOP KB root must be a JSON array")

    if args.redact_only:
        if not args.output:
            raise SystemExit("--redact-only requires --output")
        redacted, count = redact_entries_only(entries)
        # The committed seed file currently uses one-space indentation.  Keep
        # it stable so a safety-only pass does not create a repository-wide
        # formatting diff.
        write_json_atomic(args.output, redacted, indent=1)
        print(json.dumps({"redactions": count, "output": str(args.output)}, ensure_ascii=False))
        return 0

    result = clean_entries(entries)
    if args.report:
        write_report(args.report, result, args.input)

    if args.output:
        if result.stats["blocking_issues"]:
            print(
                "Refusing normalized output: blocking data-quality issues exist. "
                "Use --report for the owner review queue."
            )
            return 2
        write_json_atomic(args.output, result.entries)

    print(json.dumps(result.stats, ensure_ascii=False, sort_keys=True))
    if args.strict and result.stats["blocking_issues"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
