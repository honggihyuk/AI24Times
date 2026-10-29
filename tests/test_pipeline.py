"""수집·선정 파이프라인. 네트워크 없이 가짜 피드로 돌린다.

    python -m unittest tests.test_pipeline
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline import feeds
from pipeline.collect import load_history, run
from pipeline.feeds import Entry, FetchResult
from pipeline.select import Item, pick
from pipeline.text import keywords, normalize_link, same_title
from pipeline.verify import paragraph_chars

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 29, 8, 0, tzinfo=KST)


def rfc822(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(items) -> bytes:
    """items: [(title, link, hours_ago or None, [categories])]"""
    body = []
    for title, link, ago, cats in items:
        date = f"<pubDate>{rfc822(ago)}</pubDate>" if ago is not None else ""
        cat = "".join(f"<category>{c}</category>" for c in cats)
        body.append(f"<item><title>{title}</title><link>{link}</link>{date}{cat}<description>desc</description></item>")
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{"".join(body)}</channel></rss>'.encode()


def atom(items) -> bytes:
    body = []
    for title, link, ago in items:
        ts = (NOW - timedelta(hours=ago)).isoformat()
        body.append(f'<entry><title>{title}</title><link rel="alternate" href="{link}"/><published>{ts}</published></entry>')
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>{"".join(body)}</feed>'.encode()


def src(sid, weight=2.5, **kw):
    s = {"id": sid, "name": sid.upper(), "url": f"https://{sid}.example/feed", "weight": weight,
         "role": "news", "pool": "main", "enabled": True}
    s.update(kw)
    return s


def config(sources, **kw):
    cfg = {"lookback_hours": 24, "top_n": 3, "per_source_cap": 2, "reserve_n": 2, "korea_n": 1,
           "korea_per_source_cap": 1, "sources": sources}
    cfg.update(kw)
    return cfg


EMPTY_HISTORY = {"links": set(), "titles": []}


class TextTest(unittest.TestCase):
    def test_korean_and_english_titles_share_keys(self):
        ko = keywords("구글, 사이버보안에 강한 제미나이 4 카본 공개")
        en = keywords("Google unveils Gemini 4 Carbon for cyber defense")
        self.assertIn("google", ko)
        self.assertIn("gemini", ko)
        self.assertTrue({"google", "gemini", "carbon"} <= en)
        self.assertNotIn("공개", ko)          # 어디에나 나오는 말은 뺀다

    def test_korean_predicates_and_counts_are_not_keys(self):
        k = keywords("애플, 16년 만에 터치스크린 맥북 출시...스티브 잡스의 원칙 뒤집었다")
        self.assertTrue({"apple", "터치스크린", "맥북", "잡스"} <= k)
        self.assertNotIn("뒤집었다", k)
        self.assertNotIn("16년", k)
        self.assertNotIn("출시...스티브", k)    # 말줄임표에서 끊는다

    def test_josa_and_mixed_tokens(self):
        self.assertIn("openai", keywords("오픈AI는 새 모델을"))
        self.assertIn("gpt-5", keywords("GPT-5의 성능"))

    def test_years_are_not_keys(self):
        self.assertNotIn("2027", keywords("가트너 2027년 전망"))
        self.assertNotIn("2027", keywords("Gartner 2027 forecast"))
        self.assertIn("50000", keywords("50000 GPUs"))   # 연도가 아닌 큰 숫자는 남긴다

    def test_same_title_prefix_rule(self):
        long_ = "Added PreModelSwitch and PostModelSwitch hook events (block, confirm, or annotate a model switch)"
        self.assertTrue(same_title(long_[:60], long_))
        self.assertFalse(same_title("Short title", "Short title that continues"))  # 40자 미만은 앞부분 일치로 안 침

    def test_normalize_link_drops_tracking(self):
        self.assertEqual(normalize_link("https://www.a.com/x/?utm_source=rss&id=3"),
                         normalize_link("http://a.com/x?id=3"))


class FeedParseTest(unittest.TestCase):
    def test_rss_atom_and_naive_dates(self):
        s = src("a")
        self.assertEqual(len(feeds.parse(rss([("T", "https://a/1", 1, [])]), s)), 1)
        self.assertEqual(len(feeds.parse(atom([("T", "https://a/2", 1)]), s)), 1)
        ko = src("k", timezone="+09:00")
        body = b'<rss><channel><item><title>t</title><link>https://k/1</link><pubDate>2026-10-29 07:00:00</pubDate></item></channel></rss>'
        e = feeds.parse(body, ko)[0]
        self.assertEqual(e.published, datetime(2026, 10, 28, 22, 0, tzinfo=timezone.utc))  # KST 07시 = UTC 22시

    def test_html_instead_of_feed_is_an_error(self):
        with self.assertRaisesRegex(feeds.FeedError, "HTML"):
            feeds.parse(b"<!DOCTYPE html><html><body>Just a moment...</body></html>", src("a"))


class RunTest(unittest.TestCase):
    def run_with(self, cfg, bodies, history=EMPTY_HISTORY, verify=None, failures=None):
        fetched = {sid: FetchResult(sid, True, body, 200) for sid, body in bodies.items()}
        for sid, err in (failures or {}).items():
            fetched[sid] = FetchResult(sid, False, http_status=403, error=err)
        return run(cfg, fetched, NOW, history, verify)

    def test_funnel_filters_and_reasons(self):
        cfg = config([src("a"), src("b", exclude_categories=["Customer Story"], drop_uncategorized=True)])
        result = self.run_with(cfg, {
            "a": rss([
                ("Nvidia ships Rubin GPU", "https://a/1", 2, []),
                ("Old story about Meta", "https://a/2", 40, []),          # 24시간 밖
                ("Undated item", "https://a/3", None, []),                # 날짜 없음
                ("Join our webinar on agents", "https://a/4", 1, []),     # 홍보 (제목)
                ("The new economics of enterprise AI", "https://a/5", 1, ["Artificial intelligence", "sponsored"]),  # 홍보 (분류)
            ]),
            "b": rss([
                ("How Acme uses ChatGPT", "https://b/1", 1, ["Customer Story"]),
                ("Untagged post", "https://b/2", 1, []),
                ("Rubin GPU from Nvidia arrives", "https://b/3", 1, ["Product"]),
            ]),
        })
        f = result["funnel"]
        self.assertEqual(f["entries_seen"], 8)
        self.assertEqual(f["dropped"]["outside_lookback"], 1)
        self.assertEqual(f["dropped"]["no_date"], 1)
        self.assertEqual(f["dropped"]["promo"], 2)
        self.assertEqual(f["dropped"]["not_news"], 2)
        self.assertEqual(f["main_candidates"], 2)
        # 같은 사건(Nvidia Rubin)이라 하나만 실린다
        self.assertEqual(f["selected"], 1)
        self.assertEqual(f["after_selection"]["same_event"], 1)
        top = result["selected"][0]
        self.assertEqual(top["score_parts"]["coverage"], 1.0)     # 다른 출처 1곳이 같은 사건을 다룸
        self.assertEqual(len(top["related"]), 1)

    def test_korean_story_gets_cross_coverage(self):
        cfg = config([src("en"), src("ko", weight=2.0, lang="ko")])
        result = self.run_with(cfg, {
            "en": rss([("Google unveils Gemini 4 Carbon", "https://en/1", 3, [])]),
            "ko": rss([("구글, 제미나이 4 카본 공개", "https://ko/1", 1, [])]),
        })
        ko = [c for c in result["selected"] + result["reserve"] + result["not_selected"] if c["source"] == "KO"]
        en = result["selected"][0]
        self.assertEqual(en["coverage_sources"], ["KO"])            # 한국어 기사가 교차보도로 잡힘
        self.assertEqual(ko[0]["reason"], "same_event")

    def test_common_company_names_alone_do_not_make_same_event(self):
        # 2026-10-29 실제 실행에서 나온 문제: '구글'·'제미나이'만 겹치는 다른 기사들이 한 묶음으로 엮였다.
        cfg = config([src("a"), src("b"), src("c"), src("d")], top_n=5, per_source_cap=5)
        result = self.run_with(cfg, {
            "a": rss([("Google launches Gemini 4 Carbon", "https://a/1", 1, []),
                      ("Google expands Gemini Search ads", "https://a/2", 2, [])]),
            "b": rss([("Google Gemini 4 Carbon is here", "https://b/1", 1, []),
                      ("Google opens Gemini to Android apps", "https://b/2", 2, [])]),
            "c": rss([("Google Gemini comes to Chromebooks", "https://c/1", 2, [])]),
            "d": rss([("Google tests Gemini in Maps", "https://d/1", 2, [])]),
        })
        groups = [a for a in result["selected"] if a["related"]]
        self.assertEqual(len(groups), 1)                          # Carbon 두 건만 한 사건
        self.assertIn("Carbon", groups[0]["title"])
        self.assertEqual(len(groups[0]["related"]), 1)
        self.assertEqual(result["funnel"]["selected"], 5)         # 나머지 구글 기사는 각각 실릴 수 있다

    def test_signal_source_counts_but_is_never_selected(self):
        cfg = config([src("a"), src("hn", weight=0, role="signal")])
        result = self.run_with(cfg, {
            "a": rss([("Anthropic releases Claude Opus", "https://a/claude", 2, [])]),
            "hn": rss([("Anthropic releases Claude Opus", "https://www.a/claude/", 1, [])]),
        })
        self.assertEqual(result["funnel"]["dropped"]["signal_only"], 1)
        self.assertEqual(result["selected"][0]["coverage_sources"], ["HN"])

    def test_source_cap_rises_one_step_only_when_needed(self):
        cfg = config([src("a"), src("b")], top_n=4, per_source_cap=2)
        result = self.run_with(cfg, {
            "a": rss([(f"Alpha{i} story", f"https://a/{i}", i, []) for i in range(1, 6)]),
            "b": rss([("Beta1 story", "https://b/1", 1, [])]),
        })
        self.assertEqual(result["funnel"]["selected"], 4)
        self.assertEqual(result["funnel"]["source_cap_used"], 3)    # 2 → 3, 한 칸만
        self.assertTrue(any("상한" in w for w in result["warnings"]))

    def test_already_published_is_skipped(self):
        cfg = config([src("a")])
        history = {"links": {normalize_link("https://a/1")},
                   "titles": ["Microsoft and OpenAI sign a new multi-year cloud agreement for Azure"]}
        result = self.run_with(cfg, {"a": rss([
            ("Repeat by link", "https://a/1?utm_source=rss", 1, []),
            ("Microsoft and OpenAI sign a new multi-year cloud agreement", "https://a/2", 1, []),  # 잘린 제목
            ("Fresh Xylophone news", "https://a/3", 1, []),
        ])}, history=history)
        self.assertEqual(result["funnel"]["dropped"]["already_published"], 2)
        self.assertEqual(result["funnel"]["main_candidates"], 1)

    def test_unreadable_original_is_replaced_from_reserve(self):
        cfg = config([src("a"), src("b"), src("c")], top_n=2, per_source_cap=1)
        bad = "https://a/1"
        verify = lambda url: {"ok": url != bad, "reason": "" if url != bad else "http_403", "http_status": 200, "chars": 900}
        result = self.run_with(cfg, {
            "a": rss([("Alpha story", bad, 1, [])]),
            "b": rss([("Beta story", "https://b/1", 2, [])]),
            "c": rss([("Gamma story", "https://c/1", 3, [])]),
        }, verify=verify)
        links = [a["link"] for a in result["selected"]]
        self.assertNotIn(bad, links)
        self.assertEqual(len(links), 2)
        self.assertEqual(result["funnel"]["substitutions"], 1)
        self.assertEqual(result["substitutions"][0]["reason"], "http_403")
        self.assertEqual(result["funnel"]["after_selection"]["original_unreadable"], 1)

    def test_failed_feed_keeps_its_reason(self):
        cfg = config([src("a"), src("b")])
        result = self.run_with(cfg, {"a": rss([("Alpha story", "https://a/1", 1, [])])},
                               failures={"b": "HTTP 403 Forbidden: Just a moment..."})
        row = next(r for r in result["sources"] if r["id"] == "b")
        self.assertEqual(row["status"], "failed")
        self.assertIn("Just a moment", row["error"])
        self.assertEqual(result["funnel"]["feeds_failed"], 1)

    def test_all_items_filtered_warns_about_feed(self):
        cfg = config([src("o", drop_uncategorized=True)])
        result = self.run_with(cfg, {"o": rss([(f"Post {i}", f"https://o/{i}", 1, []) for i in range(3)])})
        self.assertTrue(any("분류" in w for w in result["warnings"]))

    def test_korea_pool_is_separate(self):
        cfg = config([src("a"), src("k", weight=2.0, pool="korea", lang="ko")])
        result = self.run_with(cfg, {
            "a": rss([("Alpha story", "https://a/1", 1, [])]),
            "k": rss([("국내 지자체 챗봇 시범 운영", "https://k/1", 1, [])]),
        })
        self.assertEqual(result["funnel"]["korea_candidates"], 1)
        self.assertEqual(result["korea"][0]["source"], "K")
        self.assertNotIn("K", [a["source"] for a in result["selected"]])


class PickTest(unittest.TestCase):
    def test_same_event_rule_is_never_relaxed(self):
        e = Entry("a", "t", "https://a/1", datetime.now(timezone.utc))
        items = [Item(e, src("a"), weight=3 - i * 0.1) for i in range(3)]
        for it in items:
            it.cluster = 0
        chosen, _ = pick(items, 3, 3)
        self.assertEqual(len(chosen), 1)


class HistoryTest(unittest.TestCase):
    def test_reads_briefings_and_earlier_candidates_only(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "briefings").mkdir()
            (d / "candidates").mkdir()
            (d / "briefings" / "2026-10-28.json").write_text(json.dumps(
                {"articles": [{"url": "https://x/1", "title": "제목", "title_original": "Title"}]}), encoding="utf-8")
            (d / "candidates" / "2026-10-28.json").write_text(json.dumps(
                {"selected": [{"link": "https://x/2", "title": "Picked"}], "korea": []}), encoding="utf-8")
            (d / "candidates" / "2026-10-29.json").write_text(json.dumps(
                {"selected": [{"link": "https://x/3", "title": "Today"}], "korea": []}), encoding="utf-8")
            h = load_history(d, "2026-10-29")
            self.assertIn(normalize_link("https://x/2"), h["links"])
            self.assertNotIn(normalize_link("https://x/3"), h["links"])   # 오늘 파일은 빼야 다시 돌려도 같은 결과
            self.assertIn("Title", h["titles"])


class VerifyTest(unittest.TestCase):
    def test_counts_only_paragraph_text(self):
        html = "<nav><p>menu menu menu</p></nav><p>" + "가" * 300 + "</p><script>var x='" + "a" * 999 + "'</script><p>" + "나" * 300 + "</p>"
        self.assertEqual(paragraph_chars(html), 600)


if __name__ == "__main__":
    unittest.main()
