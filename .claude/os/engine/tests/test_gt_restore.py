"""«되돌려줘» — 마지막 «반영»이 남긴 사본으로 원본 GT를 되돌린다(`gt_decisions.restore`, CLI `gt_review.py restore`).

미리 보기는 아무것도 쓰지 않고, `--yes`면 지금 원본도 사본으로 남긴 뒤 되돌린다(되돌리기도 되돌릴 수 있게). 판정 원장은 건드리지 않는다.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_gt_review import SCRIPTS, Fixture  # noqa: E402

sys.path.insert(0, str(SCRIPTS))

import gt_decisions  # noqa: E402


class RestoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.temp.name))
        self.env = mock.patch.dict(os.environ, {k: v for k, v in self.fx.env.items() if k.startswith("CATALOG_OS_")})
        self.env.start()
        self.profile = self.fx.saved_profile()
        self.gt = Path(self.temp.name) / "data" / "gt.jsonl"

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_nothing_to_restore_says_so(self) -> None:
        with self.assertRaises(gt_decisions.DecisionRejected):
            gt_decisions.restore(self.profile, confirm=False)

    def test_preview_then_restore_keeps_the_current_file_too(self) -> None:
        original = self.gt.read_text(encoding="utf-8")
        backup = self.gt.with_name(self.gt.name + ".before-gt-review-20260101T000000Z")
        backup.write_text(original, encoding="utf-8")
        changed = original.replace('"PURPEL"', '"PURPLE"')
        self.gt.write_text(changed, encoding="utf-8")  # «반영»이 한 줄을 바꾼 뒤라고 치자
        preview = gt_decisions.restore(self.profile, confirm=False)
        self.assertEqual((preview["restored"], preview["linesToChange"]), (False, 1))
        self.assertEqual(self.gt.read_text(encoding="utf-8"), changed, "미리 보기는 쓰지 않는다")
        done = gt_decisions.restore(self.profile, confirm=True)
        self.assertTrue(done["restored"])
        self.assertEqual(self.gt.read_text(encoding="utf-8"), original)
        self.assertEqual(Path(done["kept"]).read_text(encoding="utf-8"), changed, "되돌리기 전 원본도 남는다")


if __name__ == "__main__":
    unittest.main()
