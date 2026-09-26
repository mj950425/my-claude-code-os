"""GT 원장 계약. 정답이 하나라는 것과, 진 라벨이 사라지지 않는다는 것을 지킨다."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BUILD_GT = Path(__file__).resolve().parents[1] / "scripts" / "build_gt.py"


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return path


class BuildGtTest(unittest.TestCase):
    def build(self, lineages, corrections=None, confirmations=None, extra_corrections=None):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        args = [sys.executable, str(BUILD_GT), "--profile-id", "t"]
        for spec in lineages:
            path = write_jsonl(tmp / f"{spec['id']}.jsonl", spec.pop("rows"))
            args += ["--lineage", json.dumps({**spec, "path": str(path)})]
        if corrections is not None:
            args += ["--corrections", str(write_jsonl(tmp / "fix.jsonl", corrections))]
        if extra_corrections is not None:
            args += ["--corrections", str(write_jsonl(tmp / "fix2.jsonl", extra_corrections))]
        if confirmations is not None:
            args += ["--confirmations", str(write_jsonl(tmp / "ok.jsonl", confirmations))]
        out, index = tmp / "gt.jsonl", tmp / "lineage.json"
        args += ["--out", str(out), "--lineage-index", str(index)]
        subprocess.run(args, check=True, capture_output=True)
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        return {row["productKey"]: row for row in rows}, json.loads(index.read_text(encoding="utf-8"))

    def test_lower_rank_number_wins_and_loser_is_kept(self) -> None:
        rows, index = self.build(
            [
                {"id": "hi", "rank": 1, "rows": [{"productKey": "P1", "goldLabel": "MALE", "goldSource": "사람"}]},
                {"id": "lo", "rank": 2, "rows": [{"productKey": "P1", "goldLabel": "UNISEX", "goldSource": "폴백"}]},
            ]
        )
        row = rows["P1"]
        self.assertEqual("MALE", row["goldLabel"])
        self.assertEqual("hi", row["goldLineage"])
        self.assertTrue(row["conflict"])
        self.assertEqual("LINEAGE_RANK", row["resolvedBy"])
        # 진 라벨은 사라지지 않는다 — 왜 이겼는지 되짚을 수 없으면 원장이 아니다
        self.assertEqual([{"lineage": "lo", "goldLabel": "UNISEX", "goldSource": "폴백"}], row["otherLineages"])
        self.assertEqual(1, index["counts"]["conflicts"])

    def test_same_label_is_not_a_conflict(self) -> None:
        rows, index = self.build(
            [
                {"id": "hi", "rank": 1, "rows": [{"productKey": "P1", "goldLabel": "MALE", "goldSource": "a"}]},
                {"id": "lo", "rank": 2, "rows": [{"productKey": "P1", "goldLabel": "MALE", "goldSource": "b"}]},
            ]
        )
        self.assertFalse(rows["P1"]["conflict"])
        self.assertEqual(0, index["counts"]["conflicts"])

    def test_correction_beats_every_lineage(self) -> None:
        rows, _ = self.build(
            [{"id": "hi", "rank": 1, "rows": [{"productKey": "P1", "goldLabel": "MALE", "goldSource": "a"}]}],
            corrections=[{"productKey": "P1", "goldLabel": "UNISEX", "goldSource": "사람 정정"}],
        )
        row = rows["P1"]
        self.assertEqual("UNISEX", row["goldLabel"])
        self.assertEqual("corrections", row["goldLineage"])
        self.assertEqual("CORRECTION", row["resolvedBy"])

    def test_unicode_line_separator_does_not_split_a_row(self) -> None:
        """상세 HTML에 섞여 오는 U+2028은 JSON에서 줄바꿈이 아니다."""
        rows, _ = self.build(
            [{"id": "hi", "rank": 1, "rows": [{"productKey": "P1", "goldLabel": "MALE", "goldSource": "a", "note": "앞 뒤"}]}]
        )
        self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()


class HumanDecisionReachesTheLedgerTest(BuildGtTest):
    """사람의 판정이 GT까지 가는가.

    지금까지 끊겨 있던 자리다. 사람이 「고쳐라」라고 판정해도 정정은 **외부 하네스 파일**에서만
    왔고, 「맞다」고 확인해도 GT에 흔적이 없어 다음 사이클이 같은 건을 다시 올렸다.
    """

    LINEAGE = [{"id": "hi", "rank": 1, "rows": [
        {"productKey": "P1", "goldLabel": "UNISEX", "goldSource": "시트"},
        {"productKey": "P2", "goldLabel": "MALE", "goldSource": "시트"},
    ]}]

    def test_a_human_correction_overrides_and_says_who(self) -> None:
        rows, _ = self.build(
            [dict(self.LINEAGE[0], rows=list(self.LINEAGE[0]["rows"]))],
            corrections=[{
                "productKey": "P1", "goldLabel": "FEMALE",
                "goldSource": "HUMAN_DECISION:BR-1", "decisionId": "BR-1",
                "reviewer": "mj", "reviewedAt": "2026-09-07T00:00:00+00:00",
                "reason": "리본 + 미니 사이즈가 겹친다",
            }],
        )
        self.assertEqual(rows["P1"]["goldLabel"], "FEMALE")
        self.assertEqual(rows["P1"]["resolvedBy"], "CORRECTION")
        # 라벨만 남으면 다음 사람이 그 값을 되짚을 수 없다.
        self.assertEqual(rows["P1"]["correctedBy"]["decisionId"], "BR-1")
        self.assertEqual(rows["P1"]["correctedBy"]["reviewer"], "mj")
        self.assertIn("리본", rows["P1"]["correctedBy"]["reason"])

    def test_a_confirmation_keeps_the_label_and_marks_it(self) -> None:
        """유지는 변경이 아니다. 라벨은 그대로 두고 «사람이 봤다»만 붙는다."""
        rows, index = self.build(
            [dict(self.LINEAGE[0], rows=list(self.LINEAGE[0]["rows"]))],
            confirmations=[{
                "productKey": "P2", "goldLabel": "MALE", "decisionId": "BR-2",
                "reviewer": "mj", "reviewedAt": "2026-09-07T00:00:00+00:00",
                "reason": "착용 컷이 전부 다른 컬러웨이다",
            }],
        )
        self.assertEqual(rows["P2"]["goldLabel"], "MALE")
        self.assertEqual(rows["P2"]["resolvedBy"], "LINEAGE_RANK")
        self.assertEqual(rows["P2"]["humanConfirmed"]["decisionId"], "BR-2")
        # 무엇을 확인했는지 남는다. 나중에 라벨이 바뀌면 이 확인은 그 라벨의 것이 아니다.
        self.assertEqual(rows["P2"]["humanConfirmed"]["label"], "MALE")
        self.assertEqual(index["humanConfirmedProductKeys"], ["P2"])
        self.assertNotIn("humanConfirmed", rows["P1"])

    def test_our_correction_is_applied_after_the_external_one(self) -> None:
        """정정은 여러 곳에서 온다. **사람이 확정한 값이 마지막에 이겨야** 한다."""
        rows, index = self.build(
            [dict(self.LINEAGE[0], rows=list(self.LINEAGE[0]["rows"]))],
            corrections=[{"productKey": "P1", "goldLabel": "MALE", "goldSource": "외부 하네스"}],
            extra_corrections=[{
                "productKey": "P1", "goldLabel": "FEMALE",
                "goldSource": "HUMAN_DECISION:BR-3", "decisionId": "BR-3",
            }],
        )
        self.assertEqual(rows["P1"]["goldLabel"], "FEMALE")
        self.assertEqual(len(index["corrections"]), 2)
