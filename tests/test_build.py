"""빌드가 끝까지 돌고, 잘못된 데이터는 어느 파일이 문제인지 알려 주는지 확인한다.

    python -m unittest
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from generator.build import DATA_DIR, DataError, TermLinker, build, localize


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
            "work/index.html",
            "work/ai24times.html",
            "work/example-habit-app.html",
        ]:
            self.assertTrue((out / path).is_file(), path)

        home = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/archive/2026-10-29.html"', home)  # 배너·목록이 최신 호를 가리킴
        self.assertIn('href="/work/ai24times.html"', home)       # 작업 미리보기

        daily = (out / "archive/2026-10-29.html").read_text(encoding="utf-8")
        self.assertIn('id="a1"', daily)
        self.assertIn('data-term="open-weight-model"', daily)

        case = (out / "work/ai24times.html").read_text(encoding="utf-8")
        self.assertIn('id="step-3"', case)                       # 과정 단계가 모두 나옴

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


    def _edit_work(self, data: Path, slug: str, change) -> None:
        path = data / "work" / f"{slug}.json"
        w = json.loads(path.read_text(encoding="utf-8"))
        change(w)
        path.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")

    def test_rejects_missing_work_image(self):
        data = self._copy_data()
        self._edit_work(data, "ai24times", lambda w: w.update(cover="/assets/work/nope.png"))
        with self.assertRaisesRegex(DataError, "ai24times.json.*nope.png"):
            build(self.tmp / "dist", data_dir=data)

    def test_rejects_bad_step_date(self):
        data = self._copy_data()
        self._edit_work(data, "example-habit-app", lambda w: w["process"][1].update(date="8월 18일"))
        with self.assertRaisesRegex(DataError, "process 2번째 단계"):
            build(self.tmp / "dist", data_dir=data)

    def test_process_sorted_by_date(self):
        data = self._copy_data()
        self._edit_work(data, "example-habit-app", lambda w: w["process"].reverse())
        out = self.tmp / "dist"
        build(out, data_dir=data)
        html = (out / "work/example-habit-app.html").read_text(encoding="utf-8")
        self.assertLess(html.index("2026-08-04"), html.index("2026-09-22"))


class EnglishBuildTest(unittest.TestCase):
    """영어판(/en/) 페이지가 같은 구조로 나오고, 번역이 없는 곳은 원문과 안내로 대신하는지."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.out = cls.tmp / "dist"
        build(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, path: str) -> str:
        return (self.out / path).read_text(encoding="utf-8")

    def test_same_pages_in_both_languages(self):
        ko = {p.relative_to(self.out).as_posix() for p in self.out.rglob("*.html") if "en" not in p.relative_to(self.out).parts[:1] and "assets" not in p.parts}
        en = {p.relative_to(self.out / "en").as_posix() for p in (self.out / "en").rglob("*.html")}
        self.assertEqual(ko, en)

    def test_header_links_to_other_language(self):
        ko = self.read("archive/2026-10-29.html")
        en = self.read("en/archive/2026-10-29.html")
        self.assertIn('href="/en/archive/2026-10-29.html" hreflang="en"', ko)
        self.assertIn('href="/archive/2026-10-29.html" hreflang="ko"', en)
        self.assertIn('id="copy-link"', ko)
        self.assertNotIn("RSS 구독", ko)

    def test_translated_page_uses_en_fields(self):
        en = self.read("en/archive/2026-10-29.html")
        self.assertIn('<html lang="en"', en)
        self.assertIn("Models get smaller", en)
        self.assertIn('href="/en/topics/models.html"', en)       # 사이트 안 링크에 /en 이 붙음
        self.assertIn('data-term="open-weight-model"', en)       # 영어 본문에서도 용어 링크
        self.assertNotIn("noindex", en)

    def test_untranslated_page_falls_back_and_is_noindex(self):
        en = self.read("en/archive/2026-10-28.html")
        self.assertIn("에이전트가", en)                            # 한국어 원문
        self.assertIn("been translated yet", en)
        self.assertIn('content="noindex, follow"', en)

    def test_english_search_index_points_to_english_pages(self):
        index = json.loads(self.read("en/search-index.json"))
        self.assertTrue(index[0]["page"].startswith("/en/archive/"))


class DataFileTest(unittest.TestCase):
    def test_ui_strings_must_match_between_languages(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            data = tmp / "data"
            shutil.copytree(DATA_DIR, data)
            ui = json.loads((data / "ui.json").read_text(encoding="utf-8"))
            del ui["en"]["copied"]
            (data / "ui.json").write_text(json.dumps(ui, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(DataError, "ui.json.*copied"):
                build(tmp / "dist", data_dir=data)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_localize_replaces_nested_en_fields(self):
        raw = {"title": "제목", "title_en": "Title", "steps": [{"phase": "개발", "phase_en": "Build"}, {"phase": "출시"}]}
        self.assertEqual(localize(raw, "en"), {"title": "Title", "steps": [{"phase": "Build"}, {"phase": "출시"}]})
        self.assertEqual(localize(raw, "ko"), {"title": "제목", "steps": [{"phase": "개발"}, {"phase": "출시"}]})


class TermLinkerTest(unittest.TestCase):
    def setUp(self):
        self.linker = TermLinker({"model": "모델", "open-weight-model": "오픈 웨이트 모델"})

    def test_longer_term_wins(self):
        html = str(self.linker.link("오픈 웨이트 모델이 나왔다"))
        self.assertIn('data-term="open-weight-model"', html)
        self.assertNotIn('data-term="model"', html)

    def test_links_first_occurrence_only_and_escapes(self):
        html = str(self.linker.link("<b>모델</b>과 모델"))
        self.assertEqual(html.count("data-term="), 1)
        self.assertIn("&lt;b&gt;", html)

    def test_english_matches_case_plural_and_word_boundary(self):
        linker = TermLinker({"token": "Token", "agent": "Agent"}, english=True)
        html = str(linker.link("128,000 tokens; an agentic tool; Agents"))
        self.assertIn('data-term="token">tokens</button>', html)
        self.assertIn('data-term="agent">Agents</button>', html)
        self.assertIn("agentic", html)                            # 단어 일부는 링크하지 않음
        self.assertEqual(html.count('data-term="agent"'), 1)


if __name__ == "__main__":
    unittest.main()
