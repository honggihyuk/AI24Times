"""피드 가져오기와 해석.

실패한 피드는 이름만이 아니라 이유까지 남긴다(HTTP 코드, 응답 앞부분, 해석 오류).
참고한 사이트는 "2곳 실패"라는 숫자만 남겨서 피드 하나가 2주 동안 죽어 있는 걸 몰랐다.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from .text import html_to_text, nfkc

USER_AGENT = "Mozilla/5.0 (compatible; AI24TimesBot/0.1; +https://ai24times.example/about)"
TIMEOUT = 20
MAX_BYTES = 8_000_000


@dataclass
class FetchResult:
    source_id: str
    ok: bool
    body: bytes = b""
    http_status: Optional[int] = None
    error: str = ""


@dataclass
class Entry:
    source_id: str
    title: str
    link: str
    published: Optional[datetime]
    summary: str = ""
    categories: list = field(default_factory=list)


def fetch(source: dict, timeout: int = TIMEOUT) -> FetchResult:
    req = urllib.request.Request(source["url"], headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES)
            return FetchResult(source["id"], True, body, r.status)
    except urllib.error.HTTPError as e:
        # 응답 본문 앞부분을 남긴다. 403이 요청 한도인지, 봇 차단 페이지인지는 본문을 봐야 안다.
        snippet = ""
        try:
            snippet = html_to_text(e.read(4000).decode("utf-8", "replace"))[:160]
        except Exception:
            pass
        return FetchResult(source["id"], False, http_status=e.code, error=f"HTTP {e.code} {e.reason}: {snippet}".strip())
    except Exception as e:  # 시간 초과, DNS, SSL 등
        return FetchResult(source["id"], False, error=f"{type(e).__name__}: {e}"[:200])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_date(value: str, default_tz: timezone = timezone.utc) -> Optional[datetime]:
    """RFC 822(RSS), ISO 8601(Atom), 그리고 'YYYY-MM-DD HH:MM:SS' 처럼 시간대 없는 형식을 읽는다.
    시간대가 없으면 피드 설정의 timezone 으로 본다(예: 국내 매체는 KST)."""
    value = (value or "").strip()
    if not value:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    if dt is None:
        iso = value.replace("Z", "+00:00").replace(" ", "T", 1)
        iso = re.sub(r"\.\d+", "", iso)  # 소수점 초는 버린다 (3.9 fromisoformat 은 자릿수에 까다롭다)
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(timezone.utc)


def source_tz(source: dict) -> timezone:
    raw = source.get("timezone")
    if not raw:
        return timezone.utc
    m = re.fullmatch(r"([+-])(\d\d):?(\d\d)", raw)
    if not m:
        raise ValueError(f"{source['id']}: timezone 은 +09:00 형식이어야 합니다")
    sign = 1 if m.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


class FeedError(Exception):
    pass


def parse(body: bytes, source: dict) -> list:
    """RSS 2.0 / RSS 1.0(RDF) / Atom 을 같은 Entry 목록으로 바꾼다."""
    head = body[:400].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise FeedError("피드 대신 HTML이 왔습니다 (봇 차단 페이지나 주소 변경일 수 있음)")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise FeedError(f"XML 해석 실패: {e}")

    tz = source_tz(source)
    items = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    entries = []
    for it in items:
        title = nfkc(html_to_text(_text(_child(it, "title"))))
        link = ""
        for l in _children(it, "link"):
            href = l.get("href")
            if href and l.get("rel", "alternate") == "alternate":
                link = href
                break
            if not href and _text(l):
                link = _text(l)
                break
        if not link:
            guid = _child(it, "guid", "id")
            if guid is not None and _text(guid).startswith("http"):
                link = _text(guid)
        date_el = _child(it, "pubDate", "published", "date", "issued", "updated", "modified")
        published = parse_date(_text(date_el), tz)
        summary = html_to_text(_text(_child(it, "description", "summary", "content", "encoded")))[:500]
        cats = []
        for c in _children(it, "category") + _children(it, "subject"):
            label = c.get("term") or _text(c)
            if label:
                cats.append(label.strip())
        if title and link:
            entries.append(Entry(source["id"], title, link.strip(), published, summary, cats))
    return entries
