"""AI24Times 정적 사이트 생성기.

data/ 의 JSON(브리핑·용어사전·사이트 설정)을 읽어 templates/ 의 Jinja 템플릿으로
dist/ 에 정적 HTML을 만든다. 서버 없이 어디에나 올릴 수 있다.

    python -m generator.build                 # dist/ 에 빌드
    python -m generator.build --out public    # 출력 폴더 지정
    python -m generator.build --base-url https://example.com
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

KST = timezone(timedelta(hours=9))
# 한국어 본문을 1분에 읽는 글자 수. 읽는 시간 표시에만 쓴다.
CHARS_PER_MINUTE = 500
FEED_SIZE = 20

REQUIRED_ARTICLE_FIELDS = ("rank", "title", "source", "url", "topic", "summary")


class DataError(Exception):
    """data/ 의 JSON이 약속한 형식과 다를 때. 어느 파일의 무엇이 문제인지 메시지에 담는다."""


# ---------------------------------------------------------------- 데이터 읽기


def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise DataError(f"{path.relative_to(ROOT)}: JSON 형식 오류 ({e})") from e


def validate_briefing(b: dict, stem: str, topics: dict) -> None:
    where = f"data/briefings/{stem}.json"
    try:
        date.fromisoformat(stem)
    except ValueError:
        raise DataError(f"{where}: 파일 이름은 YYYY-MM-DD.json 이어야 합니다")
    if b.get("date") != stem:
        raise DataError(f"{where}: date 필드({b.get('date')!r})가 파일 이름과 다릅니다")

    insight = b.get("insight") or {}
    if not insight.get("headline") or not insight.get("body"):
        raise DataError(f"{where}: insight.headline 과 insight.body 가 필요합니다")

    articles = b.get("articles") or []
    if not articles:
        raise DataError(f"{where}: articles 가 비어 있습니다")
    for a in articles:
        missing = [k for k in REQUIRED_ARTICLE_FIELDS if not a.get(k)]
        if missing:
            raise DataError(f"{where}: {a.get('rank', '?')}번 기사에 {', '.join(missing)} 필드가 없습니다")
        if a["topic"] not in topics:
            raise DataError(
                f"{where}: {a['rank']}번 기사의 topic {a['topic']!r} 이 data/site.json 에 없습니다"
            )
    ranks = sorted(a["rank"] for a in articles)
    if ranks != list(range(1, len(articles) + 1)):
        raise DataError(f"{where}: rank 는 1부터 빠짐없이 매겨야 합니다 (지금: {ranks})")


def load_briefings(directory: Path, topics: dict) -> list[dict]:
    briefings = []
    for path in sorted(directory.glob("*.json")):
        b = load_json(path)
        validate_briefing(b, path.stem, topics)
        b["articles"].sort(key=lambda a: a["rank"])
        b.setdefault("korea", [])
        briefings.append(b)
    if not briefings:
        raise DataError(f"{directory.relative_to(ROOT)}: 브리핑 JSON이 하나도 없습니다")
    briefings.sort(key=lambda b: b["date"], reverse=True)  # 최신이 앞
    return briefings


# ---------------------------------------------------------------- 용어 링크


class TermLinker:
    """본문에서 용어사전에 있는 단어를 찾아 설명 패널을 여는 버튼으로 바꾼다."""

    def __init__(self, glossary: dict):
        self.glossary = glossary
        # 긴 이름부터 찾아야 "오픈 웨이트 모델"이 "모델"보다 먼저 잡힌다.
        names = sorted(((t["name"], slug) for slug, t in glossary.items()), key=lambda x: -len(x[0]))
        self.slug_by_name = dict(names)
        self.pattern = re.compile("|".join(re.escape(n) for n, _ in names)) if names else None

    def find(self, text: str) -> list[str]:
        if not self.pattern:
            return []
        return list(dict.fromkeys(self.slug_by_name[m.group(0)] for m in self.pattern.finditer(text)))

    def link(self, text: str) -> Markup:
        """같은 용어는 문단마다 처음 한 번만 링크한다. 매번 링크하면 본문이 버튼투성이가 된다."""
        if not self.pattern:
            return escape(text)
        out, last, seen = [], 0, set()
        for m in self.pattern.finditer(text):
            slug = self.slug_by_name[m.group(0)]
            if slug in seen:
                continue
            seen.add(slug)
            out.append(escape(text[last : m.start()]))
            out.append(
                Markup('<button type="button" class="term" data-term="{}">{}</button>').format(slug, m.group(0))
            )
            last = m.end()
        out.append(escape(text[last:]))
        return Markup("").join(out)


def briefing_text(b: dict) -> str:
    parts = [b["insight"]["headline"], *b["insight"]["body"], b["insight"].get("watch", "")]
    for a in b["articles"]:
        parts += [a["title"], a["summary"], a.get("implication", "")]
    return "\n".join(parts)


# ---------------------------------------------------------------- 템플릿 필터


def kdate(value: str) -> str:
    d = date.fromisoformat(value)
    weekday = "월화수목금토일"[d.weekday()]
    return f"{d.year}년 {d.month}월 {d.day}일 ({weekday})"


def comma(value: int) -> str:
    return f"{value:,}"


def make_env(linker: TermLinker) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        undefined=StrictUndefined,  # 템플릿 오타가 빈 문자열로 조용히 넘어가지 않게
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["kdate"] = kdate
    env.filters["comma"] = comma
    env.filters["link_terms"] = linker.link
    return env


# ---------------------------------------------------------------- 빌드


def safe_clean(out: Path) -> None:
    out = out.resolve()
    if out == ROOT or out in ROOT.parents or out in (DATA_DIR, TEMPLATE_DIR, STATIC_DIR):
        raise SystemExit(f"출력 폴더로 {out} 를 쓸 수 없습니다 (프로젝트 폴더를 지우게 됩니다)")
    if out.exists():
        shutil.rmtree(out)


def build(out: Path, base_url: str | None = None, data_dir: Path = DATA_DIR) -> dict:
    site = load_json(data_dir / "site.json")
    if base_url:
        site["base_url"] = base_url
    site["base_url"] = site["base_url"].rstrip("/")
    topics: dict = site["topics"]
    glossary: dict = load_json(data_dir / "glossary.json")
    briefings = load_briefings(data_dir / "briefings", topics)

    linker = TermLinker(glossary)
    env = make_env(linker)
    now = datetime.now(KST)

    safe_clean(out)
    out.mkdir(parents=True)

    def render(template: str, path: str, **ctx) -> None:
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(template).render(site=site, topics=topics, built_at=now, **ctx)
        target.write_text(html, encoding="utf-8")

    # 브리핑 페이지: 최신 호는 / 와 /archive/날짜.html 두 곳에 같은 내용으로 나간다.
    for i, b in enumerate(briefings):
        text = briefing_text(b)
        terms = linker.find(text)
        ctx = dict(
            b=b,
            reading_min=max(1, round(len(text) / CHARS_PER_MINUTE)),
            terms={slug: glossary[slug] for slug in terms},
            newer=briefings[i - 1] if i > 0 else None,
            older=briefings[i + 1] if i + 1 < len(briefings) else None,
        )
        render("daily.html.j2", f"archive/{b['date']}.html",
               nav="archive", canonical_path=f"/archive/{b['date']}.html", is_home=False, **ctx)
        if i == 0:
            render("daily.html.j2", "index.html", nav="today", canonical_path="/", is_home=True, **ctx)

    # 지난 호: 월별로 묶는다.
    by_month: dict[str, list[dict]] = defaultdict(list)
    for b in briefings:
        by_month[b["date"][:7]].append(b)
    render("archive_index.html.j2", "archive/index.html",
           nav="archive", canonical_path="/archive/", by_month=by_month, total=len(briefings))

    # 주제별: 모든 호의 기사를 주제로 모은다.
    by_topic: dict[str, list[dict]] = {slug: [] for slug in topics}
    for b in briefings:
        for a in b["articles"]:
            by_topic[a["topic"]].append({**a, "date": b["date"]})
    render("topics_index.html.j2", "topics/index.html",
           nav="topics", canonical_path="/topics/", by_topic=by_topic)
    for slug, items in by_topic.items():
        render("topic.html.j2", f"topics/{slug}.html",
               nav="topics", canonical_path=f"/topics/{slug}.html", slug=slug, items=items)

    render("glossary.html.j2", "glossary.html", nav="glossary", canonical_path="/glossary.html",
           glossary=dict(sorted(glossary.items(), key=lambda kv: kv[1]["name"])))

    # RSS: 한 호 = 한 항목. 발행 시각은 설정의 발행 시각(KST)으로 고정한다.
    hour = site.get("publish_hour_kst", 7)
    feed_items = [
        {**b, "pub_date": format_datetime(datetime.fromisoformat(b["date"]).replace(hour=hour, tzinfo=KST))}
        for b in briefings[:FEED_SIZE]
    ]
    render("feed.xml.j2", "feed.xml", items=feed_items, last_build=format_datetime(now))

    # 지난 호 검색용 색인. 브라우저가 받아서 직접 거른다.
    index = [
        {
            "date": b["date"],
            "rank": a["rank"],
            "title": a["title"],
            "title_original": a.get("title_original", ""),
            "source": a["source"],
            "topic": topics[a["topic"]]["name"],
            "summary": a["summary"],
            "page": f"/archive/{b['date']}.html#a{a['rank']}",
        }
        for b in briefings
        for a in b["articles"]
    ]
    (out / "search-index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    shutil.copytree(STATIC_DIR, out / "assets")

    return {"briefings": len(briefings), "articles": len(index), "out": str(out)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI24Times 정적 사이트 빌드")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="출력 폴더 (기본: dist)")
    parser.add_argument("--base-url", help="canonical·RSS 링크에 쓸 사이트 주소 (기본: site.json 값)")
    args = parser.parse_args(argv)
    try:
        result = build(Path(args.out), args.base_url)
    except DataError as e:
        print(f"데이터 오류: {e}", file=sys.stderr)
        return 1
    print(f"브리핑 {result['briefings']}호 · 기사 {result['articles']}건 → {result['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
