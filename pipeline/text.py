"""제목 비교와 키워드 추출.

같은 사건 판정과 교차보도 점수가 모두 제목 키워드에 기대므로, 한국어와 영어 제목이
같은 열쇠를 만들도록 하는 것이 이 파일의 일이다. (참고한 사이트는 대문자 영어 토큰만
뽑아서 한국어 기사가 교차보도 점수를 영영 받지 못했다.)
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 한국어·영어 표기를 하나의 열쇠로 모은다. 왼쪽은 소문자로 적는다.
ALIASES = {
    "오픈ai": "openai", "오픈에이아이": "openai",
    "구글": "google", "딥마인드": "deepmind", "제미나이": "gemini", "제미니": "gemini", "젬마": "gemma",
    "메타": "meta", "라마": "llama",
    "엔비디아": "nvidia",
    "앤스로픽": "anthropic", "앤트로픽": "anthropic", "클로드": "claude",
    "마이크로소프트": "microsoft", "ms": "microsoft", "코파일럿": "copilot",
    "애플": "apple", "아마존": "amazon", "aws": "amazon",
    "삼성": "samsung", "삼성전자": "samsung", "sk하이닉스": "sk hynix", "하이닉스": "sk hynix",
    "네이버": "naver", "카카오": "kakao", "lg": "lg", "엘지": "lg",
    "챗gpt": "chatgpt", "챗지피티": "chatgpt",
    "xai": "xai", "엑스ai": "xai", "그록": "grok",
    "미스트랄": "mistral", "퍼플렉시티": "perplexity", "딥시크": "deepseek", "알리바바": "alibaba", "큐원": "qwen",
    "테슬라": "tesla", "인텔": "intel", "tsmc": "tsmc", "퀄컴": "qualcomm", "브로드컴": "broadcom",
    "허깅페이스": "hugging face", "깃허브": "github", "세일즈포스": "salesforce", "오라클": "oracle",
    "소프트뱅크": "softbank", "바이두": "baidu", "텐센트": "tencent", "화웨이": "huawei",
    "유럽연합": "eu", "백악관": "white house",
}

# 어느 제목에나 나와서 같은 사건의 근거가 못 되는 말.
EN_STOP = {
    "the", "how", "why", "what", "when", "who", "new", "with", "from", "this", "that", "for", "and", "its",
    "your", "our", "after", "over", "into", "about", "says", "said", "will", "can", "now", "just", "more",
    "here", "report", "reports", "exclusive", "update", "today", "week", "first", "launches", "launch",
    "announces", "introducing", "introduces", "releases", "release", "adds", "gets", "makes", "wants",
    "llm", "llms", "gpu", "gpus", "api", "app", "apps", "model", "models", "agent", "agents", "chatbot",
    "startup", "startups", "company", "companies", "billion", "million", "use", "using", "via", "but",
    "all", "are", "you", "not", "has", "have", "was", "out", "off", "big", "top", "best", "they", "his", "her",
}
KO_STOP = {
    "인공지능", "공개", "발표", "출시", "출범", "기업", "서비스", "모델", "기술", "개발", "확대", "강화", "도입",
    "시장", "지원", "활용", "추진", "계획", "선보", "선보여", "선봬", "제공", "기반", "위한", "통해", "관련",
    "국내", "세계", "글로벌", "최대", "최초", "올해", "내년", "이번", "대해", "대한", "에이전트", "생성형",
    "업데이트", "기능", "플랫폼", "솔루션", "협력", "협약", "체결", "나선다", "나서", "본격", "가능", "예정",
    "밝혀", "밝혔다", "전망", "분석", "결과", "이유", "사용자", "사용", "데이터", "센터", "투자", "억원", "조원",
}
# 끝에 붙은 조사를 떼어 "구글은"과 "구글이"가 같은 열쇠가 되게 한다. 긴 것부터 본다.
JOSA = sorted(
    ["으로부터", "에서는", "에게서", "으로는", "이라는", "라는", "에서", "에게", "으로", "까지", "부터", "보다",
     "처럼", "마저", "조차", "이나", "이며", "하고", "은", "는", "이", "가", "을", "를", "의", "에", "와", "과",
     "도", "로", "만", "나", "며", "서"],
    key=len, reverse=True,
)
# 하이픈·점은 글자 사이에 있을 때만 토큰에 포함한다 (GPT-5, 3.5 는 하나로, "공개...브라우저"는 둘로).
TOKEN = re.compile(r"[0-9A-Za-z가-힣]+(?:[\-\.][0-9A-Za-z가-힣]+)*")
YEAR = re.compile(r"(19|20)\d\d")


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _strip_josa(token: str) -> str:
    if not re.search(r"[가-힣]$", token):
        return token
    for j in JOSA:
        if token.endswith(j) and len(token) - len(j) >= 2:
            return token[: -len(j)]
    return token


def keywords(title: str) -> set:
    """제목에서 사건을 가리킬 만한 열쇠말을 뽑는다. 결과는 소문자·표준 표기."""
    out = set()
    for raw in TOKEN.findall(nfkc(title)):
        whole = raw.strip(".-")
        # 별칭은 조사를 떼기 전에 먼저 본다. "제미나이"의 "이"를 조사로 떼면 "제미나"가 된다.
        if whole.lower() in ALIASES:
            out.add(ALIASES[whole.lower()])
            continue
        tok = _strip_josa(whole)
        low = tok.lower()
        if not low:
            continue
        if low in ALIASES:
            out.add(ALIASES[low])
            continue
        has_ko = bool(re.search(r"[가-힣]", tok))
        has_en = bool(re.search(r"[A-Za-z]", tok))
        has_digit = bool(re.search(r"\d", tok))
        if has_ko and not has_en:
            # 서술어("뒤집었다", "알려준다")와 숫자+단위("16년", "500만건")는 사건의 이름이 아니다.
            if len(tok) >= 2 and tok not in KO_STOP and not tok.endswith("다") and not has_digit:
                out.add(low)
        elif has_en:
            if low in EN_STOP:
                continue
            # 고유명사(대문자 시작 3자 이상), 약어(전부 대문자 3자 이상), 숫자 섞인 이름(GPT-5, H100)
            if (tok[0].isupper() and len(tok) >= 3) or (has_digit and len(tok) >= 2):
                out.add(low)
        elif has_digit and len(tok) >= 4 and not YEAR.fullmatch(tok):
            out.add(low)  # 연도는 같은 사건의 근거가 못 된다
    return out


def normalize_title(title: str) -> str:
    t = nfkc(title).lower()
    t = re.sub(r"[^\w가-힣]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def same_title(a: str, b: str, min_len: int = 40) -> bool:
    """정규화한 두 제목이 같거나, 한쪽이 다른 쪽의 앞부분이면 같은 글로 본다.
    피드가 긴 제목을 자르거나 다음 단계가 제목을 다듬어도 같은 글로 잡히게 하려는 것이다.
    짧은 제목끼리 앞부분만 우연히 겹치는 경우는 min_len 으로 막는다."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    return len(short) >= min_len and long_.startswith(short)


TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|ref_src$|mc_|cmpid$|sr_share$)")


def normalize_link(url: str) -> str:
    """추적용 쿼리와 끝 슬래시를 떼고, 호스트는 소문자·www 없이 비교한다."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|header|footer|form)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()
