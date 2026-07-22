import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

L0_DIR = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "intelligent-troubleshooting"
    / "l0"
)
sys.path.insert(0, str(L0_DIR))

import build_sop_kb  # noqa: E402
import clean_sop_kb as cleaner  # noqa: E402


def entry(error_code, service="csdp-wechat", steps=None, **overrides):
    value = {
        "system": "CSDP",
        "service": service,
        "module": "客户",
        "function": "创建工单",
        "scenario": "",
        "error_code": error_code,
        "level": "P1",
        "type": "依赖异常",
        "recovery_steps": steps or [],
        "log_signature": "",
        "evidence_dql": [],
        "anomaly_criteria": None,
        "owner_team": None,
        "origin": "seed",
        "status": "candidate",
        "completeness": {},
        "cause": "测试原因",
    }
    value.update(overrides)
    return value


class SplitErrorCodesTests(unittest.TestCase):
    def test_splits_only_explicit_line_breaks(self):
        self.assertEqual(
            cleaner.split_error_codes("101010\n101014"),
            ["101010", "101014"],
        )
        self.assertEqual(cleaner.split_error_codes("IM/2002"), ["IM/2002"])


class SourceParserRegressionTests(unittest.TestCase):
    def test_recovery_split_preserves_ips_and_method_arguments(self):
        import build_sop_kb as builder

        source = (
            "1. 检查主机 10.6.89.31/10.56.8.34\n"
            "2. 查询 db.orders.find({}).limit(10).skip(0)"
        )

        steps = builder.split_recovery_text(source)

        self.assertEqual(len(steps), 2)
        self.assertIn("10.6.89.31/10.56.8.34", steps[0])
        self.assertIn(".limit(10).skip(0)", steps[1])

    def test_builder_keeps_different_contexts_for_same_code(self):
        first = entry("DUP", service="service-one")
        second = entry("DUP", service="service-two", module="登录")

        merged = build_sop_kb.merge_sheets(
            {
                build_sop_kb.SourceContextKey(
                    "DUP", "service-one", "客户", "创建工单", ""
                ): first
            },
            {
                build_sop_kb.SourceContextKey(
                    "DUP", "service-two", "登录", "创建工单", ""
                ): second
            },
        )

        self.assertEqual(len(merged), 2)

    def test_builder_surfaces_conflicting_metadata_for_same_context(self):
        key = build_sop_kb.SourceContextKey(
            "DUP", "service-one", "客户", "创建工单", "创建"
        )
        first = entry("DUP", service="service-one", scenario="创建", level="P0")
        first["causes"] = ["原因一"]
        first.pop("cause")
        second = entry("DUP", service="service-one", scenario="创建", level="P1")
        second["causes"] = ["原因二"]
        second.pop("cause")

        merged = build_sop_kb.merge_sheets({key: first}, {key: second})
        finalized = [build_sop_kb.finalize(item) for item in merged.values()]
        result = cleaner.clean_entries(finalized)

        issues = [
            issue for issue in result.issues if issue.code == "SOURCE_METADATA_CONFLICT"
        ]
        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].blocking)
        self.assertNotIn("_source_conflict_fields", result.entries[0])

    def test_builder_surfaces_same_sheet_metadata_conflicts(self):
        class FakeReader:
            def load_sheet(self, _index):
                return {
                    3: {
                        "A": "service-one",
                        "B": "客户",
                        "C": "创建工单",
                        "D": "创建",
                        "E": "P0",
                        "K": "依赖异常",
                        "L": "原因一",
                        "M": "DUP",
                        "N": "检查日志",
                        "P": "signature-one",
                    },
                    4: {
                        "D": "创建",
                        "E": "P1",
                        "K": "数据异常",
                        "L": "原因二",
                        "M": "DUP",
                        "N": "联系 owner",
                        "P": "signature-two",
                    },
                }

        config = {
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

        built = build_sop_kb.build_sheet(FakeReader(), 1, config, 3)
        built_entry = next(iter(built.values()))

        self.assertEqual(
            set(built_entry["_source_conflict_fields"]),
            {"level", "type", "log_signature"},
        )

    def test_cross_sheet_merge_preserves_existing_conflict_markers(self):
        key = build_sop_kb.SourceContextKey(
            "DUP", "service-one", "客户", "创建工单", "创建"
        )
        first = entry("DUP", service="service-one", scenario="创建", level="P0")
        first["causes"] = ["原因一"]
        first.pop("cause")
        second = copy.deepcopy(first)
        second["_source_conflict_fields"] = ["level"]

        merged = build_sop_kb.merge_sheets({key: first}, {key: second})

        self.assertEqual(merged[key]["_source_conflict_fields"], ["level"])

    def test_builder_finalization_redacts_metadata(self):
        fake_token = "c" * 32
        source = entry("SAFE", scenario=f"token={fake_token}")
        source["causes"] = [f'{{"token":"{fake_token}"}}']
        source.pop("cause")

        cleaned = build_sop_kb.finalize(source)

        self.assertNotIn(fake_token, json.dumps(cleaned, ensure_ascii=False))


class RecoveryStepTests(unittest.TestCase):
    def test_merges_numbered_sections_and_keeps_highest_risk(self):
        steps = [
            {"text": "背景说明", "action_type": "manual_unknown"},
            {"text": "1. 检查数据", "action_type": "auto_readonly"},
            {"text": "确认记录存在", "action_type": "auto_readonly"},
            {"text": "缺失时手动补录", "action_type": "manual_write"},
            {"text": "2. 验证结果", "action_type": "auto_readonly"},
            {"text": "重新查询", "action_type": "auto_readonly"},
        ]

        cleaned = cleaner.merge_recovery_steps(steps)

        self.assertEqual(len(cleaned), 3)
        self.assertEqual(
            cleaned[1]["text"],
            "1. 检查数据\n确认记录存在\n缺失时手动补录",
        )
        self.assertEqual(cleaned[1]["action_type"], "manual_write")
        self.assertEqual(cleaned[2]["action_type"], "auto_readonly")

    def test_does_not_treat_ip_fragment_as_numbered_heading(self):
        steps = [
            {"text": "S3区服务器地址", "action_type": "manual_unknown"},
            {"text": "10.6.89.", "action_type": "manual_unknown"},
            {"text": "提交工单申请放通", "action_type": "manual_write"},
            {"text": "联系 owner", "action_type": "human_contact"},
        ]

        cleaned = cleaner.merge_recovery_steps(steps)

        self.assertEqual(cleaned, steps)

    def test_write_action_wins_over_contact_wording(self):
        text = "联系 owner 重新启动数据库实例"
        self.assertEqual(cleaner.classify_action(text), "manual_write")
        cleaned = cleaner.merge_recovery_steps(
            [{"text": text, "action_type": "human_contact"}]
        )
        self.assertEqual(cleaned[0]["action_type"], "manual_write")

    def test_existing_readonly_update_statement_is_raised_to_write(self):
        text = "修改服务工单更新语句中的状态status，sub_status"
        cleaned = cleaner.merge_recovery_steps(
            [{"text": text, "action_type": "auto_readonly"}]
        )

        self.assertEqual(cleaned[0]["action_type"], "manual_write")

    def test_manual_unknown_is_never_relaxed_to_readonly(self):
        text = "查看结果后执行未建模操作"
        cleaned = cleaner.merge_recovery_steps(
            [{"text": text, "action_type": "manual_unknown"}]
        )

        self.assertEqual(cleaned[0]["action_type"], "manual_unknown")

    def test_merges_shell_comment_with_command(self):
        steps = [
            {"text": "# 检查MongoDB状态", "action_type": "auto_readonly"},
            {"text": "systemctl status mongodb", "action_type": "auto_readonly"},
            {"text": "# 重启MongoDB", "action_type": "manual_write"},
            {"text": "systemctl restart mongodb", "action_type": "manual_write"},
        ]

        cleaned = cleaner.merge_recovery_steps(steps)

        self.assertEqual(len(cleaned), 2)
        self.assertEqual(
            cleaned[0]["text"],
            "# 检查MongoDB状态\nsystemctl status mongodb",
        )
        self.assertEqual(cleaned[1]["action_type"], "manual_write")

    def test_cleanup_is_idempotent_for_merged_shell_steps(self):
        steps = [
            {"text": "# 重启MongoDB", "action_type": "manual_write"},
            {"text": "systemctl restart mongodb", "action_type": "manual_write"},
            {"text": "联系 owner", "action_type": "human_contact"},
        ]

        first_pass = cleaner.merge_recovery_steps(steps)
        second_pass = cleaner.merge_recovery_steps(first_pass)

        self.assertEqual(second_pass, first_pass)
        self.assertEqual(len(second_pass), 2)

    def test_merges_multiline_curl_command(self):
        steps = [
            {"text": "curl --request POST \\", "action_type": "manual_write"},
            {"text": "--url 'https://example.test' \\", "action_type": "manual_unknown"},
            {"text": "--data '{", "action_type": "manual_unknown"},
            {"text": '"id": "123"', "action_type": "manual_unknown"},
            {"text": "}'", "action_type": "manual_unknown"},
            {"text": "联系 owner", "action_type": "human_contact"},
        ]

        cleaned = cleaner.merge_recovery_steps(steps)

        self.assertEqual(len(cleaned), 2)
        self.assertIn("--data", cleaned[0]["text"])
        self.assertEqual(cleaned[0]["action_type"], "manual_write")
        self.assertEqual(cleaned[1]["action_type"], "human_contact")

    def test_merges_curl_command_after_explanatory_prefix(self):
        steps = [
            {
                "text": "通过 Postman 调用 curl --location 'https://example.test' \\",
                "action_type": "manual_write",
            },
            {"text": "--header 'Content-Type: application/json' \\", "action_type": "manual_unknown"},
            {"text": "--data '{\"id\": 1}'", "action_type": "manual_unknown"},
            {"text": "验证结果", "action_type": "auto_readonly"},
        ]

        cleaned = cleaner.merge_recovery_steps(steps)

        self.assertEqual(len(cleaned), 2)
        self.assertIn("--header", cleaned[0]["text"])
        self.assertEqual(cleaned[0]["action_type"], "manual_write")

    def test_merges_multiline_database_mutation(self):
        steps = [
            {
                "text": "更新语句 db.orders.updateMany(",
                "action_type": "manual_write",
            },
            {"text": "{status: 1},", "action_type": "manual_unknown"},
            {"text": "{$set: {status: 2}}", "action_type": "manual_unknown"},
            {"text": ");", "action_type": "manual_unknown"},
            {"text": "重新查询确认", "action_type": "auto_readonly"},
        ]

        cleaned = cleaner.merge_recovery_steps(steps)

        self.assertEqual(len(cleaned), 2)
        self.assertIn("updateMany", cleaned[0]["text"])
        self.assertEqual(cleaned[0]["action_type"], "manual_write")


class RedactionTests(unittest.TestCase):
    def test_redacts_query_json_and_bearer_tokens(self):
        fake_token = "a" * 32
        text = (
            f'https://example.test/path?app=CSDP&token={fake_token} '
            f'{{"token":"{fake_token}","Authorization":"Bearer {fake_token}"}}'
        )

        cleaned, count = cleaner.redact_text(text)

        self.assertEqual(count, 3)
        self.assertNotIn(fake_token, cleaned)
        self.assertEqual(cleaned.count("<TOKEN>"), 2)
        self.assertIn("Bearer <BEARER_TOKEN>", cleaned)

    def test_keeps_existing_placeholders(self):
        text = "Authorization: Bearer <BEARER_TOKEN>; token=<TOKEN>"
        cleaned, count = cleaner.redact_text(text)
        self.assertEqual(cleaned, text)
        self.assertEqual(count, 0)

    def test_redacts_token_after_escaped_or_encoded_ampersand(self):
        fake_token = "b" * 32
        text = (
            f"app=CSDP\\u0026token={fake_token} "
            f"app%3DCSDP%2Fu0026token%3D{fake_token}"
        )

        cleaned, count = cleaner.redact_text(text)

        self.assertEqual(count, 2)
        self.assertNotIn(fake_token, cleaned)


class CleaningTests(unittest.TestCase):
    def test_expands_codes_but_reports_context_collision(self):
        source = [
            entry("A\nB", service="service-one"),
            entry("B", service="service-two", module="登录"),
        ]

        result = cleaner.clean_entries(copy.deepcopy(source))

        self.assertEqual([item["error_code"] for item in result.entries], ["A", "B", "B"])
        collisions = [issue for issue in result.issues if issue.code == "KEY_COLLISION"]
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0].error_code, "B")
        self.assertTrue(collisions[0].blocking)

    def test_recomputes_completeness_after_cleanup(self):
        source = [
            entry(
                "903001",
                steps=[{"text": "检查日志", "action_type": "auto_readonly"}],
                evidence_dql=[{"query": "L::logs:(message){error_code='903001'}"}],
                anomaly_criteria={"log_hit": "count >= 1"},
            )
        ]

        result = cleaner.clean_entries(source)
        completeness = result.entries[0]["completeness"]

        self.assertTrue(completeness["has_code"])
        self.assertTrue(completeness["has_recovery"])
        self.assertTrue(completeness["has_evidence_dql"])
        self.assertTrue(completeness["has_anomaly_criteria"])
        self.assertTrue(completeness["automatable_candidate"])

    def test_reports_irrecoverable_split_damage(self):
        source = [
            entry(
                "BROKEN",
                steps=[
                    {"text": "服务器地址：10.6.89.", "action_type": "manual_unknown"},
                    {"text": ".skip(", "action_type": "manual_unknown"},
                ],
            )
        ]

        result = cleaner.clean_entries(source)
        codes = {issue.code for issue in result.issues}

        self.assertIn("TRUNCATED_IPV4", codes)
        self.assertIn("BROKEN_CALL", codes)
        damage_codes = {"TRUNCATED_IPV4", "BROKEN_CALL"}
        self.assertTrue(
            all(issue.blocking for issue in result.issues if issue.code in damage_codes)
        )

    def test_reports_truncated_version_as_blocking(self):
        source = [
            entry(
                "BROKEN_VERSION",
                steps=[
                    {"text": "kafka-3.5.", "action_type": "manual_unknown"},
                    {"text": "rabbitmq_server-3.8.", "action_type": "manual_unknown"},
                ],
            )
        ]

        result = cleaner.clean_entries(source)
        issues = [issue for issue in result.issues if issue.code == "TRUNCATED_VERSION"]

        self.assertEqual(len(issues), 2)
        self.assertTrue(all(issue.blocking for issue in issues))

    def test_reports_truncated_contact_phone_as_blocking(self):
        source = [
            entry(
                "BROKEN_CONTACT",
                steps=[
                    {"text": "如果服务异常，联系 雷树朝(", "action_type": "human_contact"},
                    {"text": "处理", "action_type": "manual_unknown"},
                ],
            )
        ]

        result = cleaner.clean_entries(source)
        issues = [issue for issue in result.issues if issue.code == "TRUNCATED_CONTACT"]

        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].blocking)

    def test_does_not_treat_arbitrary_chinese_call_as_truncated_contact(self):
        source = [
            entry(
                "VALID_CALL",
                steps=[
                    {"text": "执行更新(", "action_type": "manual_write"},
                ],
            )
        ]

        result = cleaner.clean_entries(source)

        self.assertFalse(
            any(issue.code == "TRUNCATED_CONTACT" for issue in result.issues)
        )

    def test_reports_missing_level_and_scenario_for_owner_review(self):
        source = [entry("INCOMPLETE", level="", scenario="")]

        result = cleaner.clean_entries(source)
        codes = {issue.code for issue in result.issues}

        self.assertIn("MISSING_LEVEL", codes)
        self.assertIn("MISSING_SCENARIO", codes)
        self.assertEqual(result.stats["missing_level"], 1)
        self.assertEqual(result.stats["missing_scenario"], 1)

    def test_missing_system_is_a_blocking_routing_error(self):
        source = [entry("NO_SYSTEM", system="")]

        result = cleaner.clean_entries(source)
        issues = [issue for issue in result.issues if issue.code == "MISSING_SYSTEM"]

        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].blocking)
        self.assertEqual(result.stats["missing_system"], 1)

    def test_step_metrics_distinguish_normalization_from_code_expansion(self):
        source = [
            entry(
                "CODE_A\nCODE_B",
                steps=[{"text": "检查日志", "action_type": "auto_readonly"}],
            )
        ]

        result = cleaner.clean_entries(source)

        self.assertEqual(result.stats["recovery_steps_before"], 1)
        self.assertEqual(result.stats["recovery_steps_after_normalization"], 1)
        self.assertEqual(result.stats["output_recovery_steps"], 2)


class CliSafetyTests(unittest.TestCase):
    def test_refuses_structural_output_when_blocking_damage_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sop.json"
            output = root / "cleaned.json"
            source.write_text(
                json.dumps(
                    [
                        entry(
                            "BROKEN",
                            steps=[
                                {
                                    "text": "服务器地址：10.6.89.",
                                    "action_type": "manual_unknown",
                                }
                            ],
                        )
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                return_code = cleaner.main(
                    ["--input", str(source), "--output", str(output)]
                )

            self.assertEqual(return_code, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
