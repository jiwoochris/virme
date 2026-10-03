"""사람이 참고 표지 라벨을 검수하는 도구.

    python -m virtueme_core.book review --n 30 --seed 1        # 연락 시트 HTML 을 만든다
    python -m virtueme_core.book review --apply 고친값.json     # 사람이 고친 값을 references.json 에 넣는다
    python -m virtueme_core.book review --stats                  # 지금까지 검수한 표지 수와 칸마다 일치율

연락 시트는 `$VIRTUEME_DATA/book_refs/review/review-<seed>.html` 이다. 표지마다 이미지(로컬 파일)와 비전 모델 라벨이
선택 상자로 들어 있다. 틀린 칸만 바꾸고 맨 위 "고친 값 내려받기"를 누르면 시트에 있던 표지 전부가 JSON 하나로 내려온다
(바꾸지 않은 표지는 "맞음"으로 기록된다). 그 파일을 `--apply` 로 넣으면 표지마다 다음이 남는다.

- `label`: 사람이 고친 값으로 바뀐 라벨
- `label_checked: true`, `label_checked_by`, `label_checked_at`
- `label_model`: 고치기 전 모델 라벨 (일치율을 다시 셀 수 있게)
- `label_corrected_fields`: 사람이 바꾼 칸 이름

`grammar` 는 검수한 표지를 2배로 세고, 브리프의 참고 표지 검색은 검수한 표지에 0.1 을 더한다. `annotate` 를 다시
돌려도 검수한 라벨은 덮어쓰지 않는다.
"""
from __future__ import annotations

import html
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

from .annotate import REFERENCES_PATH, usable
from .collect import ref_image, refs_dir
from .vocab import GENRE_KEYS, LABEL_FIELDS, MOODS, normalize_label

FIELDS = list(LABEL_FIELDS) + ["genre_guess", "moods", "is_cover"]


def _load(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or REFERENCES_PATH).read_text("utf-8"))


def pick(refs: list[dict[str, Any]], n: int, seed: int, include_checked: bool = False) -> list[dict[str, Any]]:
    """라벨이 있고 이미지가 있는 표지 가운데 n 장. 검수하지 않은 것 먼저, 장르마다 고르게 (시드 고정)."""
    rng = random.Random(seed)
    pool = [r for r in refs if r.get("label") and (include_checked or not r.get("label_checked"))
            and ref_image(r["id"]) is not None]
    by_genre: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(pool, key=lambda r: r["id"]):
        by_genre.setdefault(r["genre"], []).append(r)
    for v in by_genre.values():
        rng.shuffle(v)
    out: list[dict[str, Any]] = []
    while len(out) < n and any(by_genre.values()):
        for g in GENRE_KEYS:
            if by_genre.get(g) and len(out) < n:
                out.append(by_genre[g].pop())
    return out


def _select(field: str, value: str | None, options: list[str]) -> str:
    opts = "".join(f"<option value='{o}'{' selected' if o == value else ''}>{o}</option>" for o in [""] + options)
    return f"<label><span>{field}</span><select data-f='{field}' data-orig='{value or ''}'>{opts}</select></label>"


SHEET_CSS = """
body { font: 13px/1.4 -apple-system, 'Apple SD Gothic Neo', sans-serif; margin: 16px; background: #f3f1ec }
header { position: sticky; top: 0; background: #f3f1ec; padding: 8px 0; z-index: 2; display: flex; gap: 12px; align-items: center }
button { font: inherit; padding: 6px 12px }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 14px }
.card { background: #fff; border-radius: 6px; padding: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.12) }
.card.changed { outline: 2px solid #d9480f }
.card img { width: 100%; height: 300px; object-fit: contain; background: #eee; display: block; margin-bottom: 8px }
.meta { color: #555; font-size: 12px; margin-bottom: 6px; min-height: 2.8em }
label { display: flex; justify-content: space-between; gap: 6px; margin: 2px 0 }
label span { color: #777 }
select { width: 60% }
.moods { font-size: 11px; color: #555; margin-top: 4px }
.moods input { width: 100%; font: inherit }
"""

SHEET_JS = """
for (const s of document.querySelectorAll('select, input.mood-in, input.cover-in'))
  s.addEventListener('change', () => mark(s.closest('.card')))
function cardValues(c) {
  const v = {}
  for (const s of c.querySelectorAll('select')) v[s.dataset.f] = s.value || null
  v.moods = c.querySelector('.mood-in').value.split(/[ ,]+/).filter(Boolean)
  v.is_cover = c.querySelector('.cover-in').checked
  return v
}
function mark(c) {
  const ch = [...c.querySelectorAll('select')].some((s) => s.value !== s.dataset.orig) ||
    c.querySelector('.mood-in').value !== c.querySelector('.mood-in').dataset.orig ||
    String(c.querySelector('.cover-in').checked) !== c.querySelector('.cover-in').dataset.orig
  c.classList.toggle('changed', ch)
  document.getElementById('n').textContent = document.querySelectorAll('.card.changed').length
}
document.getElementById('dl').onclick = () => {
  const by = document.getElementById('by').value || 'reviewer'
  const items = [...document.querySelectorAll('.card')].map((c) => ({ id: c.dataset.id, label: cardValues(c) }))
  const blob = new Blob([JSON.stringify({ checked_by: by, checked_at: new Date().toISOString().slice(0, 10), items }, null, 1)],
    { type: 'application/json' })
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'label-corrections.json'; a.click()
}
"""


def contact_sheet(n: int = 30, seed: int = 0, out: Path | None = None, include_checked: bool = False,
                  refs_path: Path | None = None) -> Path:
    data = _load(refs_path)
    chosen = pick(data["items"], n, seed, include_checked)
    cards = []
    for r in chosen:
        lab = r["label"] or {}
        prov = r.get("provenance") or {}
        img = ref_image(r["id"])
        sels = "".join(_select(f, lab.get(f), list(allowed)) for f, allowed in LABEL_FIELDS.items())
        sels += _select("genre_guess", lab.get("genre_guess"), GENRE_KEYS)
        moods = " ".join(lab.get("moods") or [])
        cards.append(
            f"<div class='card' data-id='{r['id']}'><img src='{img.as_uri() if img else ''}' alt=''>"
            f"<div class='meta'><b>{html.escape(r['id'])}</b> · {r['genre']} · {r['lang']}<br>"
            f"{html.escape(str(prov.get('title') or ''))} ({html.escape(str(prov.get('year') or ''))})</div>{sels}"
            f"<div class='moods'>moods <input class='mood-in' value='{moods}' data-orig='{moods}'></div>"
            f"<label><span>is_cover</span><input type='checkbox' class='cover-in'"
            f"{' checked' if lab.get('is_cover', True) else ''} data-orig='{str(bool(lab.get('is_cover', True))).lower()}'></label>"
            f"</div>")
    doc = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>표지 라벨 검수 {seed}</title>"
           f"<style>{SHEET_CSS}</style></head><body><header><b>표지 라벨 검수</b> {len(chosen)}장 · 바꾼 표지 "
           f"<span id='n'>0</span>장 · 검수자 <input id='by' placeholder='이름'>"
           f"<button id='dl'>고친 값 내려받기</button>"
           f"<span style='color:#777'>moods 는 {', '.join(MOODS)} 가운데 최대 4개, 띄어쓰기로 구분</span></header>"
           f"<div class='grid'>{''.join(cards)}</div><script>{SHEET_JS}</script></body></html>")
    out = out or refs_dir() / "review" / f"review-{seed}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, "utf-8")
    return out


def apply_corrections(corr: dict[str, Any], refs_path: Path | None = None, write: bool = True) -> dict[str, Any]:
    """사람이 확인한 라벨을 넣는다. corr = {checked_by, checked_at, items: [{id, label}]}. 반환: 이번 묶음의 일치율."""
    path = refs_path or REFERENCES_PATH
    data = _load(path)
    by_id = {r["id"]: r for r in data["items"]}
    who = corr.get("checked_by") or "reviewer"
    when = corr.get("checked_at") or date.today().isoformat()
    applied = 0
    for it in corr.get("items") or []:
        r = by_id.get(it.get("id"))
        if r is None:
            continue
        model = r.get("label_model") or r.get("label") or {}
        raw = {**model, **(it.get("label") or {})}
        fixed = normalize_label(raw)
        g = str(raw.get("genre_guess") or "").lower()
        fixed["genre_guess"] = g if g in GENRE_KEYS else None
        fixed["moods"] = sorted(set(fixed["moods"]))
        r["label_model"] = model
        r["label"] = fixed
        r["label_checked"] = True
        r["label_checked_by"] = who
        r["label_checked_at"] = when
        r["label_corrected_fields"] = [f for f in FIELDS if _norm_cmp(f, model.get(f)) != _norm_cmp(f, fixed.get(f))]
        applied += 1
    if write:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return {"applied": applied, **agreement(data["items"])}


def _norm_cmp(field: str, v: Any) -> Any:
    if field == "moods":
        return tuple(sorted(v or []))
    if field == "is_cover":
        return bool(True if v is None else v)
    return v or None


def agreement(refs: list[dict[str, Any]]) -> dict[str, Any]:
    """검수한 표지에서 모델 라벨이 사람 판단과 같은 비율 (칸마다, 그리고 다섯 디자인 칸이 모두 맞은 비율)."""
    checked = [r for r in refs if r.get("label_checked")]
    if not checked:
        return {"checked": 0}
    per = {}
    for f in FIELDS:
        same = sum(1 for r in checked if f not in (r.get("label_corrected_fields") or []))
        per[f] = round(same / len(checked), 3)
    all5 = sum(1 for r in checked if not set(r.get("label_corrected_fields") or []) & set(LABEL_FIELDS))
    return {"checked": len(checked), "per_field": per, "all_design_fields": round(all5 / len(checked), 3)}


def stats(refs_path: Path | None = None) -> dict[str, Any]:
    return agreement(_load(refs_path)["items"])


def checked_weight(r: dict[str, Any]) -> float:
    """grammar 가 표지 한 장을 세는 무게. 사람이 검수한 표지는 2."""
    return 2.0 if r.get("label_checked") else 1.0


__all__ = ["contact_sheet", "apply_corrections", "agreement", "stats", "pick", "checked_weight", "usable"]
