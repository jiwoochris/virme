"""밖으로 나가는 글 기록과 마지막 이름 검사.

기록 문장이 이 컴퓨터 밖으로 나가는 길은 두 가지다.
- core 가 OpenRouter 로 직접 보내는 요청: 정서 채점, 관련도 판정, 관계 추출, 문맥 검사 등 (`llm._chat`)
- core 가 이야기·대화용으로 만들어 브라우저에 넘기고, 브라우저가 OpenRouter 로 보내는 프롬프트 (`/story/prompt`, `/talk/context`)

두 길 모두 보내기 직전에 `Outbound.send` 를 거친다. 여기서 가명표의 실명(본인 이름 포함)과 전화번호·이메일 모양이
남아 있는지 한 번 더 찾아 가명으로 바꾸고, 보낸 글을 프로필 폴더의 `outbound/<날짜>.jsonl` 에 남긴다.
로그에는 실명을 적지 않는다: 무엇을 바꿨는지는 가명과 종류("지인A 의 이름", "전화번호")로만 남긴다.
기록 넣기 화면(`/import`)이 이 로그를 읽어 "밖으로 나간 글"을 보여 준다.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .privacy import _PII_PATTERNS, SELF_ALIAS, Masker
from .store import Store

KEEP_DAYS = 30
MAX_TEXT = 40_000  # 한 건에 남기는 글자 수 상한 (넘으면 뒤를 자르고 표시)

# 지시문 첫 줄 → 화면에 보여 줄 용도
PURPOSES = (
    ("너는 개인 기록 검색을 돕는다", "키워드 확장"),
    ("너는 개인 기록 검색 결과를 검수한다", "키워드 관련도 판정"),
    ("너는 개인 기록의 정서를 채점한다", "정서 채점"),
    ("너는 개인 기록에서 개체와 관계를 뽑는다", "사람·장소 관계 추출"),
    ("너는 기록 주인이 자기 습관을", "스스로 말한 습관 찾기"),
    ("너는 개인 기록에서 뽑은 개체 이름", "개체 이름 합치기"),
    ("너는 개인 기록 묶음에 이름을", "주제 묶음 이름 붙이기"),
    ("너는 소설·대화 속 문장이 인용한", "근거 문맥 검사"),
    ("너는 소설 교정자다", "틀린 문단 다시 쓰기"),
)
PII_KIND = {"[링크:\\1]": "링크", "[이메일]": "이메일", "[주민번호]": "주민번호", "[카드번호]": "카드번호",
            "[전화번호]": "전화번호", "[계좌번호]": "계좌번호", "[계정]": "계정"}


def purpose_of(system: str) -> str:
    head = (system or "").strip()
    for prefix, label in PURPOSES:
        if head.startswith(prefix):
            return label
    return head.splitlines()[0][:40] if head else "기타"


class Outbound:
    def __init__(self, root: Path, store: Store):
        self.root = root
        self.store = store
        self._lock = threading.Lock()
        self._masker: tuple[int, Masker, list[tuple[Any, str]]] | None = None

    # ---------- 마지막 검사 ----------
    def _names(self) -> tuple[Masker, list[tuple[Any, str]]]:
        """가명표로 만든 이름 정규식. 가명표가 바뀌면(새로 가져오기) 다시 만든다."""
        people = self.store.people()
        key = hash(tuple(sorted(people.items())))
        if self._masker is None or self._masker[0] != key:
            m = Masker(people)
            self._masker = (key, m, list(m._names))
        return self._masker[1], self._masker[2]

    def guard(self, text: str) -> tuple[str, list[str]]:
        """가명표의 실명과 개인정보 모양을 찾아 바꾼다. 반환: (바꾼 글, 찾은 것 — 가명·종류로만)."""
        if not text:
            return text, []
        _, names = self._names()
        found: list[str] = []
        out = text
        # Masker.mask 와 같은 순서로 바꾸면서 무엇을 바꿨는지 센다 (전화번호를 먼저 바꿔야 계좌번호로 두 번 세지 않는다)
        for pat, repl in _PII_PATTERNS:
            out, n = pat.subn(repl, out)
            if n:
                found.append(PII_KIND.get(repl, "개인정보"))
        for pat, token in names:
            out, n = pat.subn(token, out)
            if n:
                found.append("본인 이름" if token == f"({SELF_ALIAS})" else f"{token} 의 이름")
        return out, sorted(set(found))

    # ---------- 보내기 직전 ----------
    def send(self, channel: str, system: str, user: str, model: str | None = None,
             purpose: str | None = None) -> tuple[str, str]:
        """검사해서 바꾼 (system, user) 를 돌려주고 로그를 남긴다. channel: core | browser."""
        system2, f1 = self.guard(system)
        user2, f2 = self.guard(user)
        self._write({
            "ts": datetime.now().isoformat(timespec="seconds"), "channel": channel,
            "purpose": purpose or purpose_of(system), "model": model,
            "chars": len(system2) + len(user2), "guarded": sorted(set(f1 + f2)),
            "system": system2[:MAX_TEXT], "user": user2[:MAX_TEXT],
            "truncated": len(system2) > MAX_TEXT or len(user2) > MAX_TEXT,
        })
        return system2, user2

    def _write(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            day = entry["ts"][:10]
            with (self.root / f"{day}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
            for old in self.root.glob("*.jsonl"):
                if old.stem < cutoff:
                    old.unlink(missing_ok=True)

    # ---------- 화면용 ----------
    def _entries(self, days: int) -> list[tuple[str, dict[str, Any]]]:
        if not self.root.exists():
            return []
        cutoff = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        out = []
        for path in sorted(self.root.glob("*.jsonl")):
            if path.stem < cutoff:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                try:
                    out.append((f"{path.stem}:{i}", json.loads(line)))
                except json.JSONDecodeError:
                    continue
        return out

    def summary(self, days: int = 7, recent: int = 40) -> dict[str, Any]:
        rows = self._entries(days)
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for _, e in rows:
            g = groups.setdefault((e["channel"], e["purpose"]), {"channel": e["channel"], "purpose": e["purpose"],
                                                                 "requests": 0, "chars": 0})
            g["requests"] += 1
            g["chars"] += e.get("chars", 0)
        guarded = [(i, e) for i, e in rows if e.get("guarded")]
        return {
            "days": days, "requests": len(rows), "chars": sum(e.get("chars", 0) for _, e in rows),
            "by_purpose": sorted(groups.values(), key=lambda g: -g["chars"]),
            "guarded_requests": len(guarded),
            "guarded_kinds": sorted({k for _, e in guarded for k in e["guarded"]}),
            "recent": [{"id": i, "ts": e["ts"], "channel": e["channel"], "purpose": e["purpose"], "model": e.get("model"),
                        "chars": e.get("chars", 0), "guarded": e.get("guarded", []),
                        # 대화 답은 기록이 지시문(system) 끝에 들어 있다
                        "preview": (e.get("user") or (e.get("system") or "")[-240:])[:240]}
                       for i, e in reversed(rows[-recent:])],
        }

    def item(self, item_id: str) -> dict[str, Any] | None:
        day, _, n = item_id.partition(":")
        path = self.root / f"{day}.jsonl"
        if not (len(day) == 10 and n.isdigit() and path.exists()):
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        return json.loads(lines[int(n)]) if int(n) < len(lines) else None
