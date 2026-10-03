"""카드 결제 가져오기: 카드사 이용내역 파일과 카드 승인 문자.

이용내역 파일
- 카드사 홈페이지·앱의 이용내역 조회 → 엑셀(.xlsx·.xls) 또는 CSV 저장. 위쪽에 조회 기간·카드 이름 같은 줄이 몇 줄 있고
  그 아래에 표가 있으므로, 날짜·가맹점·금액 열 이름이 모두 있는 줄을 찾아 머리글로 쓴다.
- 열 이름은 카드사마다 다르다: 날짜 이용일·이용일자·거래일시·승인일시, 가맹점 가맹점·가맹점명·이용가맹점,
  금액 이용금액·승인금액·금액. 업종·카드 열은 있으면 업종만 쓴다. 취소 표시 열(취소여부·상태)에 "취소"가 있으면 음수로.
- 카드사가 내려 주는 .xls 가운데 실제로는 HTML 표인 것이 많아 그것도 읽는다. 진짜 옛 엑셀(.xls) 은 xlrd 가 있을 때만 읽고,
  없으면 엑셀에서 .xlsx 로 다시 저장해 달라고 안내한다.

카드 승인 문자
- 문자 가져오기(sms.py)가 자동 발송 문자를 버리기 전에 `parse_card_sms` 로 신한·삼성·현대·KB국민·롯데·우리·하나·BC·NH 승인
  문자를 결제 기록으로 바꾼다. "취소" 문자는 음수 금액이다.

카드 번호와 이름은 저장하지 않는다. 레코드에는 가맹점·금액·업종만 남는다.
삼성페이·애플페이처럼 간편결제는 따로 내보내기가 없다. 그 결제는 연결한 카드사의 이용내역·승인 문자에 그대로 나온다.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import DEFAULT_TZ
from ..privacy import Masker
from ..records import Record, iso, record_id
from .traces import Window, find, opened

STATEMENT_FILES = ("*.xlsx", "*.xls", "*.csv")
DATE_COLS = ("이용일시", "이용일자", "이용일", "거래일시", "거래일자", "거래일", "승인일시", "승인일자", "승인일", "매출일자")
TIME_COLS = ("이용시간", "승인시간", "거래시간", "이용시각", "승인시각")
MERCHANT_COLS = ("이용가맹점", "가맹점명", "가맹점", "이용하신가맹점", "이용처", "사용처")
AMOUNT_COLS = ("이용금액", "승인금액", "거래금액", "결제금액", "금액")
CATEGORY_COLS = ("업종", "가맹점업종", "업종명", "업태")
CANCEL_COLS = ("취소여부", "취소구분", "상태", "승인구분", "구분", "매출구분")


def _norm(h: Any) -> str:
    return re.sub(r"[\s()\[\]/·.]|\(원\)|원$", "", str(h or ""))


def _pick(headers: list[str], names: tuple[str, ...]) -> int | None:
    """열 이름 후보 순서대로 찾는다. 같은 이름이 없으면 후보로 시작하는 열 ('이용금액(원)')."""
    norm = [_norm(h) for h in headers]
    for n in names:
        if n in norm:
            return norm.index(n)
    for n in names:
        for i, h in enumerate(norm):
            if h.startswith(n):
                return i
    return None


def find_header(rows: list[list[Any]], scan: int = 40) -> tuple[int, dict[str, int]] | None:
    """앞의 scan 줄에서 날짜·가맹점·금액 열이 모두 있는 첫 줄. (줄 번호, 열 위치) 를 돌려준다."""
    for i, row in enumerate(rows[:scan]):
        cells = [str(c or "").strip() for c in row]
        cols = {"date": _pick(cells, DATE_COLS), "merchant": _pick(cells, MERCHANT_COLS), "amount": _pick(cells, AMOUNT_COLS)}
        if None in cols.values():
            continue
        for key, names in (("time", TIME_COLS), ("category", CATEGORY_COLS), ("cancel", CANCEL_COLS)):
            j = _pick(cells, names)
            if j is not None and j not in cols.values():
                cols[key] = j
        return i, cols
    return None


DATE_RE = re.compile(r"(\d{2,4})[.\-/년 ]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})일?(?:\D+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def parse_when(value: Any, time_value: Any = None, tz: str = DEFAULT_TZ) -> datetime | None:
    """"2026.06.03", "2026-06-03 13:20", "26/06/03", "20260603", 엑셀 날짜 값 → 지역 시각."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        s = str(value or "").strip()
        if re.fullmatch(r"\d{8}", s):
            s = f"{s[:4]}.{s[4:6]}.{s[6:]}"
        m = DATE_RE.search(s)
        if not m:
            return None
        y, mo, d, h, mi, sec = m.groups()
        y = int(y) + (2000 if len(y) == 2 else 0)
        try:
            dt = datetime(y, int(mo), int(d), int(h or 0), int(mi or 0), int(sec or 0))
        except ValueError:
            return None
    if time_value not in (None, "") and dt.hour == 0 and dt.minute == 0:
        tm = re.search(r"(\d{1,2}):(\d{2})", str(time_value))
        if tm:
            dt = dt.replace(hour=int(tm.group(1)), minute=int(tm.group(2)))
    return dt.replace(tzinfo=ZoneInfo(tz))


def parse_amount(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(round(value))
    s = str(value or "").replace(",", "").replace("원", "").strip()
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None


# ---------- 파일 읽기 ----------
def _read_csv(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return [row for row in csv.reader(io.StringIO(text))]


class _TableParser:
    """HTML 표(카드사가 .xls 이름으로 주는 것)를 줄 목록으로."""

    def __init__(self, text: str):
        from html.parser import HTMLParser

        rows: list[list[str]] = []
        state: dict[str, Any] = {"row": None, "cell": None}

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    state["row"] = []
                elif tag in ("td", "th"):
                    state["cell"] = []

            def handle_endtag(self, tag):
                if tag in ("td", "th") and state["cell"] is not None and state["row"] is not None:
                    state["row"].append(" ".join("".join(state["cell"]).split()))
                    state["cell"] = None
                elif tag == "tr" and state["row"] is not None:
                    rows.append(state["row"])
                    state["row"] = None

            def handle_data(self, data):
                if state["cell"] is not None:
                    state["cell"].append(data)

        P().feed(text)
        self.rows = rows


def read_rows(path: Path) -> list[list[Any]]:
    head = path.read_bytes()[:512]
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv(path)
    if head.startswith(b"PK"):  # xlsx
        try:
            from openpyxl import load_workbook
        except ImportError as err:  # pragma: no cover
            raise ValueError("엑셀(.xlsx)을 읽으려면 openpyxl 이 필요합니다: uv sync") from err
        wb = load_workbook(path, read_only=True, data_only=True)
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        wb.close()
        return rows
    if head.lstrip().lower().startswith((b"<", b"\xef\xbb\xbf<")):
        for enc in ("utf-8", "cp949"):
            try:
                return _TableParser(path.read_bytes().decode(enc)).rows
            except UnicodeDecodeError:
                continue
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # 옛 엑셀 (BIFF)
        try:
            import xlrd  # type: ignore[import-not-found]
        except ImportError as err:
            raise ValueError(f"{path.name}: 옛 엑셀(.xls) 형식입니다. 엑셀에서 .xlsx 나 CSV 로 다시 저장해 올려 주세요.") from err
        sh = xlrd.open_workbook(str(path)).sheet_by_index(0)
        return [sh.row_values(i) for i in range(sh.nrows)]
    return _read_csv(path)


def _payment(dt: datetime, merchant: str, amount: int, category: str | None, channel: str, masker: Masker,
             n: int = 0) -> Record:
    merchant = masker.mask(merchant.strip()) or merchant.strip()
    meta: dict[str, Any] = {"merchant": merchant, "amount": amount, "channel": channel}
    if category:
        meta["category"] = category
    return Record(
        id=record_id("card", channel, iso(dt), merchant, amount, n),
        ts=iso(dt), source="card", kind="payment", modality="text",
        text=f"결제: {merchant} {amount:,}원", author="나", is_self=True, place=merchant, meta=meta,
    )


def parse_statement(path: Path, masker: Masker, window: Window, info: Counter) -> list[Record]:
    rows = read_rows(path)
    found = find_header(rows)
    if not found:
        info["no_header"] += 1
        return []
    start, cols = found
    out: list[Record] = []
    seen: Counter[tuple] = Counter()
    for row in rows[start + 1:]:
        cell = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None  # noqa: E731
        dt = parse_when(cell("date"), cell("time"))
        amount = parse_amount(cell("amount"))
        merchant = str(cell("merchant") or "").strip()
        if dt is None or amount is None or not merchant:
            info["not_payment"] += 1  # 합계·빈 줄
            continue
        if "취소" in str(cell("cancel") or "") and amount > 0:
            amount = -amount
        if not window.ok(dt):
            continue
        cat = str(cell("category") or "").strip() or None
        key = (iso(dt), merchant, amount)
        out.append(_payment(dt, merchant, amount, cat, "statement", masker, seen[key]))
        seen[key] += 1
    return out


def import_statements(root: Path, masker: Masker, window: Window | None = None) -> tuple[list[Record], dict[str, Any]]:
    window = window or Window()
    info: Counter = Counter()
    recs: list[Record] = []
    with opened(root) as base:
        files = find(base, *STATEMENT_FILES)
        for f in files:
            recs += parse_statement(f, masker, window, info)
    recs = list({r.id: r for r in recs}.values())
    return recs, {"files": len(files), "payments": len(recs), "cancelled": sum(1 for r in recs if r.meta["amount"] < 0),
                  "no_header": info["no_header"], "out_of_range": window.skipped}


# ---------- 승인 문자 ----------
CARD_BRAND = r"(?:신한|삼성|현대|KB국민|KB|국민|롯데|우리|하나|BC|비씨|NH|농협|씨티|IBK|기업)"
SMS_HEAD = re.compile(CARD_BRAND + r"[^\n]{0,24}?(?P<kind>승인취소|취소|승인)")
SMS_AMOUNT = re.compile(r"(?P<amt>\d{1,3}(?:,\d{3})+|\d+)원")
SMS_WHEN = re.compile(r"(?P<mo>\d{1,2})/(?P<d>\d{1,2})\s*(?P<h>\d{1,2}):(?P<mi>\d{2})")
SMS_TAIL = re.compile(r"(누적|잔액|총누적|사용가능|일시불|할부|\d+개월).*$", re.S)
CARD_DIGITS = re.compile(r"\d[\d*]{3,}")


def parse_card_sms(text: str, received: datetime, masker: Masker | None = None) -> Record | None:
    """카드 승인·취소 문자 한 통 → 결제 기록. 승인 문자가 아니면 None.

    문자에 연도가 없으므로 받은 시각의 연도를 쓰고, 받은 달보다 뒤 달이면 지난해로 본다(1월에 받은 12월 승인).
    """
    body = re.sub(r"\[(?:Web발신|국외발신|국제발신)\]", " ", text or "")
    head = SMS_HEAD.search(body)
    if not head:
        return None
    rest = body[head.end():]
    amt = SMS_AMOUNT.search(rest)
    when = SMS_WHEN.search(rest)
    if not amt or not when:
        return None
    mo, d = int(when.group("mo")), int(when.group("d"))
    year = received.year - (1 if mo > received.month + 1 else 0)
    try:
        dt = datetime(year, mo, d, int(when.group("h")), int(when.group("mi")), tzinfo=received.tzinfo or ZoneInfo(DEFAULT_TZ))
    except ValueError:
        return None
    # 가맹점은 시각 뒤에 온다. 누적·잔액 안내 앞에서 끊는다. 시각 앞뒤 순서가 다른 문자는 금액 줄 다음 줄을 쓴다
    tail = rest[when.end():]
    merchant = SMS_TAIL.sub("", tail).strip().split("\n")[0].strip()
    if not merchant:
        lines = [ln.strip() for ln in rest[amt.end():when.start()].split("\n") if ln.strip()]
        merchant = SMS_TAIL.sub("", lines[-1]).strip() if lines else ""
    merchant = CARD_DIGITS.sub("", merchant).strip(" ,/")
    if not merchant:
        return None
    amount = int(amt.group("amt").replace(",", ""))
    if "취소" in head.group("kind"):
        amount = -amount
    return _payment(dt, merchant, amount, None, "sms", masker or Masker({}))
