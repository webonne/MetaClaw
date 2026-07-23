#!/usr/bin/env python3
"""Build the L0 SOP knowledge base from the private source workbook.

The workbook is intentionally not committed.  Pass its local path explicitly:

    python3 build_sop_kb.py /path/to/f.xlsx

Unlike the first one-off parser, this version never treats ``/`` or ``)`` as a
global step separator.  That old rule removed digits from IP addresses and
MongoDB ``limit(...)`` / ``skip(...)`` calls.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence
from xml.etree import ElementTree as ET

from clean_sop_kb import (
    classify_action,
    clean_entries,
    recompute_completeness,
    redact_text,
    redact_value,
    write_json_atomic,
    write_report,
)

XML_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_TAG = "{" + XML_NAMESPACE + "}"
NS = {"m": XML_NAMESPACE}
CELL_REFERENCE = re.compile(r"([A-Z]+)(\d+)")


class SourceContextKey(NamedTuple):
    """Workbook deduplication key; distinct from the D1 routing key."""

    error_code: str
    service: str
    module: str
    function: str
    scenario: str


class WorkbookReader:
    def __init__(self, path: Path):
        self.archive = zipfile.ZipFile(path)
        self.shared_strings = self._load_shared_strings()

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> "WorkbookReader":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _load_shared_strings(self) -> List[str]:
        try:
            raw = self.archive.read("xl/sharedStrings.xml")
        except KeyError:
            return []
        root = ET.fromstring(raw)
        return ["".join(node.text or "" for node in item.iter(NS_TAG + "t")) for item in root.findall("m:si", NS)]

    def _cell_value(self, cell: ET.Element) -> str:
        cell_type = cell.get("t")
        if cell_type == "inlineStr":
            return "".join(node.text or "" for node in cell.iter(NS_TAG + "t"))

        value = cell.find("m:v", NS)
        if value is None or value.text is None:
            return ""
        if cell_type == "s":
            return self.shared_strings[int(value.text)]
        return value.text

    def load_sheet(self, index: int) -> Dict[int, Dict[str, str]]:
        root = ET.fromstring(self.archive.read(f"xl/worksheets/sheet{index}.xml"))
        rows: Dict[int, Dict[str, str]] = {}
        for cell in root.iter(NS_TAG + "c"):
            reference = cell.get("r") or ""
            match = CELL_REFERENCE.fullmatch(reference)
            if not match:
                continue
            column, row_text = match.groups()
            rows.setdefault(int(row_text), {})[column] = self._cell_value(cell).strip()
        return rows


def mask(value: str) -> str:
    return redact_text(value or "")[0]


def split_recovery_text(value: str) -> List[str]:
    """Split only on actual line boundaries and preserve every character."""

    text = mask(value).replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in re.split(r"\n+", text) if line.strip()]


def forward_fill(
    rows: Mapping[int, Mapping[str, str]],
    columns: Sequence[str],
    start_row: int,
) -> Dict[int, Dict[str, str]]:
    last = {column: "" for column in columns}
    output: Dict[int, Dict[str, str]] = {}
    for row_number in sorted(rows):
        if row_number < start_row:
            continue
        cells = dict(rows[row_number])
        for column in columns:
            if cells.get(column):
                last[column] = cells[column]
            else:
                cells[column] = last[column]
        output[row_number] = cells
    return output


def _new_entry(cells: Mapping[str, str], config: Mapping[str, Any], code: str) -> Dict[str, Any]:
    type_column = config.get("type")
    return {
        "system": "CSDP",
        "service": cells.get("A", "") or config.get("service_default", ""),
        "module": cells.get("B", ""),
        "function": cells.get("C", ""),
        "scenario": cells.get(config["scenario"], ""),
        "error_code": code,
        "level": cells.get(config["level"], ""),
        "type": cells.get(type_column, "") if type_column else "",
        "causes": [],
        "recovery_steps": [],
        "log_signature": "",
        "evidence_dql": [],
        "anomaly_criteria": None,
        "owner_team": None,
        "origin": "seed",
        "status": "candidate",
    }


def _entry_key(
    cells: Mapping[str, str], config: Mapping[str, Any], code: str
) -> SourceContextKey:
    return SourceContextKey(
        error_code=code,
        service=cells.get("A", "") or config.get("service_default", ""),
        module=cells.get("B", ""),
        function=cells.get("C", ""),
        scenario=cells.get(config["scenario"], ""),
    )


def _merge_source_metadata(
    target: Dict[str, Any], incoming: Mapping[str, Any], fields: Sequence[str]
) -> None:
    """Fill blank metadata and preserve evidence of every conflicting value."""

    incoming_conflicts = incoming.get("_source_conflict_fields", [])
    if incoming_conflicts:
        conflicts = target.setdefault("_source_conflict_fields", [])
        for field in incoming_conflicts:
            if field not in conflicts:
                conflicts.append(field)

    for field in fields:
        incoming_value = incoming.get(field)
        if not target.get(field) and incoming_value:
            target[field] = incoming_value
        elif target.get(field) and incoming_value and target[field] != incoming_value:
            conflicts = target.setdefault("_source_conflict_fields", [])
            if field not in conflicts:
                conflicts.append(field)


def build_sheet(
    reader: WorkbookReader,
    sheet_index: int,
    config: Mapping[str, Any],
    start_row: int,
) -> Dict[SourceContextKey, Dict[str, Any]]:
    rows = reader.load_sheet(sheet_index)
    filled_rows = forward_fill(rows, config["forward_fill"], start_row)
    entries: Dict[SourceContextKey, Dict[str, Any]] = {}

    for row_number in sorted(filled_rows):
        cells = filled_rows[row_number]
        code = cells.get(config["code"], "").strip()
        cause = cells.get(config["cause"], "").strip()
        recovery = cells.get(config["recovery"], "").strip()
        if not any((code, cause, recovery)):
            continue
        if not code:
            code = f"UNCODED@{config.get('service_default', '?')}#{row_number}"

        key = _entry_key(cells, config, code)
        incoming = _new_entry(cells, config, code)
        entry = entries.get(key)
        if entry is None:
            entry = incoming
            entries[key] = entry
        else:
            _merge_source_metadata(entry, incoming, ("scenario", "level", "type"))
        if cause and cause not in entry["causes"]:
            entry["causes"].append(cause)

        existing_steps = {item["text"] for item in entry["recovery_steps"]}
        for text in split_recovery_text(recovery):
            if text not in existing_steps:
                entry["recovery_steps"].append(
                    {"text": text, "action_type": classify_action(text)}
                )
                existing_steps.add(text)

        log_column = config.get("log")
        log_value = cells.get(log_column, "") if log_column else ""
        if log_value:
            _merge_source_metadata(
                entry,
                {"log_signature": mask(log_value)[:600]},
                ("log_signature",),
            )

    return entries


def merge_sheets(
    *sheets: Mapping[SourceContextKey, Dict[str, Any]],
) -> Dict[SourceContextKey, Dict[str, Any]]:
    merged: Dict[SourceContextKey, Dict[str, Any]] = {}
    for sheet in sheets:
        for key, entry in sheet.items():
            if key not in merged:
                merged[key] = entry
                continue

            target = merged[key]
            for cause in entry["causes"]:
                if cause not in target["causes"]:
                    target["causes"].append(cause)

            existing_steps = {item["text"] for item in target["recovery_steps"]}
            for step in entry["recovery_steps"]:
                if step["text"] not in existing_steps:
                    target["recovery_steps"].append(step)
                    existing_steps.add(step["text"])

            _merge_source_metadata(
                target, entry, ("scenario", "level", "type", "log_signature")
            )
    return merged


def finalize(entry: Dict[str, Any]) -> Dict[str, Any]:
    entry, _ = redact_value(entry)
    entry["cause"] = " / ".join(entry.pop("causes"))
    entry["completeness"] = recompute_completeness(entry)
    return entry


def build_knowledge_base(source: Path) -> List[Dict[str, Any]]:
    sheet_one_config = {
        "forward_fill": ["A", "B", "C"],
        "code": "M",
        "cause": "L",
        "recovery": "N",
        "scenario": "D",
        "level": "E",
        "type": "K",
        "log": "P",
        "service_default": "csdp",
    }
    sheet_two_config = {
        "forward_fill": ["A", "B", "C"],
        "code": "M",
        "cause": "L",
        "recovery": "N",
        "scenario": "F",
        "level": "E",
        "service_default": "csdp",
    }

    with WorkbookReader(source) as reader:
        sheet_one = build_sheet(reader, 1, sheet_one_config, 3)
        sheet_two = build_sheet(reader, 2, sheet_two_config, 2)
    merged = merge_sheets(sheet_two, sheet_one)
    return [finalize(entry) for entry in merged.values()]


def inventory(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    real = [entry for entry in entries if entry["completeness"]["has_code"]]
    with_recovery = [entry for entry in real if entry["completeness"]["has_recovery"]]
    contact_only = [entry for entry in with_recovery if entry["completeness"]["contact_only"]]
    automatable = [entry for entry in real if entry["completeness"]["automatable_candidate"]]
    with_log = [entry for entry in real if entry["log_signature"]]

    by_level: Dict[str, int] = defaultdict(int)
    for entry in real:
        by_level[entry["level"] or "—"] += 1
    backlog = sorted(
        [entry for entry in automatable if entry["level"] in {"P0", "P1"}],
        key=lambda entry: (entry["level"], entry["error_code"]),
    )
    return {
        "total": len(real),
        "with_recovery": len(with_recovery),
        "contact_only": len(contact_only),
        "automatable_candidate": len(automatable),
        "with_log": len(with_log),
        "by_level": dict(by_level),
        "backlog_first": [entry["error_code"] for entry in backlog[:25]],
    }


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Local path to the private source xlsx")
    parser.add_argument("--output", type=Path, default=base / "sop_kb.json")
    parser.add_argument("--inventory-json", type=Path)
    parser.add_argument("--quality-report", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if not args.source.is_file():
        raise SystemExit(f"Source workbook not found: {args.source}")

    entries = build_knowledge_base(args.source)
    result = clean_entries(entries)
    if args.quality_report:
        write_report(args.quality_report, result, args.source)
    if result.stats["blocking_issues"]:
        print(
            "Refusing SOP KB output: source data still has blocking routing or corruption issues. "
            "Use --quality-report for the owner review queue."
        )
        print(json.dumps(result.stats, ensure_ascii=False, sort_keys=True))
        return 2

    stats = inventory(result.entries)
    write_json_atomic(args.output, result.entries, indent=1)
    if args.inventory_json:
        write_json_atomic(args.inventory_json, stats, indent=1)
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
