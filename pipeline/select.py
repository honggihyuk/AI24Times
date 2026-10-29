"""거르기 → 점수 → 같은 사건 묶기 → 고르기.

네트워크를 쓰지 않는 순수 계산이라 같은 입력이면 언제 돌려도 같은 결과가 나온다.
(나중에 "그날 왜 이렇게 골랐나"를 재현할 수 있어야 한다.)

점수 = 출처 가중치 + 교차보도 가점(다른 출처 1곳당 1.0, 최대 3.0) + 신선도(최대 1.0)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .feeds import Entry
from .text import keywords, normalize_link, same_title

COVERAGE_PER_SOURCE = 1.0
COVERAGE_MAX = 3.0
FRESHNESS_MAX = 1.0
RARE_MIN = 3           # "드문 말": 그날 이 수 이하의 글에만 나온 키워드 ...
RARE_SHARE = 0.05      # ... 또는 그날 글 수의 5% 이하 (둘 중 큰 값)
FUTURE_SKEW = timedelta(minutes=30)  # 피드 시계가 조금 빠른 것은 봐준다

AI_TERMS = re.compile(
    r"(?i)\b(ai|a\.i\.|artificial intelligence|machine learning|llm|gpt|chatbot|neural|deep learning|"
    r"openai|anthropic|deepmind|gemini|claude|copilot|nvidia|agentic|agents?)\b|인공지능|생성형|에이전트|챗봇|머신러닝|딥러닝|LLM|GPU"
)
# 피드가 직접 붙여 주는 후원·광고 표시. 출처와 상관없이 홍보로 본다.
# (MIT Technology Review 피드는 2026-10-29 기준 10건 중 7건이 'sponsored' 였다.)
PROMO_CATEGORIES = {"sponsored", "sponsor", "sponsored content", "partner content", "advertorial",
                    "promoted", "paid post", "paid content", "광고", "후원", "협찬"}
PROMO = re.compile(
    r"(?i)\b(sponsored|webinar|partner content|advertorial|promo code|giveaway|deals? of the|% off|"
    r"coupon|discount)\b|\[(광고|후원|AD|PR)\]|웨비나|할인|이벤트 참가|무료 체험|참가자 모집|수강생 모집|사전 등록"
)


@dataclass(eq=False)  # 같은 내용이어도 서로 다른 후보다. 비교는 객체 자체로 한다.
class Item:
    """후보 하나. 거르기 단계를 지나며 drop 이유가 붙거나 점수가 매겨진다."""
    entry: Entry
    source: dict
    keys: set = field(default_factory=set)
    drop: str = ""            # 거른 이유 (비어 있으면 아직 살아 있음)
    weight: float = 0.0
    coverage: float = 0.0
    freshness: float = 0.0
    coverage_sources: list = field(default_factory=list)
    cluster: int = -1
    related: list = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(self.weight + self.coverage + self.freshness, 2)

    @property
    def sid(self) -> str:
        return self.source["id"]


# ---------------------------------------------------------------- 거르기


def apply_filters(items: list, now: datetime, lookback_hours: int, history: dict, warnings: list) -> None:
    """살아남지 못한 항목에는 drop 에 이유를 적는다. 순서가 곧 우선순위다."""
    start = now - timedelta(hours=lookback_hours)
    for it in items:
        pub = it.entry.published
        if pub is None:
            it.drop = "no_date"   # 날짜 없는 글을 "새 글"로 보면 몇 달 전 글이 오늘 소식이 된다
        elif pub < start or pub > now + FUTURE_SKEW:
            it.drop = "outside_lookback"

    in_window = [it for it in items if not it.drop]
    by_source = defaultdict(list)
    for it in in_window:
        by_source[it.sid].append(it)

    for sid, group in by_source.items():
        src = group[0].source
        excluded = set(src.get("exclude_categories", []))
        dropped = 0
        for it in group:
            cats = it.entry.categories
            if (not cats and src.get("drop_uncategorized")) or excluded.intersection(cats):
                it.drop = "not_news"
                dropped += 1
        # 한 피드의 글이 전부 걸러졌다면 그날 전부 홍보 글이었다기보다 피드가 분류를 안 주기 시작했을 가능성이 크다.
        if dropped and dropped == len(group) and len(group) >= 3:
            warnings.append(f"{src['name']}: 지난 {lookback_hours}시간 글 {len(group)}건이 모두 '뉴스 아님'으로 걸러졌습니다. 피드의 분류가 바뀌었는지 확인하세요.")

    for it in in_window:
        if it.drop:
            continue
        text = f"{it.entry.title} {it.entry.summary}"
        if it.source.get("require_ai_terms") and not AI_TERMS.search(text):
            it.drop = "off_topic"
        elif PROMO.search(it.entry.title) or PROMO_CATEGORIES.intersection(c.lower() for c in it.entry.categories):
            it.drop = "promo"
        elif _already_published(it, history):
            it.drop = "already_published"


def _already_published(it: Item, history: dict) -> bool:
    if normalize_link(it.entry.link) in history["links"]:
        return True
    return any(same_title(it.entry.title, t) for t in history["titles"])


def dedupe_links(items: list) -> None:
    """여러 피드가 같은 주소를 실었으면 가중치가 높은 출처 하나만 남긴다."""
    best = {}
    for it in items:
        if it.drop or it.source.get("role") == "signal":
            continue
        key = normalize_link(it.entry.link)
        keep = best.get(key)
        if keep is None:
            best[key] = it
        elif it.source["weight"] > keep.source["weight"]:
            keep.drop = "duplicate_link"
            best[key] = it
        else:
            it.drop = "duplicate_link"


# ---------------------------------------------------------------- 점수


@dataclass
class Vocab:
    """그날 글 전체에서 각 키워드가 몇 번 나왔는지. 흔한 말과 드문 말을 가른다."""
    df: Counter
    rare_df: int

    @classmethod
    def build(cls, window: list) -> "Vocab":
        df = Counter(k for it in window for k in it.keys)
        return cls(df, max(RARE_MIN, round(len(window) * RARE_SHARE)))

    def match(self, a: set, b: set) -> list:
        """두 제목이 같은 사건을 가리키면 겹친 키워드를, 아니면 빈 목록을 돌려준다.
        '구글'·'제미나이'처럼 그날 여러 기사에 나온 말만 겹쳐서는 같은 사건이 아니다.
        그날 드문 말(예: 'carbon', 'nemotron')이 하나 이상 겹치고, 겹친 말이 둘 이상이거나
        드문 말이 둘 이상 겹칠 때만 같은 사건으로 본다."""
        shared = a & b
        rare = [k for k in shared if self.df[k] <= self.rare_df]
        if rare and (len(shared) >= 2 or len(rare) >= 2):
            return sorted(shared)
        return []


def score(candidates: list, window: list, now: datetime, lookback_hours: int) -> Vocab:
    """candidates 에 점수를 매긴다. window 는 교차보도를 셀 때 볼 그날의 모든 글(신호용 출처 포함)."""
    vocab = Vocab.build(window)
    links_by_source = defaultdict(set)
    for it in window:
        links_by_source[it.sid].add(normalize_link(it.entry.link))

    for it in candidates:
        it.weight = float(it.source["weight"])
        others = set()
        my_link = normalize_link(it.entry.link)
        for other in window:
            if other.sid == it.sid or other.sid in others:
                continue
            if my_link in links_by_source[other.sid] or vocab.match(it.keys, other.keys):
                others.add(other.sid)
        it.coverage_sources = sorted(others)
        it.coverage = min(COVERAGE_MAX, COVERAGE_PER_SOURCE * len(others))
        age_h = max(0.0, (now - it.entry.published).total_seconds() / 3600)
        it.freshness = round(max(0.0, FRESHNESS_MAX * (1 - age_h / lookback_hours)), 2)
    return vocab


def cluster(candidates: list, vocab: Vocab) -> None:
    """같은 사건을 다룬 후보끼리 묶는다.
    점수가 높은 순으로 훑으며, 기존 묶음의 대표(첫 기사)와 직접 같은 사건일 때만 그 묶음에 넣는다.
    A~B, B~C 가 비슷하다고 A~C 까지 엮이는 연쇄를 막으려는 것이다(그러면 하루치 구글 기사가 한 묶음이 된다)."""
    heads = []   # [(대표, 묶음 번호, 구성원)]
    for it in sorted(candidates, key=lambda c: -c.score):
        link = normalize_link(it.entry.link)
        for head, cid, members in heads:
            if normalize_link(head.entry.link) == link or vocab.match(head.keys, it.keys):
                it.cluster = cid
                members.append(it)
                break
        else:
            it.cluster = len(heads)
            heads.append((it, it.cluster, [it]))
    for _, _, members in heads:
        for it in members:
            it.related = [m for m in members if m is not it]


# ---------------------------------------------------------------- 고르기


def pick(candidates: list, top_n: int, cap: int, exclude: frozenset = frozenset()) -> tuple:
    """점수순으로 훑어 한 매체 cap 건, 같은 사건 한 건 규칙으로 top_n 건을 고른다.
    못 채우면 상한을 한 칸씩 올린다(통째로 풀지 않는다). 같은 사건 규칙은 풀지 않는다.
    돌려주는 값: (고른 목록, 실제로 쓴 상한)"""
    ranked = sorted((c for c in candidates if id(c) not in exclude), key=lambda c: -c.score)
    used_cap = cap
    while True:
        chosen, per_source, clusters = [], Counter(), set()
        for c in ranked:
            if len(chosen) >= top_n:
                break
            if c.cluster in clusters or per_source[c.sid] >= used_cap:
                continue
            chosen.append(c)
            per_source[c.sid] += 1
            clusters.add(c.cluster)
        if len(chosen) >= top_n or used_cap >= top_n:
            return chosen, used_cap
        used_cap += 1


def explain_drops(candidates: list, chosen: list, cap: int) -> dict:
    """뽑히지 않은 후보마다 이유를 붙인다: 같은 사건 / 매체 상한 / 순위 밖."""
    chosen_ids = {id(c) for c in chosen}
    used_clusters = {c.cluster for c in chosen}
    per_source = Counter(c.sid for c in chosen)
    reasons = {}
    for c in candidates:
        if id(c) in chosen_ids:
            continue
        if c.cluster in used_clusters:
            reasons[id(c)] = "same_event"
        elif per_source[c.sid] >= cap:
            reasons[id(c)] = "source_cap"
        else:
            reasons[id(c)] = "rank"
    return reasons


def reserve(candidates: list, chosen: list, n: int, cap: int) -> list:
    """원문 확인에서 빠지는 기사가 생기면 대신 들어갈 예비 후보. 같은 규칙으로 이어서 고른 순서."""
    per_source = Counter(c.sid for c in chosen)
    clusters = {c.cluster for c in chosen}
    out = []
    for c in sorted(candidates, key=lambda c: -c.score):
        if len(out) >= n:
            break
        if c in chosen or c.cluster in clusters or per_source[c.sid] >= cap:
            continue
        out.append(c)
        clusters.add(c.cluster)
    return out
