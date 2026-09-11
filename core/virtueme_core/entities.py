"""개체·관계 추출과 지식그래프, 그리고 "스스로 말한 나"와 기록 사이의 간극.

개체·관계
- 가려진 기록 텍스트를 30건씩 LLM 에 보내 사람·장소·활동·음식·물건·동물·일·단체와 "나 —함께함→ 지인D" 같은 관계를 받는다.
- 그래프는 networkx 로 만든다. 노드 = (종류, 이름), 간선 = 같은 기록에 함께 나온 횟수 + 관계 동사.
- 그래프를 만들기 전에 이름을 합친다: 제주도→제주, 우리집→집, 우리 크루·크루→러닝 크루, 지인D씨→지인D.
  규칙으로 못 잡는 다른 낱말(뜀·달리기→러닝)은 빌드 때 LLM 에 한 번 물어 동의어 표를 만들어 둔다(synonyms).
  대명사("우리", "너희")와 가린 표기("누군가")는 사람 노드에서 뺀다. 같은 이름이 종류만 다르게 잡히면(회사: 장소·단체)
  더 많이 나온 종류로 모은다.
- 노드마다 처음·마지막 날짜, 언급된 기록의 평균 정서가를 붙인다.

간극
- 본인 기록에서 어떤 활동을 하지 않는다·자주 한다고 스스로 단정하는 말("요즘 운동할 시간도 없어")을 LLM 이 고른다.
  실력("아직 초보예요")이나 그날 상태("몸이 따라오질 않네")는 기록 수와 비교할 수 없으므로 뺀다.
- 그 말의 주제 낱말이 다른 기록 두 건 이상에 실제로 나올 때만, 키워드 검색으로 다시 찾아 그 말 **뒤**의 기록 수와 비교한다.
- 문장은 템플릿으로 만든다. LLM 이 수치를 지어낼 틈을 두지 않기 위해서다.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import patterns
from .config import JUDGE_MODEL, LLM_MODEL, openrouter_key
from .llm import chat_json
from .privacy import Masker
from .query import QueryEngine
from .store import Store

TYPES = ("person", "place", "activity", "food", "object", "animal", "event", "org")
TYPE_KO = {"person": "사람", "place": "장소", "activity": "활동", "food": "음식", "object": "물건", "animal": "동물",
           "event": "일", "org": "단체"}

ENT_SYSTEM = """너는 개인 기록에서 개체와 관계를 뽑는다. 기록마다 아래를 준다.
- entities: [{"t":"person|place|activity|food|object|animal|event|org","n":"이름"}]
  기록에 직접 나온 것만. 사람은 기록 표기 그대로(나, 지인A, 엄마). 장소는 고유한 곳(한강, 제주)과 집·회사 같은 생활 공간.
  활동은 명사형(러닝, 야근, 산책). 반려동물 이름은 animal. 기록 주인은 "나".
- relations: [{"s":"주어","p":"동사","o":"대상"}] 기록에 드러난 것만. p 는 함께함, 다녀옴, 좋아함, 싫어함, 걱정함, 고마워함,
  돌봄, 먹음, 함, 만남 가운데 하나.
개체가 없으면 빈 목록. JSON 하나만 출력한다: {"items":[{"i":번호,"entities":[],"relations":[]}]}"""
ENT_VERSION = "ent1"

CLAIM_SYSTEM = """너는 기록 주인이 자기 습관을 어떻게 말하는지 찾는다. 기록 가운데 평소에 어떤 활동을 하지 않는다·할 시간이 없다·싫어한다,
또는 자주 한다·매일 한다·좋아한다고 스스로 단정하는 문장을 고른다.
고른 문장마다 kind 를 붙인다.
- habit: 평소 습관·빈도·취향을 단정 ("요즘 ~할 시간이 없다", "원래 ~ 안 한다", "요즘 ~ 많이 한다")
- event: 그날 한 일이나 하고 있는 일
- plan: 앞으로 할 일, wish: 하고 싶은 일
- skill: 실력·수준 ("아직 초보예요"), state: 몸 상태·기분 ("오늘 피곤해")
항목마다: {"i":번호,"kind":"habit|event|plan|wish|skill|state","claim":"짧은 요약","topic":"그 활동을 가리키는 구체적인 명사 한 낱말","polarity":-1 또는 1}
topic 은 기록에 나올 법한 활동·대상 이름으로 쓴다(운동, 요리, 독서처럼). 활동, 신체, 생각, 일상, 시간, 지식 같은 넓은 낱말은 쓰지 마라.
polarity 는 topic 을 기준으로 정한다. topic 을 하지 않음·못함·싫어함이면 -1 ("운동할 시간이 없다" → topic 운동, -1),
topic 을 자주 함·좋아함이면 1 ("요즘 야근이 많다" → topic 야근, 1). 남에 대한 말은 고르지 않는다.
JSON 하나만 출력한다: {"claims":[]}"""
CLAIM_VERSION = "claim5"
BROAD_TOPICS = {"활동", "신체", "생각", "일상", "시간", "지식", "상태", "기분", "감정", "컨디션", "생활", "일", "것"}


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def extract(store: Store, model: str = LLM_MODEL, batch: int = 30) -> dict[str, Any]:
    """개체·관계가 없는 기록을 뽑아 저장한다. 같은 문장은 캐시에서 다시 쓴다."""
    if not openrouter_key():
        return {"skipped": "OPENROUTER_API_KEY 없음"}
    masker = Masker.from_store(store)
    done = store.entity_done()
    todo = [(r, masker.mask(r.text) or "") for r in store.records() if r.text and r.id not in done]
    hits = 0
    pending = []
    for r, t in todo:
        c = store.cache_get(f"{ENT_VERSION}:{model}:{_hash(t)}")
        if c is not None:
            store.put_entities(r.id, [tuple(x) for x in c["e"]], [tuple(x) for x in c["r"]])
            store.cache_put(f"entdone:{r.id}", 1)
            hits += 1
        else:
            pending.append((r, t))

    def run(chunk):
        lines = "\n".join(f"{i}. {t[:200].replace(chr(10), ' / ')}" for i, (_, t) in enumerate(chunk))
        data = chat_json(ENT_SYSTEM, f"기록 {len(chunk)}건:\n{lines}", model=model, max_tokens=120 * len(chunk) + 300)
        out = {}
        for item in data.get("items", []) if isinstance(data, dict) else []:
            i = int(item.get("i", -1))
            if not 0 <= i < len(chunk):
                continue
            ents = [(e.get("t"), str(e.get("n", "")).strip()) for e in item.get("entities", [])
                    if e.get("t") in TYPES and str(e.get("n", "")).strip()]
            rels = [(str(x.get("s", "")).strip(), str(x.get("p", "")).strip(), str(x.get("o", "")).strip())
                    for x in item.get("relations", []) if x.get("s") and x.get("p") and x.get("o")]
            out[i] = (ents, rels)
        return chunk, out

    chunks = [pending[k : k + batch] for k in range(0, len(pending), batch)]
    n_done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for chunk, out in pool.map(run, chunks):
            for i, (r, t) in enumerate(chunk):
                ents, rels = out.get(i, ([], []))
                store.put_entities(r.id, ents, rels)
                store.cache_put(f"{ENT_VERSION}:{model}:{_hash(t)}", {"e": ents, "r": rels})
                store.cache_put(f"entdone:{r.id}", 1)
                n_done += 1
    return {"extracted": n_done, "cached": hits}


NOT_PEOPLE = {"우리", "너희", "저", "저희", "제", "너", "니", "누군가", "사람", "사람들", "나이", "여러분", "다들", "모두", "본인"}


SYN_SYSTEM = """너는 개인 기록에서 뽑은 개체 이름 목록을 정리한다. 종류마다 이름과 나온 횟수가 주어진다.
같은 것을 가리킬 가능성이 있는 이름끼리 묶고, 묶음 안의 이름마다 대표 이름과의 관계를 적는다.
- same: 정확히 같은 것. 동의어·활용형·띄어쓰기 차이·줄임말 (뜀·뛰었어·달리기 ↔ 러닝, 제주도 ↔ 제주)
- narrower: 대표 이름보다 좁은 말 (회 ↔ 해물, 운동화 ↔ 신발, 야근 ↔ 일, 뚝섬 ↔ 한강)
- broader: 대표 이름보다 넓은 말
- related: 비슷하지만 다른 것 (장마 ↔ 비, 아침 ↔ 저녁, 밥그릇 ↔ 사료)
대표 이름은 묶음에서 가장 많이 나온 이름이다. 대표 이름 말고 다른 이름이 하나 이상 있는 묶음만 적는다(혼자인 이름은 적지 않는다).
JSON 하나만 출력한다: {"groups":[{"type":"activity","canonical":"러닝","members":[{"name":"뜀","relation":"same"}]}]}"""
SYN_VERSION = "syn3"


def _syn_key(rows: list[tuple[str, str, str]], model: str) -> tuple[str, dict[str, Counter[str]]]:
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    for _, t, n in rows:
        if t != "person":  # 사람은 가명이라 LLM 이 묶을 수 없다
            by_type[t][n] += 1
    top = {t: Counter(dict(c.most_common(60))) for t, c in by_type.items()}
    sig = _hash(json.dumps({t: sorted(c) for t, c in sorted(top.items())}, ensure_ascii=False))
    return f"{SYN_VERSION}:{model}:{sig}", top


def build_synonyms(store: Store, model: str = JUDGE_MODEL) -> dict[str, str]:
    """"종류:이름" → 대표 이름. 빌드 때 한 번 LLM 을 부르고 캐시한다. 키가 없으면 빈 표."""
    rows = store.entity_rows()
    key, top = _syn_key(rows, model)
    hit = store.cache_get(key)
    if hit is not None:
        return hit
    if not openrouter_key() or not top:
        return {}
    body = "\n".join(f"[{t}] " + ", ".join(f"{n}({c})" for n, c in cnt.most_common()) for t, cnt in top.items())
    try:
        data = chat_json(SYN_SYSTEM, body, model=model, max_tokens=4000)
    except Exception:
        return {}
    out: dict[str, str] = {}
    # 관계를 적게 한 뒤 same 만 합친다. 묶기만 시키면 넓은 말·좁은 말(해물·회)까지 묶어서다 (2026-09-11 확인)
    for g in data.get("groups", []) if isinstance(data, dict) else []:
        t, canon = str(g.get("type", "")), str(g.get("canonical", "")).strip()
        if t not in top or canon not in top[t]:
            continue
        for m in g.get("members", []):
            n = str(m.get("name", "")).strip()
            if m.get("relation") == "same" and n != canon and n in top[t]:
                out[f"{t}:{n}"] = canon
    store.cache_put(key, out)
    return out


def cached_synonyms(store: Store, model: str = JUDGE_MODEL) -> dict[str, str]:
    key, _ = _syn_key(store.entity_rows(), model)
    return store.cache_get(key) or {}


def canonical_names(rows: list[tuple[str, str, str]],
                    synonyms: dict[str, str] | None = None) -> dict[tuple[str, str], tuple[str, str] | None]:
    """(종류, 이름) → 합칠 (종류, 이름). None 이면 그래프에서 뺀다. synonyms 는 "종류:이름" → 대표 이름."""
    syn = synonyms or {}
    rows = [(rid, t, syn.get(f"{t}:{n}", n)) for rid, t, n in rows] if syn else rows
    count: Counter[tuple[str, str]] = Counter((t, n) for _, t, n in rows)
    names_by_type: dict[str, Counter[str]] = defaultdict(Counter)
    for (t, n), c in count.items():
        names_by_type[t][n] += c
    out: dict[tuple[str, str], tuple[str, str] | None] = {}

    def base(t: str, n: str) -> str | None:
        n = n.strip().strip("()")
        if t == "person":
            if n in NOT_PEOPLE:
                return None
            for suf in ("씨", "님"):
                if n.endswith(suf) and n[: -len(suf)] in names_by_type[t]:
                    return n[: -len(suf)]
            return n
        if n.startswith("우리") and n[2:].strip() in names_by_type[t]:
            n = n[2:].strip()
        for suf in (" 앞", " 근처", "도"):
            if n.endswith(suf) and len(n) - len(suf) >= 2 and n[: -len(suf)] in names_by_type[t]:
                n = n[: -len(suf)]
                break
        # 머리 명사만 쓴 이름("크루")은 그 머리 명사로 끝나는 복합어가 하나뿐이고 그쪽이 더 자주 나올 때만 합친다
        # ("크루" 3번 → "러닝 크루" 5번). "카페"(6번)를 "제주도 카페"(1번)로 합치는 일은 없게 한다
        compounds = [m for m in names_by_type[t] if m != n and m.endswith(" " + n) and not m.startswith("우리")]
        if len(compounds) == 1 and names_by_type[t][compounds[0]] > names_by_type[t][n]:
            n = compounds[0]
        return n

    merged: Counter[tuple[str, str]] = Counter()
    for (t, n), c in count.items():
        b = base(t, n)
        out[(t, n)] = (t, b) if b else None
        if b:
            merged[(t, b)] += c
    # 같은 이름이 여러 종류로 잡히면 많이 나온 종류로 (사람·동물은 섞지 않는다)
    by_name: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (t, n), c in merged.items():
        by_name[n].append((c, t))
    major = {n: max(v)[1] for n, v in by_name.items()}
    for key, val in out.items():
        if val and val[0] not in ("person", "animal") and major[val[1]] not in ("person", "animal"):
            out[key] = (major[val[1]], val[1])
    return out


def graph(store: Store, emotions: dict[str, tuple[float, float, str]], record_ids: set[str] | None = None,
          limit: int = 40) -> dict[str, Any]:
    """노드·간선 목록. record_ids 를 주면 그 기록에 나온 개체와 그 이웃만 남긴다."""
    import networkx as nx

    recs = {r.id: r for r in store.records()}
    rows = store.entity_rows()
    syn = cached_synonyms(store)
    canon = canonical_names(rows, syn)
    canon.update({(t, n): canon.get((t, syn[f"{t}:{n}"])) for _, t, n in rows if f"{t}:{n}" in syn})
    aliases: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_rec: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rid, t, n in rows:
        c = canon.get((t, n))
        if c:
            by_rec[rid].append(c)
            if c[1] != n:
                aliases[c].add(n)
    g = nx.Graph()
    for rid, ents in by_rec.items():
        r = recs.get(rid)
        if not r:
            continue
        uniq = sorted(set(ents))
        for t, n in uniq:
            key = f"{t}:{n}"
            if key not in g:
                g.add_node(key, type=t, name=n, count=0, records=[], first=r.day, last=r.day, vsum=0.0, vn=0)
            d = g.nodes[key]
            d["count"] += 1
            d["records"].append(rid)
            d["first"], d["last"] = min(d["first"], r.day), max(d["last"], r.day)
            if rid in emotions:
                d["vsum"] += emotions[rid][0]
                d["vn"] += 1
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = f"{uniq[i][0]}:{uniq[i][1]}", f"{uniq[j][0]}:{uniq[j][1]}"
                w = g.get_edge_data(a, b, {}).get("weight", 0)
                g.add_edge(a, b, weight=w + 1)
    rename = {n: c[1] for (t, n), c in canon.items() if c}
    rel_count: Counter[tuple[str, str, str]] = Counter((rename.get(s, s), p, rename.get(o, o))
                                                       for _, s, p, o in store.relation_rows())
    names = {d["name"]: k for k, d in g.nodes(data=True)}
    for (s, p, o), c in rel_count.items():
        if s in names and o in names and names[s] != names[o]:
            e = g.get_edge_data(names[s], names[o]) or {}
            preds = dict(e.get("predicates", {}))
            preds[p] = preds.get(p, 0) + c
            g.add_edge(names[s], names[o], weight=e.get("weight", 0), predicates=preds)
    nodes = list(g.nodes)
    if record_ids is not None:
        seeds = [k for k in nodes if set(g.nodes[k]["records"]) & record_ids]
        focus = Counter({k: len(set(g.nodes[k]["records"]) & record_ids) for k in seeds})
        keep = [k for k, _ in focus.most_common(limit)]
    else:
        keep = sorted(nodes, key=lambda k: -g.nodes[k]["count"])[:limit]
    keep_set = set(keep)
    out_nodes = []
    for k in keep:
        d = g.nodes[k]
        out_nodes.append({"id": k, "type": d["type"], "type_ko": TYPE_KO.get(d["type"], d["type"]), "name": d["name"],
                          "aliases": sorted(aliases.get((d["type"], d["name"]), ())),
                          "count": d["count"], "first": d["first"], "last": d["last"],
                          "valence": round(d["vsum"] / d["vn"], 3) if d["vn"] else None,
                          "focus": (len(set(d["records"]) & record_ids) if record_ids is not None else None)})
    out_edges = []
    for a, b, d in g.edges(data=True):
        if a in keep_set and b in keep_set and d.get("weight", 0) + sum(d.get("predicates", {}).values()) > 0:
            out_edges.append({"source": a, "target": b, "weight": d.get("weight", 0), "predicates": d.get("predicates", {})})
    out_edges.sort(key=lambda e: -e["weight"])
    return {"nodes": out_nodes, "edges": out_edges[: limit * 3]}


def find_gaps(store: Store, engine: QueryEngine, model: str = LLM_MODEL, max_topics: int = 8) -> list[dict[str, Any]]:
    """자기 서술과 기록 사이의 간극. 결과는 캐시에 남긴다 (기록이 바뀌면 다시 계산)."""
    own = [r for r in engine.records if r.is_self and r.text and len(r.text) >= 6]
    from .query import JUDGE_VERSION  # 판정 기준이 바뀌면 간극의 기록 수도 달라진다

    key = f"{CLAIM_VERSION}:{JUDGE_VERSION}:{model}:{_hash(''.join(r.id + (r.text or '') for r in engine.records))}"
    hit = store.cache_get(key)
    if hit is not None:
        return hit
    if not openrouter_key():
        return []
    masker = Masker.from_store(store)
    claims: list[dict[str, Any]] = []
    for k in range(0, len(own), 60):
        chunk = own[k : k + 60]
        lines = "\n".join(f"{i}. {(masker.mask(r.text) or '')[:160].replace(chr(10), ' / ')}" for i, r in enumerate(chunk))
        try:
            data = chat_json(CLAIM_SYSTEM, f"기록 {len(chunk)}건:\n{lines}", model=model, max_tokens=1500)
        except Exception:
            continue
        for c in data.get("claims", []) if isinstance(data, dict) else []:
            i = int(c.get("i", -1))
            if (0 <= i < len(chunk) and c.get("kind") == "habit" and c.get("topic")
                    and c.get("polarity") in (-1, 1, "-1", "1")):
                claims.append({"record": chunk[i], "claim": str(c.get("claim", "")).strip(),
                               "topic": str(c["topic"]).strip(), "polarity": int(c["polarity"])})
    # 주제 낱말이 말한 기록 말고도 두 건 이상 실제로 나와야 기록 수와 견줄 수 있다
    lemma_hits: Counter[str] = Counter()
    for r in own:
        for w in set(r.meta.get("lemmas") or []):
            lemma_hits[w] += 1
    by_topic: dict[str, dict[str, Any]] = {}
    for c in claims:
        if c["topic"] in BROAD_TOPICS or lemma_hits[c["topic"]] < 3:
            continue
        by_topic.setdefault(c["topic"], c)
    last_day = max(r.dt.date() for r in engine.records)
    out = []
    for topic, c in list(by_topic.items())[:max_topics]:
        res = engine.query(topic)
        r0 = c["record"]
        t0 = r0.dt.date()
        ev = [s.record for s in res.strong if s.record.id != r0.id]
        after = [r for r in ev if r.dt.date() > t0]
        before = [r for r in ev if r.dt.date() <= t0]
        days_after = len({r.day for r in after})
        span_after = (last_day - t0).days
        if c["polarity"] < 0 and span_after < 14 and len(after) < 5:
            verdict = "판단 보류"  # 말한 뒤 기록 기간이 짧으면 "안 한다"가 맞는지 알 수 없다
        elif c["polarity"] < 0:
            verdict = "간극" if len(after) >= 5 and days_after >= 3 else "일치" if len(after) <= 1 else "판단 보류"
        else:
            verdict = "간극" if len(ev) <= 1 else "일치" if len(ev) >= 5 else "판단 보류"
        quote = (masker.mask(r0.text) or "").replace("\n", " / ")[:60]
        d0 = patterns.ko_date(t0)
        if verdict == "간극" and c["polarity"] < 0:
            summary = f"{d0}에 \"{quote}\"라고 했지만, 그 뒤 '{topic}' 기록이 {len(after)}건({days_after}일) 있습니다."
        elif verdict == "간극":
            summary = f"{d0}에 \"{quote}\"라고 했지만, 전체 기록에서 '{topic}' 기록은 {len(ev)}건뿐입니다."
        elif verdict == "일치":
            summary = f"{d0}의 \"{quote}\"는 기록과 맞습니다 ('{topic}' 기록 {len(ev)}건)."
        elif c["polarity"] < 0 and span_after < 14:
            summary = f"{d0}의 \"{quote}\"는 그 뒤 기록이 {span_after}일치뿐이라 판단하기 어렵습니다."
        else:
            summary = f"{d0}의 \"{quote}\"는 기록만으로 판단하기 어렵습니다 ('{topic}' 기록 {len(ev)}건)."
        out.append({"claim": c["claim"], "topic": topic, "polarity": c["polarity"], "record_id": r0.id,
                    "date": t0.isoformat(), "quote": quote, "evidence_before": len(before), "evidence_after": len(after),
                    "days_after": days_after, "grade": res.grade, "verdict": verdict, "summary": summary,
                    "evidence_ids": [r.id for r in ev][:30]})
    order = {"간극": 0, "판단 보류": 1, "일치": 2}
    out.sort(key=lambda x: (order[x["verdict"]], -x["evidence_after"]))
    store.cache_put(key, out)
    return out

