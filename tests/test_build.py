"""빌드가 끝까지 돌고, 잘못된 데이터는 어느 파일이 문제인지 알려 주는지 확인한다.

    python -m unittest
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from generator.build import DATA_DIR, DataError, TermLinker, build


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builds_all_pages(self):
        out = self.tmp / "dist"
        build(out)
        for path in [
            "index.html",
            "archive/index.html",
            "archive/2026-10-29.html",
            "archive/2026-10-28.html",
            "topics/index.html",
            "topics/models.html",
            "glossary.html",
            "feed.xml",
            "search-index.json",
            "assets/css/tokens.css",
            "assets/js/site.js",
        ]:
            self.assertTrue((out / path).is_file(), path)

        home = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="a1"', home)
        self.assertIn('data-term="open-weight-model"', home)

        index = json.loads((out / "search-index.json").read_text(encoding="utf-8"))
        self.assertEqual(index[0]["date"], "2026-10-29")  # 최신 호가 먼저

    def _copy_data(self) -> Path:
        data = self.tmp / "data"
        shutil.copytree(DATA_DIR, data)
        return data

    def test_rejects_unknown_topic(self):
        data = self._copy_data()
        path = data / "briefings" / "2026-10-29.json"
        b = json.loads(path.read_text(encoding="utf-8"))
        b["articles"][0]["topic"] = "nope"
        path.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(DataError, "2026-10-29.json.*nope"):
            build(self.tmp / "dist", data_dir=data)

    def test_rejects_rank_gap(self):
        data = self._copy_data()
        path = data / "briefings" / "2026-10-28.json"
        b = json.loads(path.read_text(encoding="utf-8"))
        b["articles"][1]["rank"] = 7
        path.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(DataError, "rank"):
            build(self.tmp / "dist", data_dir=data)


class TermLinkerTest(unittest.TestCase):
    def setUp(self):
        self.linker = TermLinker({
            "model": {"name": "모델"},
            "open-weight-model": {"name": "오픈 웨이트 모델"},
        })

    def test_longer_term_wins(self):
        html = str(self.linker.link("오픈 웨이트 모델이 나왔다"))
        self.assertIn('data-term="open-weight-model"', html)
        self.assertNotIn('data-term="model"', html)

    def test_links_first_occurrence_only_and_escapes(self):
        html = str(self.linker.link("<b>모델</b>과 모델"))
        self.assertEqual(html.count("data-term="), 1)
        self.assertIn("&lt;b&gt;", html)


if __name__ == "__main__":
    unittest.main()
