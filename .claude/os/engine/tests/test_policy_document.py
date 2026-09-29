"""Canonical policy documents cannot silently discard or ambiguously bind rules."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from policy_document import load_document


def rule(identifier, priority="1", level=4):
    return f"{'#' * level} {identifier} · 판정\n- 내용: 확인된 근거로 판정한다.\n- 우선순위: {priority}\n- 판례: 없음\n"


class PolicyDocumentTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "definitions.md"
        fields = [dict(id=code, name=name, valueCodes={"확인": "YES"})
                  for code, name in [("first", "첫 속성"), ("second", "둘째 속성")]]
        (self.path.parent / "profile.json").write_text(json.dumps({"policyTask": {
            "documentFormat": "policy-document-v2", "definitions": str(self.path), "fields": fields
        }}), encoding="utf-8")
        self.content = "# 정책\n\n## 목적\n두 속성을 판정한다.\n"
        for name, identifier in [("첫 속성", "A1"), ("둘째 속성", "B1")]:
            self.content += f"\n## {name}\n### 허용값\n- 확인 — 근거가 확인됨\n### 규칙\n{rule(identifier)}"

    def load(self, content=None, field=None):
        self.path.write_text(content if content is not None else self.content, encoding="utf-8")
        return load_document(self.path, field)

    def test_independent_fields_may_use_same_numeric_priority(self):
        document = self.load()
        self.assertEqual([f["rules"][0]["우선순위"] for f in document["fields"]], ["1", "1"])

    def test_repeated_sections_are_rejected(self):
        for heading in ["목적", "첫 속성"]:
            with self.subTest(heading=heading), self.assertRaisesRegex(ValueError, "절 제목 중복"):
                self.load(self.content + f"\n## {heading}\n덮어쓰면 안 됨\n")

    def test_rule_ids_are_unique_across_fields(self):
        with self.assertRaisesRegex(ValueError, "통틀어 고유"):
            self.load(self.content.replace("B1 ·", "A1 ·"))

    def test_common_rule_ids_cannot_shadow_field_rules(self):
        with self.assertRaisesRegex(ValueError, "통틀어 고유"):
            self.load(self.content + "\n## 공통 규칙\n" + rule("A1", "공통", 3))

    def test_common_rules_require_common_priority(self):
        with self.assertRaisesRegex(ValueError, "우선순위는 공통"):
            self.load(self.content + "\n## 공통 규칙\n" + rule("C1", "1", 3))

    def test_empty_common_section_is_rejected(self):
        with self.assertRaises(ValueError):
            self.load(self.content + "\n## 공통 규칙\n")

    def test_projection_includes_common_rules_and_only_selected_field_rules(self):
        document = self.load(self.content + "\n## 공통 규칙\n" + rule("C1", "공통", 3), "second")
        self.assertEqual([r["id"] for r in document["rules"]], ["C1", "B1"])
        self.assertEqual(document["field"], "second")

    def test_all_registered_documents_bind_every_configured_field(self):
        attributes = Path(__file__).resolve().parents[2] / "attributes"
        profiles = sorted(attributes.glob("*/profile.json"))
        self.assertTrue(profiles)
        for profile in profiles:
            spec = json.loads(profile.read_text())["policyTask"]
            path = profile.parent / "definitions.md"
            with self.subTest(profile=profile.parent.name):
                document = load_document(path)
                self.assertIsNotNone(document)
                self.assertEqual([f["id"] for f in document["fields"]], [f["id"] for f in spec["fields"]])
                for field in spec["fields"]:
                    projection = load_document(path, field["id"])
                    self.assertEqual({v["code"] for v in projection["values"]}, set(field["valueCodes"].values()))


if __name__ == "__main__":
    unittest.main()
