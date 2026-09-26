"""잡화 가져오기 — GT 경로는 프로필 한 곳에서 읽고, GT 사본은 만들지 않는다(CLAUDE.md 규칙 4).

경로가 두 곳에 적혀 있으면 새 판이 와서 프로필만 고쳤을 때 가져오기가 옛 판의 해시를 계속 적는다. 사본이 있으면
시트를 새로 고친 뒤에도 옛 답이 이 저장소에 남는다.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK / "adapters"))

import import_accessories_category_gender_sources as importer  # noqa: E402


class ImportPathsTest(unittest.TestCase):
    def test_the_gt_path_comes_from_the_profile(self) -> None:
        profile = json.loads((PACK / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(str(importer.GT_SOURCE), profile["gtTask"]["gt"]["path"])

    def test_the_importer_writes_no_gt_copy(self) -> None:
        body = (PACK / "adapters/import_accessories_category_gender_sources.py").read_text(encoding="utf-8")
        self.assertNotIn("accessories-product-gt.jsonl", body, "GT 사본 파일 이름이 가져오기에 없어야 한다")
        self.assertNotIn("write_jsonl(golden / \"accessories-product-gt", body)


if __name__ == "__main__":
    unittest.main()
