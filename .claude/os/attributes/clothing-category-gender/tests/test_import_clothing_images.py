import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ADAPTER = Path(__file__).resolve().parents[1] / "adapters/import_clothing_images.py"
spec = importlib.util.spec_from_file_location("clothing_import", ADAPTER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ImportImagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gt = self.root / "gt.jsonl"
        self.gt.write_text(json.dumps({"productKey": "SHOP:42", "goodsNo": "42", "goldLabel": "kept"}) + "\n")
        self.source = self.root / "source.jsonl"
        self.output = self.root / "output"
        self.index = self.output / "golden/index.jsonl"
        (self.root / "image.jpg").write_bytes(b"fixture image bytes")

    def run_import(self, rows):
        self.source.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return module.import_index(self.source, self.gt, self.index, self.output, {"THUMBNAIL"})

    def item(self, key="SHOP:42", file="image.jpg"):
        return {"productKey": key, "images": [{"imageId": "I1", "role": "THUMBNAIL", "file": file}]}

    def test_import_copies_evidence_without_changing_gt(self):
        before = self.gt.read_bytes()
        report = self.run_import([self.item()])
        row = json.loads(self.index.read_text())
        self.assertEqual((self.output / row["images"][0]["file"]).read_bytes(), b"fixture image bytes")
        self.assertEqual(self.gt.read_bytes(), before)
        self.assertEqual(report["uncoveredGtKeys"], [])
        self.assertFalse(report["networkUsed"])

    def test_same_product_number_on_other_platform_does_not_join(self):
        with self.assertRaisesRegex(ValueError, "연결되는 로컬 이미지"):
            self.run_import([self.item(key="OTHER:42")])
        self.assertFalse(self.index.exists())

    def test_invalid_source_preserves_existing_index(self):
        self.run_import([self.item()])
        before = self.index.read_bytes()
        with self.assertRaisesRegex(ValueError, "연결되는 로컬 이미지"):
            self.run_import([self.item(file="absent.jpg")])
        self.assertEqual(self.index.read_bytes(), before)

    def test_missing_references_remain_visible(self):
        item = self.item()
        item["images"].append({"imageId": "I2", "role": "THUMBNAIL", "file": "absent.jpg"})
        report = self.run_import([item])
        self.assertEqual(len(report["missingImages"]), 1)
        self.assertEqual(report["imageReferences"], 1)

    def test_duplicate_product_or_image_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "productKey"):
            self.run_import([self.item(), self.item()])
        item = self.item()
        item["images"] *= 2
        with self.assertRaisesRegex(ValueError, "imageId"):
            self.run_import([item])

    def test_empty_or_duplicate_gt_keys_are_rejected_without_writing_index(self):
        for gt_rows in [[{"productKey": ""}], [{"productKey": "SHOP:42"}] * 2]:
            with self.subTest(gt=gt_rows):
                self.gt.write_text("".join(json.dumps(row) + "\n" for row in gt_rows))
                with self.assertRaisesRegex(ValueError, "GT productKey"):
                    self.run_import([self.item()])
                self.assertFalse(self.index.exists())


if __name__ == "__main__":
    unittest.main()
