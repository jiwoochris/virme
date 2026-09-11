import json
from datetime import datetime

from PIL import Image

from virtueme_core.ingest import instagram, photos
from virtueme_core.privacy import Pseudonymizer
from virtueme_core.store import Store


def mojibake(s: str) -> str:
    """인스타그램 내보내기가 한글을 적는 방식 (UTF-8 바이트를 latin-1 글자로)."""
    return s.encode("utf-8").decode("latin-1")


def write_export(root):
    content = root / "your_instagram_activity" / "content"
    content.mkdir(parents=True)
    media = root / "media" / "posts" / "202607"
    media.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(media / "a.jpg")
    posts = [
        {"media": [{"uri": "media/posts/202607/a.jpg", "creation_timestamp": 1783515600,
                    "title": mojibake("한강 러닝 5km 완주 #러닝"),
                    "media_metadata": {"photo_metadata": {"exif_data": [{"latitude": 37.528, "longitude": 126.934}]}}}]},
        {"media": [{"uri": "media/posts/202607/missing.jpg", "creation_timestamp": 1783602000, "title": ""}]},
    ]
    (content / "posts_1.json").write_text(json.dumps(posts), encoding="utf-8")
    (content / "stories.json").write_text(json.dumps({"ig_stories": [
        {"uri": "media/posts/202607/a.jpg", "creation_timestamp": 1783688400, "title": mojibake("콩이 산책")}]}),
        encoding="utf-8")
    info = root / "personal_information" / "personal_information"
    info.mkdir(parents=True)
    (info / "personal_information.json").write_text(json.dumps({"profile_user": [
        {"string_map_data": {"Name": {"value": mojibake("윤서연")}}}]}), encoding="utf-8")
    inbox = root / "your_instagram_activity" / "messages" / "inbox" / "haeun_123"
    inbox.mkdir(parents=True)
    (inbox / "message_1.json").write_text(json.dumps({
        "participants": [{"name": mojibake("한하은")}, {"name": mojibake("윤서연")}],
        "title": mojibake("한하은"),
        "messages": [  # 최신이 먼저
            {"sender_name": mojibake("윤서연"), "timestamp_ms": 1783700060000, "content": mojibake("하은님 운동화 뭐 신어요?")},
            {"sender_name": mojibake("윤서연"), "timestamp_ms": 1783700000000, "content": mojibake("오늘 코스 좋았어요")},
            {"sender_name": mojibake("한하은"), "timestamp_ms": 1783699000000, "content": mojibake("서연님 수고했어요!")},
        ]}), encoding="utf-8")


def test_instagram_export(tmp_path):
    root = tmp_path / "ig"
    write_export(root)
    store = Store(tmp_path / "t.db")
    recs, info = instagram.import_instagram(root, Pseudonymizer(store))
    assert info["owner_found"] and info["posts"] == 1 and info["stories"] == 1
    post = next(r for r in recs if r.kind == "post")
    assert post.text == "한강 러닝 5km 완주 #러닝"  # latin-1 깨짐 복구
    assert post.modality == "text+image" and post.lat == 37.528
    dms = [r for r in recs if r.kind == "message"]
    # 서연이 60초 간격으로 보낸 두 메시지는 한 발화, 시간순 정렬
    assert [r.author for r in dms] == ["지인A", "나"]
    # 성을 뗀 이름으로 부른 경우("하은님")도 가명으로 바뀐다
    assert dms[1].text == "오늘 코스 좋았어요\n지인A님 운동화 뭐 신어요?"
    assert dms[0].text == "(나)님 수고했어요!"


def test_photos_exif_time_gps_and_duplicates(tmp_path):
    folder = tmp_path / "pics"
    folder.mkdir()
    img = Image.new("RGB", (16, 16), "red")
    exif = img.getexif()
    exif.get_ifd(0x8769)[0x9003] = "2026:08:12 15:30:00"
    exif.get_ifd(0x8769)[0x9011] = "+09:00"
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (33.0, 27.0, 36.0), 3: "E", 4: (126.0, 55.0, 12.0)})
    img.save(folder / "jeju.jpg", exif=exif)
    img.save(folder / "copy.jpg", exif=exif)  # 같은 내용 → 중복
    Image.new("RGB", (16, 16), "green").save(folder / "noexif.png")
    recs, info = photos.import_photos(folder)
    assert info["records"] == 2 and info["skipped"]["duplicate"] == 1
    jeju = next(r for r in recs if r.meta["time_source"] == "exif")
    assert datetime.fromisoformat(jeju.ts) == datetime.fromisoformat("2026-08-12T15:30:00+09:00")
    assert abs(jeju.lat - 33.46) < 1e-3 and abs(jeju.lon - 126.92) < 1e-3
    assert any(r.meta["time_source"] == "mtime" for r in recs)
