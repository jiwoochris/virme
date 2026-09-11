"""photo_manifest.json 의 사진을 위키미디어 공용에서 내려받는다.

사진은 저작권 때문에 저장소에 넣지 않는다. 이 스크립트가 검색어마다 공용 사진을 받아
인스타그램 내보내기 폴더와 휴대폰 사진 폴더(`photos/`)에 넣고, 저작자·라이선스를
CREDITS.photos.md 에 적는다. 휴대폰 사진에는 촬영 시각과 GPS 를 EXIF 로 넣는다.

    uv run python examples/seoyeon/fetch_photos.py
"""
from __future__ import annotations

import io
import json
import re
import time
from collections import Counter
from pathlib import Path

import httpx
from PIL import Image

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "virtueme-example/0.1 (https://github.com/DECK6/virme; KAIST CTxAI capstone sample data)"}
API = "https://commons.wikimedia.org/w/api.php"


def search(query: str, n: int = 6) -> list[dict]:
    r = httpx.get(API, params={
        "action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
        "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": 640, "format": "json",
    }, headers=UA, timeout=30)
    pages = sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    return [p for p in pages if p.get("imageinfo", [{}])[0].get("mime") in ("image/jpeg", "image/png")]


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def to_dms(deg: float) -> tuple[float, float, float]:
    d = int(abs(deg))
    m = int((abs(deg) - d) * 60)
    s = round((abs(deg) - d - m / 60) * 3600, 2)
    return (float(d), float(m), s)


def main() -> None:
    manifest = json.loads((HERE / "photo_manifest.json").read_text(encoding="utf-8"))
    used: Counter[str] = Counter()
    cache: dict[str, list[dict]] = {}
    credit_file = HERE / "CREDITS.photos.md"
    # 이미 받은 사진의 출처 줄은 그대로 두고 새로 받은 사진만 덧붙인다
    lines = credit_file.read_text(encoding="utf-8").splitlines() if credit_file.exists() else []
    credits = {m.group(1): ln for ln in lines if (m := re.match(r"- `([^`]+)`", ln))}
    ok = 0
    for item in manifest:
        target = HERE / item["target"]
        if target.exists():
            ok += 1
            continue
        q = item["query"]
        if q not in cache:
            # 위키미디어 검색은 모든 낱말이 들어간 파일만 찾으므로, 결과가 없으면 뒤 낱말부터 줄인다
            words = q.split()
            for k in range(len(words), 0, -1):
                cache[q] = search(" ".join(words[:k]))
                time.sleep(0.3)
                if cache[q]:
                    break
        cands = cache[q]
        if not cands:
            print("검색 결과 없음:", q)
            continue
        page = cands[used[q] % len(cands)]
        used[q] += 1
        info = page["imageinfo"][0]
        raw = httpx.get(info["thumburl"], headers=UA, timeout=60, follow_redirects=True).content
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.get("exif"):
            exif = img.getexif()
            exif[0x010F], exif[0x0110] = "Apple", "iPhone 15"
            sub = exif.get_ifd(0x8769)
            sub[0x9003] = f"{item['date'].replace('-', ':')} {item['time']}:00"
            sub[0x9011] = "+09:00"
            if item.get("gps"):
                lat, lon = item["gps"]
                exif.get_ifd(0x8825).update({1: "N" if lat >= 0 else "S", 2: to_dms(lat),
                                            3: "E" if lon >= 0 else "W", 4: to_dms(lon)})
            img.save(target, quality=88, exif=exif)
        else:
            img.save(target, quality=88)
        meta = info.get("extmetadata", {})
        credits[item["target"]] = (f"- `{item['target']}` ← [{page['title']}]({info.get('descriptionurl', '')}) · "
                       f"{strip_html(meta.get('Artist', {}).get('value', '')) or '작성자 미상'} · "
                       f"{meta.get('LicenseShortName', {}).get('value', '라이선스 확인 필요')}")
        ok += 1
    head = ["# 샘플 사진 출처", "", "위키미디어 공용에서 내려받은 사진입니다. 저장소에는 포함하지 않습니다.", ""]
    credit_file.write_text("\n".join(head + [credits[k] for k in sorted(credits)]) + "\n", encoding="utf-8")
    print(f"사진 {ok}/{len(manifest)}장 준비")


if __name__ == "__main__":
    main()
