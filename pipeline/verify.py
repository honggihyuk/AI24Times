"""원문 확인.

고른 기사의 원문 페이지를 실제로 열어 본문이 읽히는지 본다. 읽히지 않으면(차단, 유료벽, 빈 페이지)
그 기사는 빼고 예비 후보로 바꾼다. RSS에 적힌 짧은 소개만 보고 요약하지 않기 위한 단계다.

여기서는 "읽을 수 있는가"만 판단하고 본문은 저장하지 않는다.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request

from .feeds import USER_AGENT
from .text import html_to_text

MIN_PARAGRAPH_CHARS = 600   # <p> 문단 글자 수 합이 이보다 적으면 본문을 못 읽은 것으로 본다
TIMEOUT = 20
MAX_BYTES = 4_000_000


def paragraph_chars(html: str) -> int:
    """메뉴·광고를 빼고 본문 문단(<p>)에 든 글자 수만 센다."""
    html = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", html)
    paras = re.findall(r"(?is)<p(?:\s[^>]*)?>(.*?)</p>", html)
    return sum(len(html_to_text(p)) for p in paras)


def check_original(url: str, min_chars: int = MIN_PARAGRAPH_CHARS, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype:
                return {"ok": False, "reason": "not_html", "http_status": r.status, "chars": 0}
            charset = r.headers.get_content_charset() or "utf-8"
            html = r.read(MAX_BYTES).decode(charset, "replace")
            chars = paragraph_chars(html)
            ok = chars >= min_chars
            return {"ok": ok, "reason": "" if ok else "too_short", "http_status": r.status, "chars": chars}
    except urllib.error.HTTPError as e:
        return {"ok": False, "reason": f"http_{e.code}", "http_status": e.code, "chars": 0}
    except Exception as e:
        return {"ok": False, "reason": type(e).__name__, "http_status": None, "chars": 0}
