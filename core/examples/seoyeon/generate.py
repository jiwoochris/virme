"""가상 인물 윤서연의 90일 기록을 LLM 으로 만든다 (한 번 만들어 커밋해 두었으므로 다시 돌릴 필요는 없다).

실제 인물이 아니다. 주차별 줄거리(ARC)를 정해 두고 그 안에서 대화·게시물·사진 설명을 쓰게 했다.
줄거리를 알고 있으므로 변화점·주제별 근거 등급이 맞게 나오는지 채점할 수 있다 (story.md).

    OPENROUTER_API_KEY=... uv run python examples/seoyeon/generate.py
    → raw_weeks.json  (build_exports.py 가 이것을 카카오톡·인스타그램 내보내기 형식으로 바꾼다)
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from virtueme_core.llm import chat_json  # noqa: E402

MODEL = "anthropic/claude-sonnet-4.5"
START = date(2026, 6, 1)

CAST = """등장인물 (모두 가상 인물)
- 윤서연: 29세, 서울의 중견 화장품 회사 브랜드 마케터 3년차. 5월 말 회사 근처 원룸으로 이사해 처음 혼자 산다. 말투는 담백하고 가끔 ㅋㅋ, ㅠㅠ 를 쓴다.
- 김민지, 박지훈, 이수아: 대학 동기. 카카오톡 단톡방 "대학친구들". 민지는 수다스럽고, 지훈은 늘 바쁜 개발자, 수아는 제주 출신이다.
- 엄마: 대전에 산다. 1:1 카카오톡 방 이름 "엄마". 반찬을 자주 보내고 건강 걱정을 한다.
- 정우진: 회사 입사 동기. 1:1 카카오톡. 러닝 크루를 먼저 하고 있다.
- 한하은: 7월에 러닝 크루에서 만난 사람. 인스타그램 DM 으로 대화한다."""

ARC = [
    "캠페인 준비로 야근이 시작된다. 이사 온 원룸이 아직 낯설고 조용하다. 엄마는 반찬 걱정, 단톡방 친구들은 다들 바쁘다.",
    "캠페인 마감 주간. 야근이 연속되고 저녁은 편의점이나 배달로 혼자 먹는다. 주말에 아무에게도 연락이 오지 않아 외롭다.",
    "번아웃 조짐. 새벽까지 잠이 안 온다. 엄마 전화를 짧게 끊고 미안해한다. 친구 약속을 피곤해서 취소한다.",
    "장마가 시작된다. 퇴근길이 쓸쓸하고 무기력하다. 회사 동기 정우진에게 푸념한다. 우진이 주말에 러닝을 같이 하자고 권한다.",
    "처음으로 러닝 크루에 나가 한강에서 3km 를 뛴다. 힘들고 근육통이 오지만 끝나고 개운했다. 두 번째 러닝도 나간다.",
    "주 3회 한강 야간 러닝이 습관이 된다. 잠이 잘 오고 기분이 확실히 좋아진다. 크루에서 한하은과 인스타 맞팔을 한다.",
    "러닝 크루 사람들과 뒤풀이. 처음으로 5km 를 쉬지 않고 완주한다. 하은과 DM 으로 러닝 코스와 운동화 얘기를 한다.",
    "퇴근길 동네에서 떠돌던 강아지를 보호소에 연락했다가 임시보호를 맡는다. 이름을 콩이로 짓는다. 산책과 사진이 늘고 웃음이 많아진다.",
    "콩이와 아침 산책. 엄마가 서울에 올라와 같이 저녁을 먹고 콩이를 예뻐한다. 일도 안정된다.",
    "여름휴가 준비. 단톡방에서 수아 고향 제주로 가자는 얘기가 나오고 일정을 짠다. 콩이는 우진이 맡아 주기로 한다.",
    "8월 11일~14일 제주 여행. 바다, 오름, 흑돼지, 해녀의 집, 비 오는 날 카페. 사진을 많이 찍고 친구들과 오래 이야기한다.",
    "콩이 정식 입양을 결정한다. 8월 말 10km 러닝 대회에 신청한다. 회사 프로젝트로 팀장에게 칭찬받는다.",
    "8월 29일 토요일 10km 대회를 완주한다. 석 달을 돌아보며 고마운 사람들에게 연락한다.",
]

SYSTEM = f"""너는 한국어 합성 데이터 작가다. 가상 인물의 휴대폰 기록을 현실감 있게 쓴다.
{CAST}

규칙
- 실제 사람처럼 짧게 끊어 보내는 카톡 말투. 한 사람이 연달아 여러 줄 보내기도 한다. 오타·줄임말·ㅋㅋ·ㅠㅠ·이모티콘 대신 "(이모티콘)" 같은 표기도 가끔.
- 줄거리에 나온 감정을 직접 설명하지 말고 대화와 행동으로 드러낸다. 감정 단어를 쓰는 날도 있고 안 쓰는 날도 있다.
- 줄거리와 상관없는 일상 잡담(택배, 날씨, 점심 메뉴, 드라마)도 30% 정도 섞는다.
- 시간은 24시간제 HH:MM, 날짜는 YYYY-MM-DD. 날짜는 주어진 주 안에서만 쓴다. 평일 낮에는 회사라 메시지가 적다.
- 실존 인물·실제 연락처·주소·계좌를 쓰지 않는다. 가게 이름은 지어낸다.
- JSON 하나만 출력한다."""

SCHEMA = """출력 형식
{"kakao":[{"room":"대학친구들|엄마|정우진","date":"YYYY-MM-DD","time":"HH:MM","sender":"이름","text":"한 줄 메시지"}],
 "instagram_posts":[{"date":"...","time":"...","caption":"게시물 글 (해시태그 포함 가능)","photo_query":"이 사진을 위키미디어에서 찾을 영어 검색어","place":"장소 이름 또는 빈 문자열"}],
 "instagram_stories":[{"date":"...","time":"...","caption":"짧은 글 또는 빈 문자열","photo_query":"영어 검색어","place":"..."}],
 "instagram_dm":[{"date":"...","time":"...","sender":"윤서연|한하은","text":"..."}],
 "photos":[{"date":"...","time":"...","photo_query":"영어 검색어","place":"장소 이름 또는 빈 문자열"}]}
분량: kakao 40~55개(세 방에 나눠서, 엄마 방은 적게), instagram_posts 1~2개, instagram_stories 1~3개, instagram_dm 0~10개(7주차부터), photos 2~4개.
카카오톡 sender 는 room 참여자만 쓴다: 대학친구들=윤서연·김민지·박지훈·이수아, 엄마=윤서연·엄마, 정우진=윤서연·정우진."""


def week(i: int) -> dict:
    d0 = START + timedelta(days=7 * i)
    d1 = min(d0 + timedelta(days=6), date(2026, 8, 29))
    user = (f"{i + 1}주차 ({d0.isoformat()} ~ {d1.isoformat()}) 줄거리: {ARC[i]}\n"
            f"이전 주 줄거리: {ARC[i - 1] if i else '없음 (시작)'}\n\n{SCHEMA}")
    for _ in range(3):
        try:
            data = chat_json(SYSTEM, user, model=MODEL, temperature=0.8, max_tokens=12000, timeout=240)
            if isinstance(data, dict) and data.get("kakao"):
                data["week"] = i + 1
                data["range"] = [d0.isoformat(), d1.isoformat()]
                return data
        except Exception as err:  # noqa: BLE001
            print(f"{i + 1}주차 재시도: {err}", file=sys.stderr)
    raise RuntimeError(f"{i + 1}주차 생성 실패")


def main() -> None:
    with ThreadPoolExecutor(max_workers=5) as pool:
        weeks = list(pool.map(week, range(len(ARC))))
    (HERE / "raw_weeks.json").write_text(json.dumps(weeks, ensure_ascii=False, indent=1), encoding="utf-8")
    print({"weeks": len(weeks), "kakao": sum(len(w["kakao"]) for w in weeks),
           "posts": sum(len(w.get("instagram_posts", [])) for w in weeks),
           "stories": sum(len(w.get("instagram_stories", [])) for w in weeks),
           "dm": sum(len(w.get("instagram_dm", [])) for w in weeks),
           "photos": sum(len(w.get("photos", [])) for w in weeks)})


if __name__ == "__main__":
    main()
