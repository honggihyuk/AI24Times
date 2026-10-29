"""AI24Times 정적 사이트 생성기.

data/ 의 JSON(브리핑·작업·용어사전·사이트 설정·화면 문구)을 읽어 templates/ 의 Jinja 템플릿으로
dist/ 에 정적 HTML을 만든다. 한국어는 dist/, 영어는 dist/en/ 에 같은 구조로 나간다.

    python -m generator.build                 # dist/ 에 빌드
    python -m generator.build --out public    # 출력 폴더 지정
    python -m generator.build --base-url https://example.com

번역: 콘텐츠 JSON의 어느 필드든 이름 뒤에 _en 을 붙인 필드를 두면 영어 페이지에서 그 값을 쓴다
(예: "title" / "title_en"). 없으면 한국어 원문을 그대로 보여 주고, 그 페이지는 검색엔진에서 뺀다.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import zlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

# 언어 → 주소 앞부분. 첫 번째가 기본 언어(사이트 루트).
LANGS = {"ko": "", "en": "/en"}
DEFAULT_LANG = "ko"

KST = timezone(timedelta(hours=9))
CHARS_PER_MINUTE = {"ko": 500, "en": 1100}   # 1분에 읽는 글자 수. 읽는 시간 표시에만 쓴다.
FEED_SIZE = 20
HOME_ISSUES = 8          # 홈 브리핑 목록에 보일 호 수
HOME_SLIDES = 3          # 홈 배너에서 넘겨 볼 호 수
HOME_WORKS = 4           # 홈 "직접 만든 작업"에 보일 작업 수

REQUIRED_ARTICLE_FIELDS = ("rank", "title", "source", "url", "topic", "summary")
REQUIRED_WORK_FIELDS = ("title", "category", "summary")
REQUIRED_STEP_FIELDS = ("date", "phase", "title")
WORK_STATUSES = ("in_progress", "done")

EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class DataError(Exception):
    """data/ 의 JSON이 약속한 형식과 다를 때. 어느 파일의 무엇이 문제인지 메시지에 담는다."""


def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise DataError(f"{path.name}: JSON 형식 오류 ({e})") from e


def parse_date(value: str, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DataError(f"{where}: 날짜 {value!r} 는 YYYY-MM-DD 형식이어야 합니다")


# ---------------------------------------------------------------- 번역


def localize(value, lang: str):
    """dict/list 를 깊이 복사하면서, lang 이 en 이면 x_en 필드가 있는 곳은 x 를 그 값으로 바꾼다.
    _en 필드 자체는 결과에서 뺀다."""
    if isinstance(value, list):
        return [localize(v, lang) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, v in value.items():
        if key.endswith("_en"):
            continue
        en_key = f"{key}_en"
        out[key] = localize(value[en_key] if lang == "en" and en_key in value else v, lang)
    return out


def load_ui(path: Path) -> dict:
    ui = load_json(path)
    missing = set(LANGS) - set(ui)
    if missing:
        raise DataError(f"data/ui.json: {', '.join(sorted(missing))} 언어 문구가 없습니다")
    base = set(ui[DEFAULT_LANG])
    for lang in LANGS:
        gap = base ^ set(ui[lang])
        if gap:
            raise DataError(f"data/ui.json: {lang} 문구의 키가 {DEFAULT_LANG} 와 다릅니다: {', '.join(sorted(gap))}")
    return ui


# ---------------------------------------------------------------- 브리핑


def validate_briefing(b: dict, stem: str, topics: dict) -> None:
    where = f"data/briefings/{stem}.json"
    parse_date(stem, f"{where} 파일 이름")
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
            raise DataError(f"{where}: {a['rank']}번 기사의 topic {a['topic']!r} 이 data/site.json 에 없습니다")
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
        raise DataError("data/briefings: 브리핑 JSON이 하나도 없습니다")
    briefings.sort(key=lambda b: b["date"])
    for no, b in enumerate(briefings, 1):  # 호수는 가장 오래된 호가 1호
        b["no"] = no
    briefings.reverse()  # 최신이 앞
    return briefings


def prepare_briefing(raw: dict, lang: str) -> dict:
    if lang == "en":
        # 영어 제목이 없으면 원문 제목이라도 쓴다. 원문은 대개 영어다.
        raw = {**raw, "articles": [
            {**a, "title_en": a["title_original"]} if "title_en" not in a and a.get("title_original") else a
            for a in raw["articles"]
        ]}
    b = localize(raw, lang)
    b["translated"] = lang == DEFAULT_LANG or "headline_en" in raw["insight"]
    return b


# ---------------------------------------------------------------- 작업(포트폴리오)


def asset_exists(src: str) -> bool:
    """/assets/... 는 static/ 안에 있어야 한다. 외부 주소(https://)는 검사하지 않는다."""
    if src.startswith("/assets/"):
        return (STATIC_DIR / src[len("/assets/"):]).is_file()
    return src.startswith(("https://", "http://"))


def validate_work(w: dict, stem: str) -> None:
    where = f"data/work/{stem}.json"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", stem):
        raise DataError(f"{where}: 파일 이름은 영문 소문자·숫자·하이픈만 쓸 수 있습니다 (주소에 그대로 쓰입니다)")
    missing = [k for k in REQUIRED_WORK_FIELDS if not w.get(k)]
    if missing:
        raise DataError(f"{where}: {', '.join(missing)} 필드가 없습니다")
    if w.get("status", "in_progress") not in WORK_STATUSES:
        raise DataError(f"{where}: status 는 {' 또는 '.join(WORK_STATUSES)} 중 하나여야 합니다")

    steps = w.get("process") or []
    if not steps:
        raise DataError(f"{where}: process 에 단계가 하나 이상 있어야 합니다")
    for i, s in enumerate(steps, 1):
        missing = [k for k in REQUIRED_STEP_FIELDS if not s.get(k)]
        if missing:
            raise DataError(f"{where}: process {i}번째 단계에 {', '.join(missing)} 필드가 없습니다")
        parse_date(s["date"], f"{where}: process {i}번째 단계")

    images = [w.get("cover")]
    images += [g.get("src") for g in w.get("gallery", [])]
    images += [img.get("src") for s in steps for img in s.get("images", [])]
    for src in images:
        if src is not None and not asset_exists(src):
            raise DataError(f"{where}: 이미지 {src!r} 를 찾을 수 없습니다 (static/ 아래에 넣고 /assets/... 로 적어 주세요)")


def load_works(directory: Path) -> list[dict]:
    if not directory.is_dir():
        return []
    works = []
    for path in sorted(directory.glob("*.json")):
        w = load_json(path)
        validate_work(w, path.stem)
        w["slug"] = path.stem
        w["process"].sort(key=lambda s: s["date"])  # 같은 날짜끼리는 적은 순서 유지
        works.append(w)
    # 최근에 손댄 작업이 앞. order 를 적으면 그 순서가 우선한다.
    works.sort(key=lambda w: w["process"][-1]["date"], reverse=True)
    works.sort(key=lambda w: w.get("order", 999))
    return works


def prepare_work(raw: dict, lang: str) -> dict:
    w = localize(raw, lang)
    for key in ("story", "gallery", "links", "stack", "role"):
        w.setdefault(key, [])
    w.setdefault("status", "in_progress")
    w["started"] = w["process"][0]["date"]
    w["updated"] = w["process"][-1]["date"]
    w["phases"] = list(dict.fromkeys(s["phase"] for s in w["process"]))
    w["translated"] = lang == DEFAULT_LANG or "title_en" in raw or "summary_en" in raw
    return w


# ---------------------------------------------------------------- 용어


def glossary_for(glossary: dict, lang: str) -> dict:
    """slug → {name, alt(다른 언어 이름), body}. 영어 페이지는 영어 이름과 설명을 쓴다."""
    out = {}
    for slug, t in glossary.items():
        if lang == "en":
            out[slug] = {"name": t.get("en") or t["name"], "alt": t["name"], "body": t.get("body_en") or t["body"]}
        else:
            out[slug] = {"name": t["name"], "alt": t.get("en", ""), "body": t["body"]}
    return out


class TermLinker:
    """본문에서 용어사전에 있는 단어를 찾아 설명 패널을 여는 버튼으로 바꾼다."""

    def __init__(self, names: dict, english: bool = False):
        # 긴 이름부터 찾아야 "오픈 웨이트 모델"이 "모델"보다 먼저 잡힌다.
        ordered = sorted(((n, slug) for slug, n in names.items()), key=lambda x: -len(x[0]))
        self.english = english
        self.slug_by_name = {(n.lower() if english else n): slug for n, slug in ordered}
        alternation = "|".join(re.escape(n) for n, _ in ordered)
        if not ordered:
            self.pattern = None
        elif english:
            # 영어는 대소문자를 가리지 않고, 단어 경계와 복수형(-s, -es)을 함께 잡는다.
            self.pattern = re.compile(rf"\b({alternation})(?:es|s)?\b", re.IGNORECASE)
        else:
            self.pattern = re.compile(f"({alternation})")

    def _slug(self, m) -> str:
        name = m.group(1)
        return self.slug_by_name[name.lower() if self.english else name]

    def find(self, text: str) -> list[str]:
        if not self.pattern:
            return []
        return list(dict.fromkeys(self._slug(m) for m in self.pattern.finditer(text)))

    def link(self, text: str) -> Markup:
        """같은 용어는 문단마다 처음 한 번만 링크한다. 매번 링크하면 본문이 버튼투성이가 된다."""
        if not self.pattern:
            return escape(text)
        out, last, seen = [], 0, set()
        for m in self.pattern.finditer(text):
            slug = self._slug(m)
            if slug in seen:
                continue
            seen.add(slug)
            out.append(escape(text[last : m.start()]))
            out.append(Markup('<button type="button" class="term" data-term="{}">{}</button>').format(slug, m.group(0)))
            last = m.end()
        out.append(escape(text[last:]))
        return Markup("").join(out)


def briefing_text(b: dict) -> str:
    parts = [b["insight"]["headline"], *b["insight"]["body"], b["insight"].get("watch", "")]
    for a in b["articles"]:
        parts += [a["title"], a["summary"], a.get("implication", "")]
    return "\n".join(parts)


# ---------------------------------------------------------------- 템플릿 환경


def make_env(lang: str, ui: dict, linker: TermLinker, prefix: str) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        undefined=StrictUndefined,  # 템플릿 오타가 빈 문자열로 조용히 넘어가지 않게
        trim_blocks=True,
        lstrip_blocks=True,
    )
    strings = ui[lang]

    def t(key: str, **kw) -> str:
        # 값 없이 부르면 {n} 같은 자리를 그대로 둔다 (브라우저 스크립트가 채우는 문구).
        return strings[key].format(**kw) if kw else strings[key]

    def long_date(value: str) -> str:
        d = date.fromisoformat(value)
        if lang == "en":
            return f"{EN_WEEKDAYS[d.weekday()]}, {EN_MONTHS[d.month - 1]} {d.day}, {d.year}"
        return f"{d.year}년 {d.month}월 {d.day}일 {'월화수목금토일'[d.weekday()]}요일"

    def month_label(value: str) -> str:
        y, m = int(value[:4]), int(value[5:7])
        return f"{EN_MONTHS[m - 1]} {y}" if lang == "en" else f"{y}년 {m}월"

    def local(url: str) -> str:
        """사이트 안 링크(/...)에 언어 앞부분을 붙인다. 정적 파일(/assets)과 외부 주소는 그대로."""
        if url.startswith("/") and not url.startswith(("/assets/", "//")):
            return prefix + url
        return url

    env.globals.update(t=t, lang=lang, p=prefix)
    env.filters.update(
        kdate=long_date,
        month_label=month_label,
        dotdate=lambda v: f"{int(v[5:7]):02d}.{int(v[8:10]):02d}",
        dotmonth=lambda v: v[:7].replace("-", "."),
        comma=lambda v: f"{v:,}",
        link_terms=linker.link,
        local=local,
    )
    return env


def favicon_ico(size: int = 32) -> bytes:
    """브라우저가 자동으로 요청하는 /favicon.ico. 브랜드 그라데이션 정사각형 PNG를 ICO로 감싼다.
    (SVG 아이콘은 <link rel="icon"> 로 따로 지정되어 있다.)"""
    a, b = (0x1B, 0x3A, 0x6B), (0x4F, 0x6B, 0xFF)
    rows = b""
    for y in range(size):
        rows += b"\x00"  # PNG 필터: 없음
        for x in range(size):
            k = (x + y) / (2 * (size - 1))
            rows += bytes(round(a[i] + (b[i] - a[i]) * k) for i in range(3))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows))
           + chunk(b"IEND", b""))
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def safe_clean(out: Path) -> None:
    out = out.resolve()
    if out == ROOT or out in ROOT.parents or out in (DATA_DIR, TEMPLATE_DIR, STATIC_DIR):
        raise SystemExit(f"출력 폴더로 {out} 를 쓸 수 없습니다 (프로젝트 폴더를 지우게 됩니다)")
    if out.exists():
        shutil.rmtree(out)


# ---------------------------------------------------------------- 빌드


def build_lang(lang: str, out_root: Path, raw: dict, ui: dict, now: datetime) -> int:
    """한 언어의 페이지 전부를 만든다. 만든 기사 색인 수를 돌려준다."""
    prefix = LANGS[lang]
    out = out_root / prefix.lstrip("/") if prefix else out_root
    site = localize(raw["site"], lang)
    topics = site["topics"]
    glossary = glossary_for(raw["glossary"], lang)
    linker = TermLinker({slug: g["name"] for slug, g in glossary.items()}, english=(lang == "en"))
    env = make_env(lang, ui, linker, prefix)
    briefings = [prepare_briefing(b, lang) for b in raw["briefings"]]
    works = [prepare_work(w, lang) for w in raw["works"]]

    def render(template: str, url: str, translated: bool = True, **ctx) -> None:
        """url 은 언어 앞부분을 뺀 주소 (예: /archive/2026-10-29.html, 홈은 /)."""
        path = url.lstrip("/") or "index.html"
        if path.endswith("/"):
            path += "index.html"
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        alternates = {code: f"{pre}{url}" for code, pre in LANGS.items()}
        html = env.get_template(template).render(
            site=site, topics=topics, built_at=now, translated=translated,
            canonical_path=alternates[lang], alternates=alternates,
            alt_lang=next(code for code in LANGS if code != lang),
            **ctx)
        target.write_text(html, encoding="utf-8")

    # 홈: 최근 브리핑 배너 + 목록 + 작업 미리보기. featured 를 먼저, 남는 자리는 나머지로.
    featured = [w for w in works if w.get("featured")] + [w for w in works if not w.get("featured")]
    render("home.html.j2", "/", nav="home",
           latest=briefings[0], slides=briefings[:HOME_SLIDES], recent=briefings[:HOME_ISSUES],
           featured=featured[:HOME_WORKS], total=len(briefings))

    # 하루치 브리핑
    for i, b in enumerate(briefings):
        text = briefing_text(b)
        render("daily.html.j2", f"/archive/{b['date']}.html", translated=b["translated"],
               nav="archive", b=b,
               # 번역이 없는 영어 페이지는 본문이 한국어이므로 한국어 속도로 센다.
               reading_min=max(1, round(len(text) / CHARS_PER_MINUTE[lang if b["translated"] else DEFAULT_LANG])),
               terms={slug: glossary[slug] for slug in linker.find(text)},
               newer=briefings[i - 1] if i > 0 else None,
               older=briefings[i + 1] if i + 1 < len(briefings) else None)

    by_month: dict[str, list[dict]] = defaultdict(list)
    for b in briefings:
        by_month[b["date"][:7]].append(b)
    render("archive_index.html.j2", "/archive/", nav="archive", by_month=by_month, total=len(briefings))

    # 주제별
    by_topic: dict[str, list[dict]] = {slug: [] for slug in topics}
    for b in briefings:
        for a in b["articles"]:
            by_topic[a["topic"]].append({**a, "date": b["date"]})
    render("topics_index.html.j2", "/topics/", nav="topics", by_topic=by_topic)
    for slug, items in by_topic.items():
        render("topic.html.j2", f"/topics/{slug}.html", nav="topics", slug=slug, items=items)

    render("glossary.html.j2", "/glossary.html", nav="glossary",
           glossary=dict(sorted(glossary.items(), key=lambda kv: kv[1]["name"].lower())))

    # 작업
    render("work_index.html.j2", "/work/", nav="work", works=works)
    for i, w in enumerate(works):
        render("work_detail.html.j2", f"/work/{w['slug']}.html", translated=w["translated"],
               nav="work", w=w,
               prev_work=works[i - 1] if i > 0 else None,
               next_work=works[i + 1] if i + 1 < len(works) else None)

    # RSS: 한 호 = 한 항목. 발행 시각은 설정의 발행 시각(KST)으로 고정한다.
    hour = site.get("publish_hour_kst", 7)
    feed_items = [
        {**b, "pub_date": format_datetime(datetime.fromisoformat(b["date"]).replace(hour=hour, tzinfo=KST))}
        for b in briefings[:FEED_SIZE]
    ]
    render("feed.xml.j2", "/feed.xml", items=feed_items, last_build=format_datetime(now))

    # 지난 기사 검색용 색인. 브라우저가 받아서 직접 거른다.
    index = [
        {
            "date": b["date"],
            "rank": a["rank"],
            "title": a["title"],
            "title_original": a.get("title_original", ""),
            "source": a["source"],
            "topic": topics[a["topic"]]["name"],
            "summary": a["summary"],
            "page": f"{prefix}/archive/{b['date']}.html#a{a['rank']}",
        }
        for b in briefings
        for a in b["articles"]
    ]
    (out / "search-index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return len(index)


def build(out: Path, base_url: str | None = None, data_dir: Path = DATA_DIR) -> dict:
    site = load_json(data_dir / "site.json")
    if base_url:
        site["base_url"] = base_url
    site["base_url"] = site["base_url"].rstrip("/")
    raw = {
        "site": site,
        "glossary": load_json(data_dir / "glossary.json"),
        "briefings": load_briefings(data_dir / "briefings", site["topics"]),
        "works": load_works(data_dir / "work"),
    }
    ui = load_ui(data_dir / "ui.json")
    now = datetime.now(KST)

    safe_clean(out)
    out.mkdir(parents=True)
    articles = {lang: build_lang(lang, out, raw, ui, now) for lang in LANGS}

    shutil.copytree(STATIC_DIR, out / "assets")
    (out / "favicon.ico").write_bytes(favicon_ico())

    return {"briefings": len(raw["briefings"]), "articles": articles[DEFAULT_LANG],
            "works": len(raw["works"]), "langs": list(LANGS), "out": str(out)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI24Times 정적 사이트 빌드")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="출력 폴더 (기본: dist)")
    parser.add_argument("--base-url", help="canonical·RSS 링크에 쓸 사이트 주소 (기본: site.json 값)")
    args = parser.parse_args(argv)
    try:
        r = build(Path(args.out), args.base_url)
    except DataError as e:
        print(f"데이터 오류: {e}", file=sys.stderr)
        return 1
    print(f"브리핑 {r['briefings']}호 · 기사 {r['articles']}건 · 작업 {r['works']}개 · 언어 {'/'.join(r['langs'])} → {r['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
