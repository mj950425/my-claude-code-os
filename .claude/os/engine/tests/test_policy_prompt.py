"""Natural-language decision rules: source, audience split, order, and legacy oracle parity."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SCRIPTS))

from gt_derive import derive
from gt_review import cmd_pages, cmd_policy, load_policy_task
from gt_review_render import render_policy_only_html, render_policy_html
from gt_task import definition_policy, reader_policy, TaskError
from policy_prompt import PolicyPromptError, decision_rules, render_inference_rules, render_agent_policy, sync_declared_prompt
from policy_document import load_document
from html import escape


POLICY = PROJECT_ROOT / ".claude/os/attributes/clothing-category-gender/definitions.md"
PROFILE = PROJECT_ROOT / ".claude/os/attributes/clothing-category-gender/profile.json"
WEARER_CONTENT = (
    "대표 썸네일과 상세 이미지에서 대상 상품을 실제로 입은 착용자를 확인한다. "
    "확인된 착용자 성별을 모아 판정한다. 남성만 확인되면 남성, 여성만 확인되면 여성, "
    "남성과 여성이 모두 확인되면 공용으로 판단한다. 확인된 착용자가 없으면 이 규칙으로 결정하지 않고 다음 규칙을 따른다."
)
LABELS = {"targetGender": ["MALE", "FEMALE", "UNISEX", "UNDETERMINED"]}


class PromptRuleSchemaTest(unittest.TestCase):
    def test_prompt_is_sorted_by_priority_and_preserves_natural_language(self) -> None:
        rendered = render_inference_rules(POLICY, "targetGender")
        rules = decision_rules(POLICY, "targetGender")
        self.assertEqual([rule["id"] for rule in rules], ["G1", "G2", "G3", "P1", "P2", "P3", "P4", "P5"])
        self.assertEqual([r["우선순위"] for r in rules[:3]], ["공통"] * 3)
        self.assertTrue(all("검산" not in r for r in rules))
        self.assertIn(WEARER_CONTENT, rendered)
        self.assertLess(rendered.index("1. 상품을 직접 설명하는 성별 문구"), rendered.index("2. 이미지 속 착용자 성별 합집합"))
        self.assertIn("단일 성별 문구와 남녀 공용 문구가 함께 있으면 단일 성별 문구를 따른다", rendered)
        self.assertIn("한쪽 성별 구간만 확인되거나 둘 다 확인되지 않으면", rendered)
        self.assertIn("남성 전용 종류와 여성 전용 실측이 함께 있거나", rendered)

    def test_malformed_or_ambiguous_rules_fail_closed(self) -> None:
        with self.subTest("missing content"):
            path = POLICY.with_name("_missing-content-test.md")
            path.write_text("## targetGender\n\n### 판정 규칙\n- `P1` sample\n  - 우선순위: 1\n  - 출처: test\n", encoding="utf-8")
            try:
                with self.assertRaisesRegex(PolicyPromptError, "내용"):
                    decision_rules(path, "targetGender")
            finally:
                path.unlink()
        with self.subTest("duplicate priority"):
            path = POLICY.with_name("_duplicate-priority-test.md")
            path.write_text(
                "## targetGender\n\n### 판정 규칙\n"
                "- `P1` first\n  - 내용: first\n  - 우선순위: 1\n  - 출처: test\n"
                "- `P2` second\n  - 내용: second\n  - 우선순위: 1\n  - 출처: test\n", encoding="utf-8")
            try:
                with self.assertRaisesRegex(PolicyPromptError, "우선순위"):
                    decision_rules(path, "targetGender")
            finally:
                path.unlink()

    def test_multiline_natural_content_is_joined_without_losing_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "definition.md"
            path.write_text(
                "## field\n### 허용값\n- `YES` yes — yes\n\n### 판정 규칙\n"
                "- `P1` title\n  - 내용: 첫 문장\n    둘째 문장\n  - 우선순위: 1\n  - 출처: test\n",
                encoding="utf-8")
            self.assertEqual(decision_rules(path, "field")[0]["내용"], "첫 문장 둘째 문장")

    def test_v2_reader_receives_same_complete_policy_as_sdk(self) -> None:
        prompt = render_inference_rules(POLICY, "targetGender")
        blind, _ = reader_policy(POLICY, LABELS, {"targetGender": False}, {})
        self.assertIn(WEARER_CONTENT, prompt)
        shared, _ = render_agent_policy(POLICY, "targetGender")
        self.assertIn(shared, blind)
        self.assertIn(WEARER_CONTENT, blind)
        self.assertIn("P2", blind)
        self.assertIn("UNISEX", blind)

    def test_production_sync_preview_reports_drift_without_writing_external_files(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["_path"] = str(PROFILE)
        from gt_task import resolve
        delivery = profile["policyTask"]["promptDelivery"]
        paths = [resolve(profile, {"path": delivery[key], "root": delivery.get("root", "source")})
                 for key in ("resource", "adapter")]
        before = [path.read_bytes() for path in paths]
        plan = sync_declared_prompt(profile, POLICY)
        self.assertIsInstance(plan["changed"], bool)
        self.assertEqual(plan["version"], plan["sha256"][:12])
        self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_policy_only_task_syncs_without_gt_or_clothing_specific_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            definitions = root / "definitions.md"
            definitions.write_text(
                "## targetGender\n\n### 허용값\n- `MALE` 남성 — 남성\n- `FEMALE` 여성 — 여성\n\n"
                "### 판정 규칙\n- `P1` 썸네일 속 모델\n  - 내용: 이미지에 보이는 착용자가 남성이면 남성으로 판단한다.\n"
                "  - 우선순위: 1\n  - 출처: 테스트\n", encoding="utf-8")
            resource = root / "prompt.txt"
            resource.write_text("head\nBEGIN\nold\nEND\ntail\n", encoding="utf-8")
            adapter = root / "Adapter.java"
            adapter.write_text('public static final String HASH = "' + "0" * 64 + '";\n'
                               'public static final String PROMPT_VERSION = "v1";\n', encoding="utf-8")
            profile = {
                "id": "thumbnail-model-gender-policy-only",
                "policyTask": {
                    "schemaVersion": "policy-task-v1", "definitions": "definitions.md",
                    "fields": [{"id": "targetGender"}],
                    "promptDelivery": {
                        "resource": "prompt.txt", "adapter": "Adapter.java", "field": "targetGender",
                        "root": "project", "beginMarker": "BEGIN", "endMarker": "END",
                        "adapterTargets": [
                            {"name": "hash", "pattern": '(public static final String HASH\\s*=\\s*)"[0-9a-f]{64}";', "valueTemplate": "{sha256}"},
                            {"name": "version", "pattern": '(public static final String PROMPT_VERSION\\s*=\\s*)[\\s\\S]*?;', "valueTemplate": "policy-{hash12}"},
                        ],
                    },
                },
            }
            # A policy-only profile intentionally has no gtTask, keyField, or GT file.
            with patch("gt_task.resolve", side_effect=lambda _profile, spec: root / spec["path"]), \
                    patch("gt_review_render.resolve", side_effect=lambda _profile, spec: root / spec["path"]):
                preview = sync_declared_prompt(profile, definitions)
                self.assertTrue(preview["changed"])
                self.assertEqual(preview["version"], preview["sha256"][:12])
                stale_page = render_policy_only_html({**profile, "_path": str(root / "profile.json")})
                self.assertIn("이미지에 보이는 착용자가 남성이면", stale_page)
                applied = sync_declared_prompt(profile, definitions, apply=True)
            self.assertTrue(applied["applied"])
            self.assertIn("이미지에 보이는 착용자가 남성이면", resource.read_text(encoding="utf-8"))
            self.assertIn("policy-" + applied["sha256"][:12], adapter.read_text(encoding="utf-8"))
            with patch("gt_task.resolve", side_effect=lambda _profile, spec: root / spec["path"]), \
                    patch("gt_review_render.resolve", side_effect=lambda _profile, spec: root / spec["path"]):
                current_page = render_policy_only_html({**profile, "_path": str(root / "profile.json")})
                current_plan = sync_declared_prompt(profile, definitions)
            self.assertFalse(current_plan["changed"])
            self.assertIn("이미지에 보이는 착용자가 남성이면", current_page)

    def test_policy_only_profile_validates_and_renders_every_rule_without_gt(self) -> None:
        profile = json.loads((POLICY.parent.parent / "clothing-thumbnail-model-gender/profile.json").read_text(encoding="utf-8"))
        profile["_path"] = str(POLICY.parent.parent / "clothing-thumbnail-model-gender/profile.json")
        summary = load_policy_task(profile)
        self.assertTrue(summary["promptDelivery"])
        self.assertEqual(summary["fields"][0]["decisionRules"], 3)
        page = render_policy_only_html(profile)
        definitions = POLICY.parent.parent / "clothing-thumbnail-model-gender/definitions.md"
        document = load_document(definitions, "modelGender")
        for rule in document["rules"]:
            self.assertIn(escape(rule["title"]), page)
            self.assertIn(escape(rule["내용"]), page)
        prompt, _ = render_agent_policy(definitions, "modelGender")
        self.assertIn(escape(prompt), page)
        self.assertNotIn("운영 프롬프트 동기화됨", page)

    def test_gt_policy_page_displays_full_source_and_exact_sdk_policy(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["_path"] = str(PROFILE)
        page = render_policy_html(profile)
        self.assertIn(escape(POLICY.read_text()), page)
        prompt, _ = render_agent_policy(POLICY, "targetGender")
        self.assertIn(escape(prompt), page)
        self.assertNotIn("운영 프롬프트 동기화됨", page)
        self.assertNotIn("정책 SHA ccc68f6e", page)

    def test_product_and_thumbnail_tasks_share_the_policy_task_contract(self) -> None:
        product = json.loads(PROFILE.read_text(encoding="utf-8"))
        product["_path"] = str(PROFILE)
        thumbnail_path = POLICY.parent.parent / "clothing-thumbnail-model-gender/profile.json"
        thumbnail = json.loads(thumbnail_path.read_text(encoding="utf-8"))
        thumbnail["_path"] = str(thumbnail_path)
        product_summary = load_policy_task(product)
        thumbnail_summary = load_policy_task(thumbnail)
        self.assertEqual(product["policyTask"]["schemaVersion"], thumbnail["policyTask"]["schemaVersion"])
        self.assertEqual(product_summary["fields"][0]["field"], "targetGender")
        self.assertEqual(thumbnail_summary["fields"][0]["field"], "modelGender")
        for profile, summary, definitions in [(product, product_summary, POLICY),
                (thumbnail, thumbnail_summary, thumbnail_path.parent / "definitions.md")]:
            self.assertEqual(summary["promptInSync"], not sync_declared_prompt(profile, definitions)["changed"])
        self.assertIn("이미지 속 착용자 성별 합집합", render_policy_html(product))
        self.assertIn("이미지 속 착용자 성별 합집합", render_inference_rules(POLICY, "targetGender"))

    def test_gt_loader_and_normal_cli_paths_reject_policy_contract_drift(self) -> None:
        from gt_task import load_task

        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["_path"] = str(PROFILE)
        profile["policyTask"]["definitions"] = ".claude/os/attributes/clothing-thumbnail-model-gender/definitions.md"
        with self.assertRaisesRegex(TaskError, "같은 정책 문서"):
            load_task(profile)
        with patch("gt_review.find_policy_profile", return_value=profile):
            with self.assertRaisesRegex(TaskError, "같은 정책 문서"):
                cmd_policy(SimpleNamespace(task=profile["id"], action="check"))
            with self.assertRaisesRegex(TaskError, "같은 정책 문서"):
                cmd_pages(SimpleNamespace(task=profile["id"]))

    def test_gt_loader_rejects_policy_and_gt_field_order_drift(self) -> None:
        from gt_task import load_task

        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["_path"] = str(PROFILE)
        profile["policyTask"]["fields"][0]["id"] = "modelGender"
        with self.assertRaisesRegex(TaskError, "필드 순서·ID"):
            load_task(profile)

    def test_policy_only_profile_rejects_delivery_for_undeclared_field(self) -> None:
        profile = json.loads((POLICY.parent.parent / "clothing-thumbnail-model-gender/profile.json").read_text(encoding="utf-8"))
        profile["_path"] = str(POLICY.parent.parent / "clothing-thumbnail-model-gender/profile.json")
        profile["policyTask"]["promptDelivery"]["field"] = "missing"
        with self.assertRaisesRegex(TaskError, "policyTask.fields"):
            load_policy_task(profile)

    def test_policy_task_rejects_duplicate_prompt_delivery_outside_its_contract(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["_path"] = str(PROFILE)
        profile["promptDelivery"] = profile["policyTask"]["promptDelivery"]
        with self.assertRaisesRegex(PolicyPromptError, "policyTask 안에만"):
            sync_declared_prompt(profile, POLICY)


class LegacyClothingOutcomeParityTest(unittest.TestCase):
    def test_union_and_fallthrough_match_the_human_rule(self) -> None:
        legacy = Path(__file__).parent / "fixtures" / "legacy-clothing-definitions.md"
        table = definition_policy(legacy, LABELS, {"targetGender": False})["observed"]["targetGender"]

        def decide(**seen: bool) -> tuple[str, str]:
            value, rule = derive(table, seen)
            return value, rule or "그 밖"

        self.assertEqual(decide(WEARER_MAN=True, WEARER_WOMAN=False), ("MALE", "V8"))
        self.assertEqual(decide(WEARER_WOMAN=True, WEARER_MAN=False), ("FEMALE", "V7"))
        self.assertEqual(decide(WEARER_MAN=True, WEARER_WOMAN=True), ("UNISEX", "V5"))
        self.assertEqual(decide(WEARER_MAN=False, WEARER_WOMAN=False, SIZE_BOTH=True), ("UNISEX", "V6"))
        # Precedence is intentional: direct text wins; a single confirmed wearer beats the later size rule.
        self.assertEqual(decide(TEXT_WOMEN=True, WEARER_MAN=True, SIZE_BOTH=True), ("FEMALE", "V2"))
        self.assertEqual(decide(WEARER_WOMAN=True, WEARER_MAN=False, SIZE_BOTH=True), ("FEMALE", "V7"))
        self.assertEqual(decide(TEXT_WOMEN=True, TEXT_BOTH=True), ("FEMALE", "V2"))
        self.assertEqual(decide(KIND_MEN=True), ("MALE", "V11"))
        self.assertEqual(decide(KIND_MEN=True, SIZE_WOMEN_ONLY=True), ("UNDETERMINED", "그 밖"))
        self.assertEqual(decide(KIND_MEN=True, KIND_WOMEN=True), ("UNDETERMINED", "그 밖"))


if __name__ == "__main__":
    unittest.main()
