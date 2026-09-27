"""AI가 GT를 모른 채 같은 값을 낸 칸은 끝난 것으로 본다 — GT나 그 칸의 정의 절이 바뀔 때만 다시 나온다.

전에는 그런 칸을 새 후보 뒤로 미루기만 했다. 새 후보가 떨어지자 사람이 이미 보고 넘어간 칸(화면이 «누를 것이 없다»고 한 칸)이
통째로 다시 올라왔다 — 실제로 60건 가운데 40건이 그렇게 돌아왔다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".claude").is_dir())
sys.path.insert(0, str(PROJECT_ROOT / ".claude/os/engine/scripts"))
sys.path.insert(0, str(PROJECT_ROOT / ".claude/os/engine/tests"))

from test_gt_review import GT_ROWS, Fixture, write_jsonl  # noqa: E402


class AgreedCellsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.temp.name))
        self.fx.save()
        self.fx.task("prepare")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def worklist_cells(self) -> dict[tuple[str, str], dict]:
        worklist = json.loads((self.fx.review_dir() / "worklist.json").read_text(encoding="utf-8"))
        return {(item["key"], cell["field"]): cell for item in worklist["items"] for cell in item["cells"]}

    def agree_on(self, key: str, field: str, value, policy: str | None = "current") -> None:
        """판독이 그 칸에서 GT와 같은 값을 냈다고 적는다(finish가 쓰는 줄과 같은 모양)."""
        from catalog_profile import load_profile
        from gt_review import field_digests
        from gt_task import load_task

        profile = load_profile(self.fx.profile_path)
        row = {"key": key, "field": field, "gtValue": value, "batchId": "earlier"}
        if policy == "current":
            row["policy"] = field_digests(profile, load_task(profile))[field]
        elif policy is not None:
            row["policy"] = policy
        with (self.fx.review_dir() / "agreed.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def reference_only_cell(self) -> tuple[str, str, object]:
        for (key, field), cell in self.worklist_cells().items():
            if cell.get("signals") == ["GT_REFERENCE_ONLY"]:
                return key, field, cell["current"]
        self.fail("사람 확인 전 라벨 하나로만 뽑힌 칸이 픽스처에 없습니다")

    def test_an_agreed_cell_does_not_come_back(self) -> None:
        key, field, value = self.reference_only_cell()
        self.agree_on(key, field, value)
        self.fx.task("prepare", "--force")
        self.assertNotIn((key, field), self.worklist_cells(), "AI가 같은 값을 낸 칸이 다시 올라왔습니다")

    def test_an_old_agreement_without_a_digest_still_counts(self) -> None:
        key, field, value = self.reference_only_cell()
        self.agree_on(key, field, value, policy=None)  # 지문을 적기 전의 동의 — 버리면 끝낸 칸이 한꺼번에 돌아온다
        self.fx.task("prepare", "--force")
        self.assertNotIn((key, field), self.worklist_cells())

    def test_it_comes_back_when_its_definition_changes(self) -> None:
        key, field, value = self.reference_only_cell()
        self.agree_on(key, field, value)
        self.fx.bodies[field] = self.fx.bodies.get(field, "") + " 기준을 고쳤다."
        self.fx.save()
        self.fx.task("prepare", "--force")
        self.assertIn((key, field), self.worklist_cells(), "정의 절이 바뀌었는데 옛 동의로 끝난 칸으로 남았습니다")

    def test_it_comes_back_when_the_gt_changes(self) -> None:
        key, field, value = self.reference_only_cell()
        self.agree_on(key, field, value)
        rows = [dict(row) for row in GT_ROWS]
        other = next(label for label in ("RED", "BLUE", "GREEN", "PURPLE", "MATTE", "GLOSSY", "NONE", "LOW", "HIGH")
                     if label != value and field in ("color", "finish", "sheen")
                     and label in {"color": ["RED", "BLUE", "GREEN", "PURPLE"], "finish": ["MATTE", "GLOSSY"],
                                   "sheen": ["NONE", "LOW", "HIGH"]}[field])
        for row in rows:
            if row["sku"] == key:
                row[field] = other
        write_jsonl(self.fx.root / "data/gt.jsonl", rows)
        self.fx.task("prepare", "--force")
        self.assertIn((key, field), self.worklist_cells(), "GT가 바뀌었는데 옛 동의로 끝난 칸으로 남았습니다")


if __name__ == "__main__":
    unittest.main()
