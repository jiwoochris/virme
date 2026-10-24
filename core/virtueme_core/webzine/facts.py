"""한 호(기본 7일)의 사실. AI 모델 없이 개수·평균·비율만 센다.

사실 한 건은 `columns._summaries` 의 요약 기록과 같은 모양(`id`·`about`·`column`·`text`·`values`·`evidence`)이라서
근거 검사기(verify.summary_facts)가 그대로 대조한다. 웹진 글이 숫자를 인용하면 이 값과 맞는지 본다.
비교 대상은 같은 사람의 앞선 4주(이전 호들)다. 앞선 기간 기록이 없으면 비교 문장은 만들지 않는다.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from .. import patterns
from ..columns import NIGHT_HOURS, PHOTO_SUFFIX, _place_key, _vals, josa, place_names, summary_id
from ..records import Record
from .match import WRITTEN_KINDS, _photo_tags

PREV_DAYS = 28
KIND_KO = {"message": "대화", "post": "게시물", "story": "스토리", "photo": "사진", "watch": "시청", "listen": "음악 재생",
           "search": "검색", "visit": "장소 방문", "payment": "결제", "health": "건강 요약", "reel": "릴스", "comment": "댓글",
           "activity": "활동"}
WEEKDAY_KO = "월화수목금토일"
KO_COUNT = {2: "두", 3: "세", 4: "네"}


def _day(r: Record) -> date:
    return r.dt.date()


def _share(n: int, d: int) -> float:
    return n / d if d else 0.0


def _pct(x: float) -> float:
    return round(100 * x, 1)


def window(records: list[Record], end: date | None, days: int) -> tuple[date, date]:
    last = max(_day(r) for r in records)
    end = min(end or last, last)
    return end - timedelta(days=days - 1), end


def _fact(out: list, about: str, column: str, subject: str, text: str, values: dict, evidence: list[str],
          section: str, headline: bool = False) -> None:
    out.append({"id": summary_id("웹진:" + about, column, subject), "about": about, "column": column, "text": text,
                "values": values, "evidence": evidence[:12], "section": section, "headline": headline})


def _amount(r: Record) -> int:
    return int(float(r.meta.get("amount") or 0))


def _who(alias: str, relations: dict[str, str | None]) -> str:
    return relations.get(alias) or "이름을 가린 한 사람"


def collect(store, end: date | None = None, days: int = 7) -> dict[str, Any]:
    records = sorted(store.records(), key=lambda r: r.ts)
    if not records:
        raise ValueError("기록이 없습니다. 먼저 기록을 가져오세요 (/import).")
    start, end = window(records, end, days)
    week = [r for r in records if start <= _day(r) <= end]
    p0 = start - timedelta(days=PREV_DAYS)
    prev = [r for r in records if p0 <= _day(r) < start]
    prev_days = len({_day(r) for r in prev}) and min(PREV_DAYS, (start - _day(records[0])).days)
    before = [r for r in records if _day(r) < start]
    relations = store.relations()
    pnames = place_names(store)
    facts: list[dict[str, Any]] = []
    ratio = days / prev_days if prev_days else None   # 앞선 기간 값을 한 호 길이로 맞추는 배율

    # ---- 분량
    kinds = Counter(r.kind for r in week)
    if week:
        top = ", ".join(f"{KIND_KO.get(k, k)} {n}건" for k, n in kinds.most_common(5))
        _fact(facts, "분량", "issue.volume", "all",
              f"{patterns.ko_date(start)}부터 {patterns.ko_date(end)}까지 남은 기록은 {len(week)}건입니다. 많은 순으로 {top}입니다.",
              _vals(numbers=[(len(week), "건")] + [(n, "건") for _, n in kinds.most_common(5)],
                    dates=[start.isoformat(), end.isoformat()], terms=[KIND_KO.get(k, k) for k, _ in kinds.most_common(5)]),
              [r.id for r in week[:12]], "data_portrait")
        by_day = Counter(_day(r) for r in week if r.kind != "health")
        if by_day:
            d, n = by_day.most_common(1)[0]
            _fact(facts, "분량", "issue.busiest", d.isoformat(),
                  f"기록이 가장 많이 남은 날은 {patterns.ko_date(d)} {WEEKDAY_KO[d.weekday()]}요일로 {n}건입니다.",
                  _vals(numbers=[(n, "건")], dates=[d.isoformat()]),
                  [r.id for r in week if _day(r) == d][:12], "data_portrait")

    # ---- 시간대
    own = [r for r in week if r.is_self and r.kind not in ("health", "visit")]
    own_prev = [r for r in prev if r.is_self and r.kind not in ("health", "visit")]
    hp = patterns.hour_profile(own)
    night = [r for r in own if r.dt.hour in NIGHT_HOURS]
    ns = _share(len(night), len(own))
    if own:
        if own_prev:
            ns0 = _share(sum(1 for r in own_prev if r.dt.hour in NIGHT_HOURS), len(own_prev))
            more = ns - ns0
            word = "늘었습니다" if more > 0.03 else "줄었습니다" if more < -0.03 else "비슷합니다"
            _fact(facts, "리듬", "issue.night", "vs_prev",
                  f"밤 11시 이후에 남긴 기록의 비율은 이번 주 {ns:.0%}, 앞선 4주 {ns0:.0%}로 {word}.",
                  _vals(percents=[_pct(ns), _pct(ns0)], times=["23:00"]), [r.id for r in night], "data_portrait",
                  headline=abs(more) >= 0.08)
        else:
            _fact(facts, "리듬", "issue.night", "week",
                  f"밤 11시 이후에 남긴 기록의 비율은 {ns:.0%}입니다.", _vals(percents=[_pct(ns)], times=["23:00"]),
                  [r.id for r in night], "data_portrait")
        if hp["center"] is not None:
            _fact(facts, "리듬", "issue.center", "week",
                  f"기록이 가장 몰린 시간대는 {patterns.band(hp['center'])}입니다.",
                  _vals(terms=[patterns.band(hp["center"])]), [r.id for r in own[:12]], "data_portrait")

    # ---- 인물
    talk = [r for r in week if r.kind == "message" and r.source in ("kakao", "sms", "instagram")]
    partner = Counter()
    ev: dict[str, list[str]] = defaultdict(list)
    for r in talk:
        others = [p for p in (r.participants or []) if p not in ("나",)] or ([r.author] if r.author and r.author != "나" else [])
        if r.author and r.author != "나":
            others = [r.author]
        for p in others:
            partner[p] += max(1, int(r.meta.get("messages") or 1))
            ev[p].append(r.id)
    seen_before = {p for r in before if r.kind == "message" for p in ([r.author] + list(r.participants or [])) if p}
    cast = []
    for alias, n in partner.most_common(4):
        who = _who(alias, relations)
        cast.append({"alias": alias, "who": who, "messages": n, "evidence": ev[alias][:12], "new": alias not in seen_before})
    # 관계가 같은 사람(대학 친구 셋)은 한 문장으로 묶는다. 가명을 글에 쓰지 않으므로 따로 쓰면 구별이 안 된다
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cast:
        groups[c["who"]].append(c)
    for who, cs in groups.items():
        n = sum(c["messages"] for c in cs)
        ids = [i for c in cs for i in c["evidence"]]
        head = f"{who} {KO_COUNT.get(len(cs), len(cs))} 명" if len(cs) > 1 else who
        _fact(facts, "인물", "issue.cast", who, f"이번 주 {josa(head, '과와')} 주고받은 메시지는 {n}개입니다.",
              _vals(numbers=[(n, "개")] + ([(len(cs), "명")] if len(cs) > 1 else []), terms=[who]), ids, "article")
    for c in cast:
        alias, who = c["alias"], c["who"]
        if alias not in seen_before and before:
            _fact(facts, "인물", "issue.cast_new", alias, f"{josa(who, '은는')} 이번 주에 처음 대화에 나타났습니다.",
                  _vals(terms=[who]), ev[alias], "article", headline=True)
    quiet = []
    if prev:
        prev_partner = Counter(p for r in prev if r.kind == "message" for p in [r.author] if p and p != "나")
        for alias, n0 in prev_partner.most_common(5):
            if alias not in partner and n0 >= 8:
                who = _who(alias, relations)
                quiet.append(who)
                _fact(facts, "인물", "issue.cast_quiet", alias,
                      f"앞선 4주에 {n0}번 말을 걸던 {josa(who, '과와')}의 대화가 이번 주에는 없습니다.",
                      _vals(numbers=[(n0, "번")], terms=[who]),
                      [r.id for r in prev if r.author == alias][-6:], "article", headline=True)

    # ---- 장소
    visits = [r for r in week if r.kind == "visit"]
    place_rows = []
    if visits:
        by_key: dict[str, list[Record]] = defaultdict(list)
        for r in visits:
            by_key[_place_key(r) or r.place or "?"].append(r)
        for key, rs in sorted(by_key.items(), key=lambda kv: -sum(int(r.meta.get("duration_min") or 0) for r in kv[1])):
            types = {(r.meta.get("place_type") or "").upper() for r in rs}
            name = "집" if "HOME" in types else "직장" if "WORK" in types else pnames.get(key.split(":", 1)[1] if ":" in key else key) \
                or next((r.place for r in rs if r.place and r.place not in ("장소", "이름 없는 장소")), None)
            mins = sum(int(r.meta.get("duration_min") or 0) for r in rs)
            place_rows.append({"key": key, "name": name or "이름 없는 장소", "named": bool(name), "days": len({_day(r) for r in rs}),
                               "minutes": mins, "home": "HOME" in types, "work": "WORK" in types,
                               "first_ever": not any(_place_key(b) == key for b in before if b.kind == "visit"),
                               "evidence": [r.id for r in rs][:12]})
        out_rows = [p for p in place_rows if not p["home"] and not p["work"]]
        if out_rows:
            p = max(out_rows, key=lambda x: (x["days"], x["minutes"]))
            label = p["name"] if p["named"] else "이름을 붙이지 않은 한 장소"
            _fact(facts, "장소", "issue.place", p["key"],
                  f"집과 직장을 빼면 가장 오래 머문 곳은 {label}입니다. {p['days']}일, 모두 {p['minutes'] // 60}시간 {p['minutes'] % 60}분을 머물렀습니다.",
                  _vals(numbers=[(p["days"], "일"), (p["minutes"] // 60, "시간")], places=[label]), p["evidence"], "documentary")
        firsts = [p for p in out_rows if p["first_ever"] and before]
        if firsts:
            _fact(facts, "장소", "issue.place_first", "week",
                  f"이번 주 처음 간 곳이 {len(firsts)}곳 있습니다.", _vals(numbers=[(len(firsts), "곳")]),
                  [i for p in firsts for i in p["evidence"][:3]], "documentary", headline=len(firsts) >= 2)

    # ---- 사진
    photos = [r for r in week if r.source == "photo" or (r.media and r.kind in ("post", "story"))]
    tagc = Counter(t for r in photos for t in _photo_tags(r))
    if photos:
        tops = [t for t, _ in tagc.most_common(3)]
        _fact(facts, "사진", "issue.photos", "week",
              f"이번 주에 찍거나 올린 사진은 {len(photos)}장입니다." + (f" 자주 찍힌 것은 {', '.join(tops)}입니다." if tops else ""),
              _vals(numbers=[(len(photos), "장")], terms=tops), [r.id for r in photos], "documentary")

    # ---- 결제
    pays = [r for r in week if r.kind == "payment" and _amount(r) > 0]
    pays_prev = [r for r in prev if r.kind == "payment" and _amount(r) > 0]
    cats = Counter()
    for r in pays:
        cats[r.meta.get("category") or "기타"] += _amount(r)
    if pays:
        total = sum(_amount(r) for r in pays)
        top_c = cats.most_common(1)[0]
        _fact(facts, "지출", "issue.spend", "week",
              f"이번 주 결제는 {len(pays)}번, 모두 {total:,}원입니다. 가장 많이 쓴 업종은 {top_c[0]}({top_c[1]:,}원)입니다.",
              _vals(numbers=[(len(pays), "번"), (total, "원"), (top_c[1], "원")], terms=[top_c[0]]),
              [r.id for r in pays], "data_portrait")
        if pays_prev and ratio:
            cats0 = Counter(r.meta.get("category") or "기타" for r in pays_prev)
            new = [c for c in cats if c not in cats0]
            if new:
                _fact(facts, "지출", "issue.spend_new", "week",
                      f"앞선 4주에 없던 결제 업종이 생겼습니다: {', '.join(new[:3])}.", _vals(terms=new[:3]),
                      [r.id for r in pays if (r.meta.get("category") or "기타") in new], "fashion" if any(
                          w in c for c in new for w in ("의류", "화장품", "뷰티", "잡화", "신발")) else "documentary",
                      headline=True)
        merch = Counter(str(r.meta.get("merchant") or "").split()[0] for r in pays if r.meta.get("merchant"))
        if merch:
            m, n = merch.most_common(1)[0]
            if n >= 3:
                _fact(facts, "지출", "issue.merchant", m, f"가장 자주 결제한 곳은 {m}로 {n}번입니다.",
                      _vals(numbers=[(n, "번")], places=[m], terms=[m]),
                      [r.id for r in pays if str(r.meta.get("merchant") or "").startswith(m)], "documentary")

    # ---- 몸
    health = [r for r in week if r.kind == "health"]
    health_prev = [r for r in prev if r.kind == "health"]
    body: dict[str, Any] = {}
    if health:
        steps = [int(r.meta["steps"]) for r in health if r.meta.get("steps")]
        sleep = [int(r.meta["sleep_min"]) for r in health if r.meta.get("sleep_min")]
        works = Counter(w.get("type") or "운동" for r in health for w in r.meta.get("workouts", []))
        km = round(sum(float(w.get("km") or 0) for r in health for w in r.meta.get("workouts", []) if w.get("type") == "달리기"), 1)
        if steps:
            avg = round(statistics.mean(steps))
            body["steps"] = avg
            prev_steps = [int(r.meta["steps"]) for r in health_prev if r.meta.get("steps")]
            if prev_steps:
                avg0 = round(statistics.mean(prev_steps))
                _fact(facts, "몸", "issue.steps", "vs_prev",
                      f"하루 평균 걸음 수는 이번 주 {avg:,}보, 앞선 4주 {avg0:,}보입니다.",
                      _vals(numbers=[(avg, "보"), (avg0, "보")]), [r.id for r in health], "data_portrait",
                      headline=avg0 > 0 and abs(avg - avg0) / avg0 >= 0.25)
            else:
                _fact(facts, "몸", "issue.steps", "week", f"하루 평균 걸음 수는 {avg:,}보입니다.",
                      _vals(numbers=[(avg, "보")]), [r.id for r in health], "data_portrait")
        if sleep:
            avg = statistics.mean(sleep)
            body["sleep_h"] = round(avg / 60, 1)
            _fact(facts, "몸", "issue.sleep", "week", f"하루 평균 수면 시간은 {avg / 60:.1f}시간입니다.",
                  _vals(numbers=[(round(avg / 60, 1), "시간")], terms=["수면", "잠"]), [r.id for r in health], "data_portrait")
        if works:
            w, n = works.most_common(1)[0]
            body["workouts"] = dict(works)
            body["run_km"] = km
            extra = f" 달린 거리는 모두 {km}km입니다." if km else ""
            _fact(facts, "몸", "issue.workout", "week", f"가장 많이 한 운동은 {w}로 {n}번입니다.{extra}",
                  _vals(numbers=[(n, "번")] + ([(km, "km")] if km else []), terms=[w]), [r.id for r in health], "data_portrait")

    # ---- 취향
    watch = [r for r in week if r.kind == "watch"]
    listen = [r for r in week if r.kind == "listen"]
    search = [r for r in week if r.kind == "search"]
    taste: dict[str, Any] = {}
    if watch:
        ch = Counter(r.meta.get("channel") or r.meta.get("series") or "?" for r in watch)
        taste["channels"] = [{"name": n, "count": c} for n, c in ch.most_common(5)]
        taste["videos"] = [r.meta.get("title") or r.text for r in watch][:8]
        c, n = ch.most_common(1)[0]
        _fact(facts, "취향", "issue.watch", c, f"이번 주 본 영상은 {len(watch)}편이고, 가장 많이 본 채널은 {c}({n}편)입니다.",
              _vals(numbers=[(len(watch), "편"), (n, "편")], terms=[c]), [r.id for r in watch], "digital_preview")
    if listen:
        ar = Counter(r.meta.get("artist") or "?" for r in listen)
        taste["artists"] = [{"name": n, "count": c} for n, c in ar.most_common(5)]
        taste["tracks"] = list(dict.fromkeys(f"{r.meta.get('artist')} - {r.meta.get('track')}" for r in listen))[:8]
        a, n = ar.most_common(1)[0]
        _fact(facts, "취향", "issue.listen", a, f"가장 많이 들은 음악가는 {a}로 {n}번 재생했습니다.",
              _vals(numbers=[(n, "번")], terms=[a]), [r.id for r in listen], "digital_preview")
    if search:
        q = Counter(r.meta.get("query") or (r.text or "").replace("검색: ", "") for r in search)
        taste["queries"] = [{"query": k, "count": c} for k, c in q.most_common(8)]
        k, n = q.most_common(1)[0]
        _fact(facts, "취향", "issue.search", k, f"가장 많이 검색한 말은 '{k}'로 {n}번입니다.",
              _vals(numbers=[(n, "번")], terms=[k]), [r.id for r in search], "digital_preview", headline=n >= 3)

    # ---- 패션 신호 (규칙표에서 fashion 으로 가는 신호에 맞은 기록)
    from .match import load_rules, matches
    fsig = [g for g in load_rules()["signals"] if g["template"] == "fashion"]
    fashion = [r for r in week if any(matches(r, g["match"]) for g in fsig)]
    if fashion:
        names = []
        for r in fashion:
            n = r.meta.get("merchant") or r.meta.get("query") or r.meta.get("title") or next(iter(_photo_tags(r)), None)
            if n and n not in names:
                names.append(str(n))
        _fact(facts, "패션", "issue.fashion", "week",
              f"옷·화장품·셀카와 관련된 기록이 이번 주 {len(fashion)}건 있습니다" + (f": {', '.join(names[:3])}." if names else "."),
              _vals(numbers=[(len(fashion), "건")], terms=names[:3]), [r.id for r in fashion], "fashion")

    # ---- 혼잣말 (시의 재료)
    mono = [r for r in week if r.is_self and r.kind in ("post", "story") and (r.text or "").strip()]
    night_written = [r for r in week if r.is_self and r.kind in WRITTEN_KINDS and r.dt.hour in NIGHT_HOURS and (r.text or "").strip()]
    hist = hp["histogram"]
    return {
        "range": [start.isoformat(), end.isoformat()], "days": days,
        "prev_range": [p0.isoformat(), (start - timedelta(days=1)).isoformat()] if prev else None,
        "records": week, "prev": prev, "facts": facts,
        "counts": dict(kinds), "hour_histogram": hist, "night_share": round(ns, 3), "time_center": hp["center"],
        "cast": cast, "quiet": quiet, "places": place_rows, "photos": photos, "photo_tags": dict(tagc.most_common(8)),
        "pays": pays, "spend_by_category": dict(cats.most_common(8)), "body": body, "taste": taste,
        "fashion": fashion, "monologue": mono, "night_written": night_written, "relations": relations,
        "daily": _daily(week, start, end),
    }


def _daily(week: list[Record], start: date, end: date) -> list[dict[str, Any]]:
    out = []
    d = start
    while d <= end:
        rs = [r for r in week if _day(r) == d]
        h = next((r for r in rs if r.kind == "health"), None)
        out.append({"date": d.isoformat(), "label": f"{d.month}.{d.day}", "weekday": WEEKDAY_KO[d.weekday()],
                    "records": sum(1 for r in rs if r.kind != "health"),
                    "messages": sum(1 for r in rs if r.kind == "message"),
                    "night": sum(1 for r in rs if r.is_self and r.dt.hour in NIGHT_HOURS and r.kind not in ("health", "visit")),
                    "spend": sum(_amount(r) for r in rs if r.kind == "payment" and _amount(r) > 0),
                    "steps": int(h.meta.get("steps") or 0) if h else None,
                    "sleep_min": int(h.meta.get("sleep_min") or 0) if h else None})
        d += timedelta(days=1)
    return out


__all__ = ["collect", "PHOTO_SUFFIX"]
