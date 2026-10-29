"""하루치 후보를 모아 고르고 data/candidates/YYYY-MM-DD.json 에 남긴다.

    python -m pipeline.collect                       # 지금 시각 기준, 원문 확인 포함
    python -m pipeline.collect --no-verify           # 원문 확인 없이 (빠름)
    python -m pipeline.collect --save-raw            # 받은 피드를 data/raw/날짜/ 에 저장
    python -m pipeline.collect --from-raw data/raw/2026-10-29 --now 2026-10-29T08:00+09:00
                                                     # 저장해 둔 피드로 그날 실행을 재현
    python -m pipeline.collect --dry-run             # 파일은 쓰지 않고 요약만 출력

흐름: 피드 수집 → 24시간 안 글만 → 뉴스 아님·홍보·이미 실은 글·같은 주소 제외
     → 점수(출처 가중치 + 교차보도 + 신선도) → 같은 사건 묶기 → 매체 상한을 지키며 10건
     → 원문 확인, 못 읽으면 예비 후보로 교체 → 단계별 건수와 함께 저장
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import feeds
from .feeds import FeedError, FetchResult
from .select import Item, apply_filters, cluster, dedupe_links, explain_drops, pick, reserve, score
from .text import keywords, normalize_link
from .verify import check_original

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = 1
MAX_VERIFY_ROUNDS = 4       # 원문 확인 실패 → 교체를 반복하는 최대 횟수
SAMPLE_LIMIT = 60           # 기록에 남길 걸러진 글 예시 수


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- 설정·기록 읽기


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for s in cfg.get("sources", []):
        for key in ("id", "name", "url", "weight"):
            if key not in s:
                raise ConfigError(f"{path.name}: 출처 {s.get('id', '?')} 에 {key} 가 없습니다")
        if s["id"] in seen:
            raise ConfigError(f"{path.name}: 출처 id {s['id']} 가 두 번 나옵니다")
        seen.add(s["id"])
        s.setdefault("role", "news")
        s.setdefault("pool", "main")
        s.setdefault("enabled", True)
        if s["role"] not in ("news", "signal"):
            raise ConfigError(f"{path.name}: {s['id']} 의 role 은 news 또는 signal 이어야 합니다")
        if s["pool"] not in ("main", "korea"):
            raise ConfigError(f"{path.name}: {s['id']} 의 pool 은 main 또는 korea 여야 합니다")
        feeds.source_tz(s)  # 형식 검사
    return cfg


def load_history(data_dir: Path, before: str) -> dict:
    """이미 실은 글. 발행된 브리핑과, 오늘 이전 날짜의 후보 파일에서 고른 글을 모은다."""
    links, titles = set(), []
    for path in sorted((data_dir / "briefings").glob("*.json")):
        if path.stem >= before:
            continue
        b = json.loads(path.read_text(encoding="utf-8"))
        for a in b.get("articles", []):
            links.add(normalize_link(a.get("url", "")))
            titles += [a[k] for k in ("title", "title_original", "title_en") if a.get(k)]
    for path in sorted((data_dir / "candidates").glob("*.json")):
        if path.stem >= before:
            continue
        c = json.loads(path.read_text(encoding="utf-8"))
        for a in c.get("selected", []) + c.get("korea", []):
            links.add(normalize_link(a["link"]))
            titles.append(a["title"])
    return {"links": links, "titles": titles}


# ---------------------------------------------------------------- 피드 수집


def fetch_all(sources: list, fetcher: Callable = feeds.fetch, workers: int = 6) -> dict:
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(fetcher, sources))
    return {r.source_id: r for r in results}


def save_raw(results: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    meta = {}
    for sid, r in results.items():
        meta[sid] = {"ok": r.ok, "http_status": r.http_status, "error": r.error}
        if r.ok:
            (folder / f"{sid}.xml").write_bytes(r.body)
    (folder / "fetch.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_raw(folder: Path, sources: list) -> dict:
    """저장해 둔 피드로 그날 실행을 재현한다. 그날 실패했던 피드는 실패한 그대로 재현된다."""
    meta = json.loads((folder / "fetch.json").read_text(encoding="utf-8"))
    out = {}
    for s in sources:
        m = meta.get(s["id"])
        if m is None:
            out[s["id"]] = FetchResult(s["id"], False, error="저장된 피드가 없음")
        elif m["ok"]:
            out[s["id"]] = FetchResult(s["id"], True, (folder / f"{s['id']}.xml").read_bytes(), m["http_status"])
        else:
            out[s["id"]] = FetchResult(s["id"], False, http_status=m["http_status"], error=m["error"])
    return out


# ---------------------------------------------------------------- 고르기 + 원문 확인


def select_verified(pool: list, top_n: int, cap: int, verify: Optional[Callable], warnings: list, label: str) -> dict:
    """점수순으로 고르고, 원문을 확인해 못 읽은 기사는 빼고 다시 고른다."""
    chosen, cap_used = pick(pool, top_n, cap)
    checked, failed, substitutions = {}, {}, []
    if verify:
        for _ in range(MAX_VERIFY_ROUNDS):
            todo = [c for c in chosen if id(c) not in checked]
            if not todo:
                break
            with ThreadPoolExecutor(4) as ex:
                for c, res in zip(todo, ex.map(lambda c: verify(c.entry.link), todo)):
                    checked[id(c)] = res
            bad = [c for c in chosen if not checked[id(c)]["ok"]]
            if not bad:
                break
            for c in bad:
                failed[id(c)] = checked[id(c)]["reason"]
            before = {id(c) for c in chosen}
            chosen, cap_used = pick(pool, top_n, cap, exclude=frozenset(failed))
            added = [c for c in chosen if id(c) not in before]
            for i, c in enumerate(bad):
                substitutions.append({"removed": brief(c), "reason": failed[id(c)],
                                      "added": brief(added[i]) if i < len(added) else None})
        else:
            warnings.append(f"{label}: 원문 확인 실패가 이어져 {MAX_VERIFY_ROUNDS}번 교체 후 멈췄습니다. 확인 안 된 기사가 남아 있을 수 있습니다.")
    if len(chosen) < top_n:
        warnings.append(f"{label}: 규칙을 지키며 고를 수 있는 기사가 {len(chosen)}건뿐이라 {top_n}건을 채우지 못했습니다.")
    return {"chosen": chosen, "cap_used": cap_used, "checked": checked, "failed": failed, "substitutions": substitutions}


# ---------------------------------------------------------------- 기록 형식


def iso_kst(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(KST).isoformat(timespec="minutes") if dt else None


def brief(c: Item) -> dict:
    return {"title": c.entry.title, "link": c.entry.link, "source": c.source["name"], "source_id": c.sid}


def describe(c: Item, rank: Optional[int] = None, checked: Optional[dict] = None, names: Optional[dict] = None) -> dict:
    d = {}
    if rank is not None:
        d["rank"] = rank
    d.update({
        "title": c.entry.title,
        "link": c.entry.link,
        "source": c.source["name"],
        "source_id": c.sid,
        "kind": c.source.get("kind", "media"),
        "lang": c.source.get("lang", "en"),
        "published": iso_kst(c.entry.published),
        "score": c.score,
        "score_parts": {"weight": c.weight, "coverage": c.coverage, "freshness": c.freshness},
        "coverage_sources": [names.get(s, s) for s in c.coverage_sources] if names else c.coverage_sources,
        "keywords": sorted(c.keys),
        "categories": c.entry.categories,
        "related": [brief(r) for r in c.related],
    })
    if checked and id(c) in checked:
        d["original"] = checked[id(c)]
    return d


# ---------------------------------------------------------------- 실행


def run(cfg: dict, fetched: dict, now: datetime, history: dict, verify: Optional[Callable] = None) -> dict:
    lookback = cfg.get("lookback_hours", 24)
    top_n, cap = cfg.get("top_n", 10), cfg.get("per_source_cap", 3)
    warnings: list = []
    names = {s["id"]: s["name"] for s in cfg["sources"]}
    rows, items = {}, []

    for s in cfg["sources"]:
        row = {"id": s["id"], "name": s["name"], "role": s["role"], "pool": s["pool"],
               "status": "ok", "http_status": None, "error": "", "entries": 0,
               "in_lookback": 0, "candidates": 0, "selected": 0}
        rows[s["id"]] = row
        if not s["enabled"]:
            row["status"] = "disabled"
            row["error"] = s.get("note", "")
            continue
        r = fetched.get(s["id"]) or FetchResult(s["id"], False, error="가져오지 않음")
        row["http_status"] = r.http_status
        if not r.ok:
            row.update(status="failed", error=r.error)
            continue
        try:
            entries = feeds.parse(r.body, s)
        except FeedError as e:
            row.update(status="failed", error=str(e))
            continue
        row["entries"] = len(entries)
        if not entries:
            row["status"] = "empty"
            warnings.append(f"{s['name']}: 피드는 응답했지만 글이 0건입니다.")
        items += [Item(e, s, keys=keywords(e.title)) for e in entries]

    apply_filters(items, now, lookback, history, warnings)
    in_window = [it for it in items if it.drop not in ("no_date", "outside_lookback")]
    # 교차보도를 셀 때는 그날 나온 글을 모두 본다. 다만 홍보·뉴스 아님으로 걸러진 글은 보도로 치지 않는다.
    coverage_window = [it for it in in_window if it.drop not in ("not_news", "promo", "off_topic")]
    dedupe_links(items)
    for it in items:
        if not it.drop and it.source["role"] == "signal":
            it.drop = "signal_only"   # 싣지 않고 교차보도 신호로만 쓰는 출처
    candidates = [it for it in items if not it.drop]
    main = [c for c in candidates if c.source["pool"] == "main"]
    korea = [c for c in candidates if c.source["pool"] == "korea"]

    vocab = score(candidates, coverage_window, now, lookback)
    cluster(main, vocab)
    cluster(korea, vocab)

    m = select_verified(main, top_n, cap, verify, warnings, "메인")
    k = select_verified(korea, cfg.get("korea_n", 3), cfg.get("korea_per_source_cap", 2), verify, warnings, "국내")
    if m["cap_used"] > cap:
        warnings.append(f"메인: 10건을 채우려고 한 매체 상한을 {cap}건에서 {m['cap_used']}건으로 올렸습니다.")

    main_ok = [c for c in main if id(c) not in m["failed"]]
    reasons = explain_drops(main_ok, m["chosen"], m["cap_used"])
    for c in main:
        if id(c) in m["failed"]:
            reasons[id(c)] = "original_unreadable"
    spare = reserve(main_ok, m["chosen"], cfg.get("reserve_n", 5), m["cap_used"])

    for it in in_window:
        rows[it.sid]["in_lookback"] += 1
    for c in candidates:
        rows[c.sid]["candidates"] += 1
    for c in m["chosen"] + k["chosen"]:
        rows[c.sid]["selected"] += 1
    failed_feeds = [r for r in rows.values() if r["status"] == "failed"]
    for r in failed_feeds:
        warnings.insert(0, f"{r['name']}: 피드 실패 - {r['error']}")

    chosen = sorted(m["chosen"], key=lambda c: -c.score)
    korea_chosen = sorted(k["chosen"], key=lambda c: -c.score)
    dropped = Counter(it.drop for it in items if it.drop)
    after = Counter(reasons.values())

    return {
        "schema_version": SCHEMA_VERSION,
        "date": now.astimezone(KST).date().isoformat(),
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "lookback": {"from": iso_kst(now - timedelta(hours=lookback)), "to": iso_kst(now), "hours": lookback},
        "settings": {"top_n": top_n, "per_source_cap": cap, "reserve_n": cfg.get("reserve_n", 5),
                     "korea_n": cfg.get("korea_n", 3), "verify": bool(verify)},
        "funnel": {
            "feeds_total": sum(1 for s in cfg["sources"] if s["enabled"]),
            "feeds_failed": len(failed_feeds),
            "feeds_disabled": sum(1 for s in cfg["sources"] if not s["enabled"]),
            "entries_seen": len(items),
            "in_lookback": len(in_window),
            "dropped": {key: dropped[key] for key in
                        ("no_date", "outside_lookback", "not_news", "off_topic", "promo",
                         "already_published", "duplicate_link", "signal_only") if dropped[key]},
            "main_candidates": len(main),
            "korea_candidates": len(korea),
            "selected": len(chosen),
            "korea_selected": len(korea_chosen),
            "after_selection": {key: after[key] for key in ("rank", "same_event", "source_cap", "original_unreadable")},
            "source_cap_used": m["cap_used"] if m["cap_used"] > cap else None,
            "substitutions": len(m["substitutions"]),
        },
        "sources": list(rows.values()),
        "warnings": warnings,
        "selected": [describe(c, i, m["checked"], names) for i, c in enumerate(chosen, 1)],
        "reserve": [describe(c, None, None, names) for c in spare],
        "korea": [describe(c, i, k["checked"], names) for i, c in enumerate(korea_chosen, 1)],
        "substitutions": m["substitutions"] + k["substitutions"],
        "not_selected": [
            {"title": c.entry.title, "source": c.source["name"], "score": c.score, "reason": reasons[id(c)]}
            for c in sorted(main, key=lambda c: -c.score) if id(c) in reasons
        ],
        "filtered_samples": [
            {"reason": it.drop, "source": it.source["name"], "title": it.entry.title}
            for it in in_window if it.drop in ("not_news", "promo", "off_topic", "already_published")
        ][:SAMPLE_LIMIT],
    }


def summary(result: dict) -> str:
    f = result["funnel"]
    lines = [
        f"{result['date']} · 피드 {f['feeds_total']}곳(실패 {f['feeds_failed']}) · 항목 {f['entries_seen']} → "
        f"{result['lookback']['hours']}시간 안 {f['in_lookback']} → 메인 후보 {f['main_candidates']} → 선정 {f['selected']}"
        f" (국내 후보 {f['korea_candidates']} → {f['korea_selected']})",
        f"걸러진 이유: {f['dropped']}",
        f"선정 후 남은 후보: {f['after_selection']} · 원문 확인으로 교체 {f['substitutions']}건",
    ]
    for a in result["selected"]:
        p = a["score_parts"]
        lines.append(f"  {a['rank']:>2}. [{a['score']:.2f} = {p['weight']}+{p['coverage']}+{p['freshness']}] "
                     f"{a['source']} · {a['title'][:70]}")
    for w in result["warnings"]:
        lines.append(f"  ! {w}")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="AI24Times 하루치 기사 후보 수집·선정")
    ap.add_argument("--now", help="기준 시각 (예: 2026-10-29T08:00+09:00). 기본은 지금")
    ap.add_argument("--config", default=str(DATA_DIR / "sources.json"))
    ap.add_argument("--out", default=str(DATA_DIR / "candidates"), help="결과 폴더")
    ap.add_argument("--no-verify", action="store_true", help="원문 확인을 건너뜀")
    ap.add_argument("--save-raw", action="store_true", help="받은 피드를 data/raw/날짜/ 에 저장")
    ap.add_argument("--from-raw", help="저장해 둔 피드 폴더로 재현 (네트워크 안 씀)")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 요약만 출력")
    args = ap.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    date = now.astimezone(KST).date().isoformat()
    try:
        cfg = load_config(Path(args.config))
    except (ConfigError, ValueError, json.JSONDecodeError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 1
    enabled = [s for s in cfg["sources"] if s["enabled"]]

    if args.from_raw:
        fetched = load_raw(Path(args.from_raw), enabled)
    else:
        fetched = fetch_all(enabled)
        if args.save_raw:
            save_raw(fetched, DATA_DIR / "raw" / date)

    history = load_history(DATA_DIR, date)
    result = run(cfg, fetched, now, history, verify=None if args.no_verify else check_original)
    print(summary(result))

    if not args.dry_run:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{date}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
