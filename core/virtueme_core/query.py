"""자유 키워드 → 관련 기록 → 근거 등급.

관련도는 직접 일치와 의미 유사도를 noisy-OR 로 합친다: rel = 1 - (1 - direct)(1 - semantic).

직접 일치 (textproc 형태소 원형 비교)
- 키워드 원형이 기록에 있으면 0.9, LLM 이 준 활용형·동의어(forms)가 있으면 0.75.

의미 유사도 (embed 두 공간)
- 기록마다 배경 질의 40개와의 코사인 평균·표준편차로 z 점수를 만든다. 무엇과도 비슷한 짧은 기록
  ("ㅇㅇ", "ㅋㅋ")이 모든 키워드에 걸리는 현상(허브 문제)을 줄이고, 텍스트 공간과 사진 공간의 값 크기를 맞춘다.
- 키워드 자체의 z 와 확장 문장(situations·forms) 상위 3개의 평균 z 를 반반 섞는다.
- z 를 로지스틱 곡선으로 0..1 로 바꾼다 (config.RelevanceConfig).

2026-09-11 eval/retrieval_eval.py 결과 (bge-m3, 확장 모델 gemini-2.5-flash)
- 한국어 문장 66개·주제 12개: MAP 0.715(키워드만) → 0.741, 강한 근거 F1 0.404 → 0.615
- 합성 활동 태그 35개·키워드 10개: MAP 0.825 → 0.853, 강한 근거 F1 0.695 → 0.671
  (태그처럼 짧은 기록은 확장이 재현율을 올리는 대신 정밀도를 조금 깎는다)

확장 목록은 LLM 이 만들고 DB 캐시에 남는다. 키가 없으면 확장 없이 키워드만으로 찾는다.

LLM 판정 (키가 있을 때 기본)
- 실제 대화 발화는 길고 주제가 섞여 있어 임베딩만으로는 경계가 흐리다 ("러닝" 에 "야근중" 발화가 걸린다).
- 임베딩 관련도 0.2 이상 후보(최대 240건)를 가려진 텍스트로 판정 모델에 보내 2·1·0 을 받는다.
  2 는 강한 근거, 1 은 약한 근거가 되고, 판정받지 못한 기록은 근거가 되지 않는다. 판정은 캐시에 남는다.
- 2026-09-11 eval/profile_eval.py (가상 인물 윤서연 본인 기록 345건, 키워드 10개, 은 라벨 claude-sonnet-4.5):
  강한 근거 F1 임베딩만 0.48 → 판정 0.68 (정밀도 0.38 → 0.71), 약한 근거 이상 F1 0.48 → 0.74.
  은 라벨과 판정 모델이 같은 회사 모델이라 일치율이 부풀 수 있으므로, 사람이 붙인 라벨로 2단계에서 다시 잰다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import numpy as np

from . import patterns
from .config import JUDGE_MODEL, LLM_MODEL, SETTINGS, Settings, openrouter_key
from .embed import sigmoid
from .llm import chat_json
from .records import Record
from .store import Store
from .textproc import lemmas, lemmas_many

EXPAND_SYSTEM = """너는 개인 기록 검색을 돕는다. 한국어 키워드 하나가 주어지면 JSON 으로 두 목록을 만든다.
- forms: 키워드와 같은 뜻으로 기록에 직접 쓰일 낱말 6~10개. 기본형(외롭다), 동의어(쓸쓸하다), 구어·줄임말(혼밥)을 넣는다. 한 항목에 한 낱말.
- situations: 키워드를 직접 쓰지 않고 그 상황을 드러내는 짧은 문장 8~12개. 일기·SNS·메신저 말투로 쓴다. 예: 외로움 → "주말인데 연락 온 사람이 없다".
JSON 하나만 출력한다: {"forms":[...],"situations":[...]}"""

JUDGE_SYSTEM = """너는 개인 기록 검색 결과를 검수한다. 키워드 하나와 기록 목록이 주어진다. 기록마다 키워드와의 관련도를 매긴다.
2 = 기록이 키워드의 주제를 직접 다룬다. 그 일이 실제로 일어났거나, 그 사람·대상에 대해 직접 말한다.
    감정·상태 키워드(예: 피곤, 행복, 외로움)는 낱말이 없어도 그 상태가 분명히 드러나면 2 다 (예: 피곤 ← "집 가면 그냥 쓰러져").
    사람·관계 키워드(예: 친구, 엄마)는 그 사람에 대해 말하거나, 그 사람과 단둘이 나눈 대화·함께한 일이면 2 다.
    여러 사람이 있는 단체 대화방에서 오간 일상 잡담이라는 것만으로는 1 이다.
1 = 간접적으로 이어진다. 같은 상황의 일부이거나 원인·결과·암시로만 읽힌다.
0 = 무관하다. 낱말이 비슷해도 뜻이 다르면 0 이다. 키워드가 한 글자(예: 비, 눈, 차)면 다른 낱말의 일부(비빔국수의 비)나
    다른 뜻(눈치의 눈)은 0 이다.
(나)는 기록 주인이고, 지인A 같은 표기는 가명 처리한 사람이다. [ ] 안은 출처와 대화 상대다.
JSON 하나만 출력한다: {"labels":[{"i":번호,"r":0}]}"""
JUDGE_VERSION = "v4"  # 판정 기준 문구를 바꾸면 올려서 예전 캐시를 쓰지 않게 한다
# 관계 키워드("친구", "동기")일 때만 대화 상대의 관계를 보여 주고 이 문장을 더한다. 다른 키워드에 관계를 보여 주면
# "러닝 크루 친구"와 나눈 잡담이 "러닝" 근거로 끌려와서(eval/profile_eval.py, 러닝 F1 0.84 → 0.67) 관계 키워드에만 쓴다
JUDGE_RELATION_NOTE = ("대화 상대 옆 ( ) 는 기록 주인과의 관계다 (예: 지인A(대학 친구)). 이 키워드는 관계를 가리키므로, "
                       "그 관계인 사람을 키워드의 사람으로 본다.")
JUDGE_VERSION_RELATION = "v5r"
# 흔적 기록(유튜브 시청·검색·결제·방문·건강·음악)은 따로 묶어 이 문장을 더해 판정한다. 글로 쓴 기록의 판정 캐시는 그대로 둔다.
# 이 문장 없이 판정하면 "콩이"(강아지 이름)에 "고양이 웃긴 영상 모음" 시청이 2 로 잡혔다 (2026-10-03 윤서연 샘플)
JUDGE_TRACE_NOTE = ("이번 기록은 기록 주인이 글로 쓴 것이 아니라 기기·서비스에 남은 흔적이다 (본 영상 제목, 검색어, 결제한 가맹점, "
                    "방문한 장소, 하루 걸음·잠·운동, 들은 곡). 흔적이 키워드의 활동·대상을 직접 가리키면 2 다 (러닝 ← 러닝화 결제, "
                    "러닝 자세 영상, 달리기 운동 기록). 키워드가 사람·반려동물의 이름이면 그 이름이 흔적에 나올 때만 2 이고, "
                    "같은 종류의 다른 대상(다른 강아지·고양이 영상)은 그 이름과 함께한 생활과 이어질 때만 1, 아니면 0 이다. "
                    "감정·상태 키워드는 흔적 제목이 그 상태를 직접 말할 때만 2, 짐작할 수 있을 뿐이면 1 이다.")
JUDGE_VERSION_TRACE = "t1"
JUDGE_BATCH = 40

BACKGROUND = [
    "시간", "사람", "생각", "물건", "장소", "날씨", "돈", "교통", "기계", "숫자", "색깔", "도시", "나무", "바다",
    "컴퓨터", "전화", "뉴스", "버스", "학교", "회사", "음식", "운동", "음악", "영화", "책", "옷", "가게", "병원",
    "공원", "여행", "가족", "친구", "동물", "꽃", "비", "밤", "아침", "주말", "일", "휴식",
]
GENERIC_LEMMAS = {
    "하다", "되다", "있다", "없다", "같다", "보다", "이다", "아니다", "것", "수", "때", "거", "나", "너", "우리",
    "좀", "더", "잘", "안", "못", "다", "많다", "주다", "오다", "가다", "그렇다", "이렇다",
}
GRADES = ("강한 근거", "약한 근거", "근거 부족")


@dataclass
class Scored:
    record: Record
    rel: float
    direct: float
    semantic: float
    space: str | None  # 의미 점수를 준 공간 (text | vision)
    embed_rel: float = 0.0  # LLM 판정 전 임베딩·직접 일치 관련도
    judge: int | None = None  # LLM 판정 (2 직접, 1 간접, 0 무관, None 판정 안 함)


@dataclass
class QueryResult:
    keyword: str
    expansions: dict[str, list[str]]
    strong: list[Scored]
    weak: list[Scored]
    adjacent: list[Record]
    grade: str
    basis: dict[str, Any]
    scope: list[Record]              # 등급 계산 대상 기록 (기본: 본인 기록)
    all_scored: list[Scored] = field(repr=False, default_factory=list)


def expand_keyword(store: Store, keyword: str, model: str = LLM_MODEL) -> dict[str, list[str]]:
    key = f"exp:{model}:{keyword}"
    hit = store.cache_get(key)
    if hit is not None:
        return hit
    if not openrouter_key():
        return {"forms": [], "situations": []}
    try:
        data = chat_json(EXPAND_SYSTEM, f"키워드: {keyword}", model=model, max_tokens=800)
    except Exception:
        return {"forms": [], "situations": []}
    out = {
        "forms": [str(x).strip() for x in data.get("forms", []) if str(x).strip()][:12],
        "situations": [str(x).strip() for x in data.get("situations", []) if str(x).strip()][:14],
    }
    store.cache_put(key, out)
    return out


def judge_line(r: Record, chars: int = 160, people: dict[str, str | None] | None = None) -> str:
    """LLM 판정·집필에 넘기는 한 줄. 텍스트는 가져올 때 이미 가려져 있다. people(가명 → 관계)을 주면
    "지인A(대학 친구)"처럼 적는다: 관계를 모르면 "친구" 키워드에서 친구와 나눈 대화를 가려낼 수 없다."""
    src = {"kakao": "카카오톡", "sms": "문자", "instagram": "인스타그램", "photo": "사진", "synthetic": "활동",
           "youtube": "유튜브 시청 기록", "search": "검색 기록", "maps": "지도 방문 기록", "card": "카드 결제 내역",
           "health": "건강 앱", "spotify": "음악 재생 기록", "netflix": "넷플릭스 시청 기록"}.get(r.source, r.source)
    people = people or {}
    who = [f"{p}({people[p]})" if people.get(p) else p for p in r.participants if p != "나"]
    ctx = f"{src} · {', '.join(who)}와의 대화" if r.thread and who else src
    body = (embed_text(r) or "").replace("\n", " / ")
    return f"[{ctx}] {body[:chars]}"


def relation_aliases(people: dict[str, str | None], keyword: str) -> set[str]:
    """키워드가 관계를 가리킬 때 그 관계인 가명. 관계 전체("대학 친구")나 끝 낱말("친구", "동기")과 같아야 한다
    ("친구" → 대학 친구·러닝 크루 친구, "러닝" → 없음)."""
    kw = keyword.strip()
    return {a for a, rel in people.items() if rel and (kw == rel or kw == rel.split()[-1])}


def judge(store: Store, keyword: str, records: list[Record], model: str = JUDGE_MODEL,
          people: dict[str, str | None] | None = None) -> dict[str, int]:
    """기록마다 0·1·2 판정. 캐시에 있는 판정은 다시 묻지 않는다.
    관계 키워드면 판정 줄에 대화 상대의 관계를 보여 준다. people 을 주지 않으면 가명표의 관계를 쓴다."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    people = store.relations() if people is None else people
    relational = bool(relation_aliases(people, keyword))
    system = JUDGE_SYSTEM.replace("\nJSON", f"\n{JUDGE_RELATION_NOTE}\nJSON") if relational else JUDGE_SYSTEM
    base_system, trace_system = system, system.replace("\nJSON", f"\n{JUDGE_TRACE_NOTE}\nJSON")
    version = JUDGE_VERSION_RELATION if relational else JUDGE_VERSION
    shown = people if relational else {}
    out: dict[str, int] = {}
    todo: list[tuple[Record, str, str]] = []
    for r in records:
        line = judge_line(r, people=shown)
        ver = f"{version}{JUDGE_VERSION_TRACE}" if r.is_trace else version
        key = f"judge:{ver}:{model}:{keyword}:{r.id}:{hashlib.sha1(line.encode()).hexdigest()[:8]}"
        hit = store.cache_get(key)
        if hit is not None:
            out[r.id] = int(hit)
        else:
            todo.append((r, line, key))

    def run(batch: list[tuple[Record, str, str]]) -> list[tuple[str, str, int]]:
        body = "\n".join(f"{i}. {line}" for i, (_, line, _) in enumerate(batch))
        data = chat_json(trace_system if batch[0][0].is_trace else base_system, f"키워드: {keyword}\n기록 {len(batch)}건:\n{body}", model=model,
                         max_tokens=20 * len(batch) + 200)
        got = []
        for item in data.get("labels", []) if isinstance(data, dict) else data:
            i = int(item.get("i", -1))
            if 0 <= i < len(batch):
                got.append((batch[i][0].id, batch[i][2], max(0, min(2, int(item.get("r", 0))))))
        return got

    # 글로 쓴 기록과 흔적 기록은 판정 기준 문장이 달라 따로 묶는다
    batches = [grp[k : k + JUDGE_BATCH] for grp in ([t for t in todo if not t[0].is_trace], [t for t in todo if t[0].is_trace])
               for k in range(0, len(grp), JUDGE_BATCH)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for got in pool.map(run, batches):
            for rid, key, label in got:
                out[rid] = label
                store.cache_put(key, label)
    return out


def embed_text(r: Record) -> str | None:
    """text 공간에 넣을 문장. 사진은 자동 태그로 문장을 만든다."""
    auto = [t["tag"] for t in r.meta.get("auto_tags", [])]
    photo = f"사진: {', '.join(auto)}" if auto else None
    if r.text and photo:
        return f"{r.text} / {photo}"
    return r.text or photo


def record_lemmas(r: Record) -> set[str]:
    got = r.meta.get("lemmas")
    if got is not None:
        return set(got)
    return lemmas(embed_text(r) or "") | set(r.tags)


class QueryEngine:
    def __init__(self, store: Store, text_embedder, vision_embedder=None, settings: Settings = SETTINGS,
                 llm_model: str = LLM_MODEL, judge_model: str = JUDGE_MODEL):
        self.store = store
        self.text = text_embedder
        self.vision = vision_embedder
        self.settings = settings
        self.llm_model = llm_model
        self.judge_model = judge_model
        self.relation_aware = True  # 관계 키워드를 붙인 관계로 찾는다 (평가에서 3단계 방식과 비교할 때만 끈다)
        self.reload()

    def reload(self) -> None:
        self.records = self.store.records()
        self.by_id = {r.id: r for r in self.records}
        self.pos = {r.id: i for i, r in enumerate(self.records)}
        self.emotions = self.store.emotions()
        self.daily = patterns.daily(self.records, self.emotions) if self.records else None
        self.spaces: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for space in ("text", "vision"):
            ids, mat = self.store.vectors(space)
            idx = np.array([self.pos[i] for i in ids if i in self.pos], dtype=int)
            keep = [k for k, i in enumerate(ids) if i in self.pos]
            if len(keep):
                self.spaces[space] = (idx, mat[keep])
        self._bg: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._lemmas: list[set[str]] | None = None

    # ---------- 구성 요소 ----------
    def _encoder(self, space: str):
        return self.text.encode if space == "text" else self.vision.encode_texts

    def _background(self, space: str) -> tuple[np.ndarray, np.ndarray]:
        if space not in self._bg:
            _, mat = self.spaces[space]
            b = self._encoder(space)(BACKGROUND)
            s = b @ mat.T
            sd = s.std(axis=0)
            # 배경 질의와의 거리가 거의 같은 기록은 z 가 폭주하므로 표준편차에 하한을 둔다
            sd = np.maximum(sd, float(np.median(sd)) * 0.5) + 1e-6
            self._bg[space] = (s.mean(axis=0), sd)
        return self._bg[space]

    def _z(self, space: str, qvecs: np.ndarray) -> np.ndarray:
        _, mat = self.spaces[space]
        mu, sd = self._background(space)
        return (qvecs @ mat.T - mu) / sd  # (질의 수, 기록 수)

    def _all_lemmas(self) -> list[set[str]]:
        if self._lemmas is None:
            missing = [i for i, r in enumerate(self.records) if r.meta.get("lemmas") is None]
            computed = dict(zip(missing, lemmas_many(embed_text(self.records[i]) or "" for i in missing)))
            self._lemmas = [
                (set(r.meta["lemmas"]) if i not in computed else computed[i]) | set(r.tags)
                for i, r in enumerate(self.records)
            ]
        return self._lemmas

    def scores(self, keyword: str, expansions: dict[str, list[str]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str | None]]:
        cfg = self.settings.relevance
        n = len(self.records)
        # 직접 일치
        kw_l = {x for x in lemmas(keyword) if x not in GENERIC_LEMMAS} or {keyword}
        form_l: set[str] = set()
        for f in expansions.get("forms", []):
            form_l |= {x for x in lemmas(f) if x not in GENERIC_LEMMAS and (len(x) > 1 or len(keyword) == 1)}
        form_l -= kw_l
        direct = np.zeros(n)
        for i, lem in enumerate(self._all_lemmas()):
            if kw_l & lem:
                direct[i] = cfg.direct_keyword
            elif form_l & lem:
                direct[i] = cfg.direct_form
        # 구조: 키워드가 대화 상대(관계어 연락처 "엄마" 등)와 같으면 그 방에서 내가 보낸 발화도 직접 근거로 본다.
        # 기록 넣기 화면에서 붙인 관계가 키워드를 품으면("친구" ⊂ "대학 친구") 그 사람과 단둘이 나눈 대화는 직접 근거,
        # 여럿이 있는 대화방은 약한 근거 후보로 둔다 (판정 기준 v4 와 같은 선: 단체방 잡담은 간접)
        rel_aliases = relation_aliases(self.store.relations(), keyword) if self.relation_aware else set()
        for i, r in enumerate(self.records):
            if not (r.is_self and r.thread):
                continue
            if keyword in r.participants and keyword != "나":
                direct[i] = max(direct[i], cfg.direct_form)
            elif rel_aliases and rel_aliases & set(r.participants):
                one_to_one = len([p for p in r.participants if p != "나"]) == 1
                direct[i] = max(direct[i], cfg.direct_form if one_to_one else (cfg.weak + cfg.judge_floor) / 2)
        # 의미 유사도
        semantic = np.zeros(n)
        space_of: list[str | None] = [None] * n
        extra = expansions.get("situations", []) + expansions.get("forms", [])
        for space in ("text", "vision"):
            if space not in self.spaces or (space == "vision" and self.vision is None):
                continue
            idx, _ = self.spaces[space]
            q = self._encoder(space)([keyword, *extra])
            z = self._z(space, q)
            if len(z) == 1:
                mix = z[0]
            else:
                # 확장 문장 하나만 우연히 닮은 기록이 끌려오지 않도록 최대값 대신 상위 3개 평균을 쓴다
                top = np.sort(z[1:], axis=0)[-cfg.expansion_top_k:].mean(axis=0)
                mix = (1 - cfg.expansion_mix) * z[0] + cfg.expansion_mix * top
            sem = sigmoid((mix - cfg.z_center) / cfg.z_width)
            for k, i in enumerate(idx):
                if sem[k] > semantic[i]:
                    semantic[i] = sem[k]
                    space_of[i] = space
        rel = 1 - (1 - direct) * (1 - semantic)
        return rel, direct, semantic, space_of

    # ---------- 질의 ----------
    def query(self, keyword: str, include_others: bool | None = None, use_judge: bool | None = None) -> QueryResult:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("키워드가 비어 있습니다")
        cfg, gcfg = self.settings.relevance, self.settings.grade
        include_others = self.settings.include_others_as_evidence if include_others is None else include_others
        use_judge = self.settings.use_judge if use_judge is None else use_judge
        expansions = expand_keyword(self.store, keyword, self.llm_model)
        rel, direct, semantic, space_of = self.scores(keyword, expansions)
        scored = [Scored(r, float(rel[i]), float(direct[i]), float(semantic[i]), space_of[i], float(rel[i]))
                  for i, r in enumerate(self.records)]
        scope = [s for s in scored if include_others or s.record.is_self]

        # LLM 판정: 임베딩 후보만 보내고, 판정 결과로 관련도를 다시 매긴다.
        # 판정받은 기록만 근거가 될 수 있고, 후보에 들지 못한 기록은 약한 근거 기준 아래로 둔다.
        method = "임베딩 + 직접 일치"
        judged: dict[str, int] = {}
        if use_judge and openrouter_key():
            cands = sorted((s for s in scope if s.rel >= cfg.judge_floor), key=lambda s: -s.rel)[: cfg.judge_limit]
            try:
                judged = judge(self.store, keyword, [s.record for s in cands], self.judge_model,
                               people=None if self.relation_aware else {})
            except Exception:  # 네트워크 오류면 임베딩 점수만으로 계속한다
                judged = {}
        if judged:
            method = "임베딩 후보 + LLM 판정"
            # 한 글자 키워드("비")는 판정 모델이 다른 낱말의 일부("비빔국수")를 2 로 매기는 일이 남아서,
            # 형태소가 직접 맞지 않고 의미 점수도 낮은 기록은 간접(1)까지만 인정한다
            one_char = len(keyword.strip()) == 1
            for s in scope:
                lab = judged.get(s.record.id)
                if one_char and lab == 2 and s.direct == 0 and s.semantic < 0.6:
                    lab = 1
                s.judge = lab
                if lab == 2:
                    s.rel = cfg.strong + (1 - cfg.strong) * s.embed_rel
                elif lab == 1:
                    s.rel = cfg.weak + (cfg.strong - cfg.weak) * 0.99 * s.embed_rel
                elif lab == 0:
                    s.rel = cfg.weak * 0.8 * s.embed_rel
                else:
                    s.rel = min(s.embed_rel, cfg.weak * 0.99)
        strong = sorted((s for s in scope if s.rel >= cfg.strong), key=lambda s: -s.rel)
        weak = sorted((s for s in scope if cfg.weak <= s.rel < cfg.strong), key=lambda s: -s.rel)
        adjacent = self._adjacent(strong, {s.record.id for s in strong + weak})

        # 기록이 있는 날은 글로 쓴 기록으로 센다. 건강 하루 요약처럼 매일 생기는 흔적 기록까지 세면 비율 기준이 흔들린다
        written = {s.record.day for s in scope if not s.record.is_trace}
        active_days = len(written or {s.record.day for s in scope})
        strong_days = len({s.record.day for s in strong})
        any_days = len({s.record.day for s in strong + weak})
        share = strong_days / active_days if active_days else 0.0
        if (len(strong) >= gcfg.strong_min_records and strong_days >= gcfg.strong_min_days
                and share >= gcfg.strong_min_day_share):
            grade = GRADES[0]
        elif (len(strong) >= gcfg.weak_min_records and strong_days >= gcfg.weak_min_days) or (
                len(strong) + len(weak) >= gcfg.weak_min_any_records and any_days >= gcfg.weak_min_any_days):
            # 직접 말한 기록은 적어도 간접 근거가 여러 날에 걸쳐 많으면 약한 근거로 본다
            grade = GRADES[1]
        else:
            grade = GRADES[2]
        basis = {
            "strong_records": len(strong), "weak_records": len(weak), "strong_days": strong_days,
            "evidence_days": any_days, "active_days": active_days, "strong_day_share": round(share, 3),
            "scope": "본인 기록 + 다른 사람 메시지" if include_others else "본인 기록",
            "thresholds": {
                "relevance_strong": cfg.strong, "relevance_weak": cfg.weak,
                "strong_grade": {"records": gcfg.strong_min_records, "days": gcfg.strong_min_days,
                                 "day_share": gcfg.strong_min_day_share},
                "weak_grade": {"records": gcfg.weak_min_records, "days": gcfg.weak_min_days,
                               "or_any_records": gcfg.weak_min_any_records, "or_any_days": gcfg.weak_min_any_days},
            },
            "expanded": bool(expansions.get("forms") or expansions.get("situations")),
            "method": method,
            "judge_model": self.judge_model if judged else None,
            "judged_records": len(judged),
        }
        return QueryResult(keyword, expansions, strong, weak, adjacent, grade, basis,
                           [s.record for s in scope], scored)

    def retrieve(self, text: str, k: int = 16) -> list[Record]:
        """대화 한 턴에 쓸 가까운 기록. 확장·판정 없이 텍스트 공간 z 점수 순 (응답이 빨라야 하므로)."""
        if "text" not in self.spaces or not text.strip():
            return []
        idx, _ = self.spaces["text"]
        z = self._z("text", self.text.encode([text]))[0]
        order = np.argsort(-z)[: k * 2]
        out = [self.records[idx[i]] for i in order if z[i] > 1.0]
        return out[:k]

    def _adjacent(self, strong: list[Scored], taken: set[str]) -> list[Record]:
        """강한 근거와 같은 대화방에서 앞뒤로 오간 기록, 같은 외출에서 찍은 사진."""
        win = timedelta(minutes=self.settings.relevance.adjacent_minutes)
        by_thread: dict[str, list[Record]] = {}
        photos = [r for r in self.records if r.kind == "photo"]
        for r in self.records:
            if r.thread:
                by_thread.setdefault(r.thread, []).append(r)
        out: dict[str, Record] = {}
        for s in strong:
            r = s.record
            t0 = r.dt
            if r.thread:
                for o in by_thread.get(r.thread, []):
                    if o.id not in taken and abs(o.dt - t0) <= win:
                        out[o.id] = o
            if r.kind == "photo":
                for o in photos:
                    if o.id not in taken and abs(o.dt - t0) <= timedelta(minutes=60):
                        out[o.id] = o
        return sorted(out.values(), key=lambda r: r.ts)

    def profile(self) -> dict[str, Any]:
        """데이터 전체 요약 (키워드와 무관)."""
        if not self.records or self.daily is None:
            return {"records": 0}
        d = self.daily
        own = [r for r in self.records if r.is_self and not r.is_trace]
        vals = [self.emotions[r.id][0] for r in own if r.id in self.emotions]
        return {
            "summary": self.store.summary(),
            "date_range": [d.dates[0].isoformat(), d.dates[-1].isoformat()],
            "valence_mean": round(float(np.mean(vals)), 3) if vals else None,
            "change_points": patterns.change_points(d.valence, d.dates),
            "activity_change_points": patterns.change_points(np.log1p(d.count), d.dates, min_delta=0.15),
            "periodicity": patterns.periodicity(d.count, d.dates),
            "hours": patterns.hour_profile(own),
        }
