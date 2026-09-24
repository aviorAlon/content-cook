"""上传按内容嗅探（``webui/uploads.py``）与参考图列表去重（发 tag 前的加固）：

* 图片集参考图 / 单条视频首帧 / 绘本定妆与场景锚 / 配乐参考图：字节不是图片就 400，落盘后缀按真实格式而不是文件名；
  绘本 BGM / 翻页音效按文件头验音频；
* 后处理读不出图片时，错误只带文件名，不把本机绝对路径给用户；
* 同一张参考图重复上传只记一条（文件层早就按内容摘要幂等，列表层以前没跟上，白占 6 / 10 张的名额）；
  按内容摘要认「同一张」，换了文件名再传也算。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from imageset import post as PO
from music import spec as MS
from tests.picturebook_fixtures import example_data
from tests.test_clip_spec import sample as clip_sample
from tests.test_imageset_router import body as set_body
from webui import deps, tasks, uploads
from webui.app import app
from webui.routers import clip as clip_router
from webui.routers import imageset as imageset_router
from webui.routers import music as music_router
from webui.routers import picturebook as pb_router

client = TestClient(app)

WAV = b"RIFF" + b"\x00\x00\x00\x24" + b"WAVEfmt " + b"\x00" * 40
RF64 = b"RF64" + b"\xff\xff\xff\xff" + b"WAVEds64" + b"\x00" * 40
MP3 = b"ID3" + b"\x00" * 64


def image_bytes(fmt: str, color=(200, 120, 90), size=(24, 24)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def mpo_bytes() -> bytes:
    """相机 / 手机直出那种 JPEG：APP2 里带 MPF 索引、不止一帧（主图 + 预览图），PIL 认成 MPO。"""

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (200, 120, 90)).save(buf, "MPO", save_all=True, append_images=[Image.new("RGB", (12, 12), (1, 2, 3))])
    data = buf.getvalue()
    assert data[:3] == b"\xff\xd8\xff"                        # 文件头就是标准 JPEG
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "MPO"                           # 样本确实会被 PIL 认成 MPO，否则测不到这条路
    return data


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    for router in (imageset_router, clip_router, pb_router, music_router):
        monkeypatch.setattr(router, "PROVIDER_FACTORY", None)
    for sub in ("sticker", "portrait", "image", "clip", "picturebook", "music"):
        (tmp_path / "content" / sub).mkdir(parents=True)
    tasks._running.clear()
    return tmp_path


def _sticker() -> str:
    r = client.post("/api/sticker", json=set_body("sticker"))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _clip() -> str:
    r = client.post("/api/clip", json=clip_sample())
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _book() -> tuple[str, str]:
    r = client.post("/api/picturebook", json=example_data())
    assert r.status_code == 200, r.text
    return r.json()["id"], next(iter(r.json()["spec"]["scenes"]))


def _music() -> str:
    r = client.post("/api/music", json={"title": "睡前摇篮曲", "brief": {"usage": "睡前底噪"}, "duration": 60, "count": 1})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------- 嗅探本身


def test_sniff_image_and_audio():
    assert uploads.sniff_image(image_bytes("PNG")) == (".png", 24, 24)
    assert uploads.sniff_image(image_bytes("JPEG"))[0] == ".jpg"
    assert uploads.sniff_image(image_bytes("WEBP"))[0] == ".webp"
    for junk in (b"this is not a png", b"junk", b""):
        with pytest.raises(uploads.UploadContentError, match="不是可读的图片文件"):
            uploads.sniff_image(junk)
    with pytest.raises(uploads.UploadContentError, match="只接受 PNG / JPEG / WebP，收到的是 GIF"):
        uploads.sniff_image(image_bytes("GIF"))
    with pytest.raises(uploads.UploadContentError, match="只接受 PNG / JPEG，收到的是 WEBP") as info:
        uploads.sniff_image(image_bytes("WEBP"), allowed={"PNG": ".png", "JPEG": ".jpg"})
    assert info.value.detected == "WEBP"
    assert uploads.sniff_audio(MP3) == ".mp3" and uploads.sniff_audio(b"\xff\xfb\x90\x00" + b"\x00" * 8) == ".mp3"
    assert uploads.sniff_audio(WAV) == ".wav" and uploads.sniff_audio(b"fLaC\x00") == ".flac"
    assert uploads.sniff_audio(b"OggS\x00") == ".ogg" and uploads.sniff_audio(b"\x00\x00\x00\x18ftypM4A ") == ".m4a"
    assert uploads.sniff_audio(b"\xff\xf1\x50\x80" + b"\x00" * 8) == ".aac"
    # RF64 / BW64：大文件、BWF 用的 WAV 变体（ffmpeg -rf64 always 写出来的就是 RF64 + 0xFFFFFFFF + WAVE），ffmpeg 照读，不能拒
    assert uploads.sniff_audio(RF64) == ".wav" and uploads.sniff_audio(b"BW64\xff\xff\xff\xffWAVEds64") == ".wav"
    with pytest.raises(uploads.UploadContentError, match="不是可用的音频文件"):
        uploads.sniff_audio(b"not audio at all")
    with pytest.raises(uploads.UploadContentError, match="不是可用的音频文件"):
        uploads.sniff_audio(b"RF64\xff\xff\xff\xffAVI ")              # 头对了一半不算


# ---------------------------------------------------------------------------- 图片集参考图


def test_sticker_asset_rejects_non_image_content(project):
    spec_id = _sticker()
    r = client.post(f"/api/sticker/{spec_id}/assets", json={"filename": "x.png", "data_base64": b64(b"this is not a png")})
    assert r.status_code == 400 and "不是可读的图片文件" in r.json()["detail"]
    assert not (project / "content" / "assets" / spec_id).exists()
    assert client.get(f"/api/sticker/{spec_id}").json()["assets"] == []
    gif = client.post(f"/api/sticker/{spec_id}/assets", json={"filename": "x.png", "data_base64": b64(image_bytes("GIF"))})
    assert gif.status_code == 400 and gif.json()["detail"] == "只接受 PNG / JPEG / WebP，收到的是 GIF"


def test_sticker_asset_suffix_follows_real_format(project):
    spec_id = _sticker()
    r = client.post(f"/api/sticker/{spec_id}/assets", json={"filename": "photo.jpg", "data_base64": b64(image_bytes("PNG"))})
    assert r.status_code == 200, r.text
    assert r.json()["file"].endswith(".png") and (project / r.json()["file"]).is_file()
    assert client.get(f"/api/sticker/{spec_id}").json()["spec"]["subject"]["assets"] == [r.json()["file"]]


def test_sticker_duplicate_upload_is_recorded_once(project):
    spec_id = _sticker()
    payload = {"filename": "ref.png", "data_base64": b64(image_bytes("PNG"))}
    first = client.post(f"/api/sticker/{spec_id}/assets", json=payload)
    again = client.post(f"/api/sticker/{spec_id}/assets", json=payload)
    assert first.status_code == again.status_code == 200
    assert first.json()["existing"] is False and again.json()["existing"] is True
    assert again.json()["file"] == first.json()["file"]
    detail = client.get(f"/api/sticker/{spec_id}").json()
    assert [a["file"] for a in detail["assets"]] == [first.json()["file"]]
    assert detail["spec"]["subject"]["assets"] == [first.json()["file"]]
    # 名额按不同的图算：同一张再传 6 次也不占位，第二张不同的图照样能进
    for _ in range(6):
        assert client.post(f"/api/sticker/{spec_id}/assets", json=payload).status_code == 200
    other = client.post(f"/api/sticker/{spec_id}/assets", json={"filename": "ref.png", "data_base64": b64(image_bytes("PNG", (1, 2, 3)))})
    assert other.status_code == 200, other.text
    assert len(client.get(f"/api/sticker/{spec_id}").json()["assets"]) == 2


def test_sticker_same_content_under_another_name_is_recorded_once(project):
    """同一段字节换个文件名（Windows 重复下载的「photo (1).png」、改过名的副本）再传：沿用原来那条，不另写文件、不再占名额。"""

    spec_id = _sticker()
    same = b64(image_bytes("PNG", (33, 44, 55)))
    first = client.post(f"/api/sticker/{spec_id}/assets", json={"filename": "photo.png", "data_base64": same})
    assert first.status_code == 200 and first.json()["existing"] is False
    for name in ("photo (1).png", "IMG_0001.png", "renamed.jpg"):
        r = client.post(f"/api/sticker/{spec_id}/assets", json={"filename": name, "data_base64": same})
        assert r.status_code == 200, r.text
        assert r.json()["existing"] is True and r.json()["file"] == first.json()["file"]
        assert r.json()["url"] == first.json()["url"]
    assert [a["file"] for a in client.get(f"/api/sticker/{spec_id}").json()["assets"]] == [first.json()["file"]]
    assert len(list((project / "content" / "assets" / spec_id).iterdir())) == 1                # 也没多写一份文件


def test_same_content_index_matches_digest_in_the_same_folder_only():
    paths = ["content/assets/A/photo-436c47cc.png", "content/assets/B/other-436c47cc.png", 7, None,
             "content/assets/A/old-deadbeef.jpg"]
    assert uploads.same_content_index(paths, "content/assets/A", "436c47cc") == 0
    assert uploads.same_content_index(paths, "content/assets/B", "436c47cc") == 1
    assert uploads.same_content_index(paths, "content/assets/C", "436c47cc") is None             # 别的项目目录里的不算
    assert uploads.same_content_index(paths, "content/assets/A", "deadbeef") == 4                # 后缀不参与比较
    assert uploads.same_content_index(["content/assets/A/photo-436c47cc-deadbeef.png"], "content/assets/A", "436c47cc") is None


def test_wedding_same_photo_with_new_who_updates_the_entry(project):
    spec_id = client.post("/api/portrait", json={"title": "婚纱", "template": "wedding", "subject": {"kind": "photo", "fidelity": 90},
                                                 "options": {"scenes": ["教堂"]}}).json()["id"]
    photo = b64(image_bytes("PNG"))
    assert client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "p.png", "data_base64": photo, "who": "bride"}).status_code == 200
    r = client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "p.png", "data_base64": photo, "who": "groom"})
    assert r.status_code == 200 and r.json()["existing"] is True
    assets = client.get(f"/api/portrait/{spec_id}").json()["assets"]
    assert len(assets) == 1 and assets[0]["who"] == "groom"
    # 不带 who 再传同一张：原条目不动，已标的 who 不被抹掉
    r = client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "p.png", "data_base64": photo})
    assert r.status_code == 200 and r.json()["existing"] is True
    assets = client.get(f"/api/portrait/{spec_id}").json()["assets"]
    assert len(assets) == 1 and assets[0]["who"] == "groom"


# ---------------------------------------------------------------------------- 单条视频首帧


def test_clip_first_frame_rejects_non_image_content(project):
    clip_id = _clip()
    r = client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "upload", "filename": "f.png", "data_base64": b64(b"junk")})
    assert r.status_code == 400 and "不是可读的图片文件" in r.json()["detail"]
    detail = client.get(f"/api/clip/{clip_id}").json()
    assert detail["spec"]["first_frame"]["asset"] is None
    assert not list((project / "output" / "clip" / clip_id).rglob("*.png"))   # junk 也没落进 00_frame/
    ok = client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "upload", "filename": "f.jpg", "data_base64": b64(image_bytes("PNG"))})
    assert ok.status_code == 200, ok.text
    assert ok.json()["file"].endswith(".png") and ok.json()["first_frame"]["asset"].endswith(".png")


# ---------------------------------------------------------------------------- 绘本定妆 / 场景 / BGM / 音效


def test_picturebook_uploads_follow_real_content(project, monkeypatch):
    # 这一条测的是「本机没有 ffprobe」时的退路：只看文件头（样本是只有文件头的假音频）。有 ffprobe 时的按内容认见下面几条
    monkeypatch.setattr(uploads, "_tool", lambda name: None)
    book_id, scene = _book()
    r = client.post(f"/api/picturebook/{book_id}/assets/scene/{scene}", json={"filename": "s.jpg", "data_base64": b64(image_bytes("PNG"))})
    assert r.status_code == 200, r.text
    assert r.json()["file"].endswith(".png")
    bad = client.post(f"/api/picturebook/{book_id}/assets/scene/{scene}", json={"filename": "s.png", "data_base64": b64(b"nope")})
    assert bad.status_code == 400 and "不是可读的图片文件" in bad.json()["detail"]
    noise = client.post(f"/api/picturebook/{book_id}/assets/music/x", json={"filename": "a.mp3", "data_base64": b64(b"not audio at all")})
    assert noise.status_code == 400 and "不是可用的音频文件" in noise.json()["detail"]
    assert not (client.get(f"/api/picturebook/{book_id}").json()["spec"].get("music") or {}).get("file")   # 没写进 music.file
    assert not (project / "content" / "assets" / book_id / "music").exists()                              # 也没落盘
    wav = client.post(f"/api/picturebook/{book_id}/assets/sfx/flip", json={"filename": "flip.mp3", "data_base64": b64(WAV)})
    assert wav.status_code == 200, wav.text
    assert wav.json()["file"].endswith(".wav")
    assert client.get(f"/api/picturebook/{book_id}").json()["spec"]["page_turn_sfx"] == wav.json()["file"]
    rf64 = client.post(f"/api/picturebook/{book_id}/assets/music/x", json={"filename": "r.wav", "data_base64": b64(RF64)})
    assert rf64.status_code == 200, rf64.text                                   # RF64 的 BGM 不被当成「认不出格式」
    assert rf64.json()["file"].endswith(".wav")


# ---------------------------------------------------------------------------- 配乐参考图


def test_music_reference_rejects_non_image_content_and_dedupes(project):
    spec_id = _music()
    junk = client.post(f"/api/music/{spec_id}/references", json={"filename": "x.png", "data_base64": b64(b"nope")})
    assert junk.status_code == 400 and "不是可读的图片文件" in junk.json()["detail"]
    webp = client.post(f"/api/music/{spec_id}/references", json={"filename": "x.png", "data_base64": b64(image_bytes("WEBP"))})
    assert webp.status_code == 400 and webp.json()["detail"] == "参考图只接受 jpg / png，收到的是 WEBP"
    assert client.get(f"/api/music/{spec_id}").json()["references"] == []

    payload = {"filename": "ref.jpg", "data_base64": b64(image_bytes("PNG"))}
    first = client.post(f"/api/music/{spec_id}/references", json=payload)
    again = client.post(f"/api/music/{spec_id}/references", json=payload)
    assert first.status_code == again.status_code == 200
    assert first.json()["file"].endswith(".png")                                 # 后缀按内容
    assert first.json()["existing"] is False and again.json()["existing"] is True
    detail = client.get(f"/api/music/{spec_id}").json()
    assert [r["file"] for r in detail["references"]] == [first.json()["file"]]
    assert detail["spec"]["reference_images"] == [first.json()["file"]]
    for _ in range(10):
        assert client.post(f"/api/music/{spec_id}/references", json=payload).status_code == 200
    other = client.post(f"/api/music/{spec_id}/references", json={"filename": "ref.png", "data_base64": b64(image_bytes("PNG", (1, 2, 3)))})
    assert other.status_code == 200, other.text
    assert len(client.get(f"/api/music/{spec_id}").json()["references"]) == 2
    # 同内容换个文件名再传：沿用原来那条，不再多记
    copy = client.post(f"/api/music/{spec_id}/references", json={"filename": "ref-copy.png", "data_base64": payload["data_base64"]})
    assert copy.status_code == 200, copy.text
    assert copy.json()["existing"] is True and copy.json()["file"] == first.json()["file"]
    assert [r["file"] for r in client.get(f"/api/music/{spec_id}").json()["references"]] == [first.json()["file"], other.json()["file"]]


# ---------------------------------------------------------------------------- 相机直出的 JPEG（PIL 认成 MPO）


def test_camera_jpeg_with_mpf_index_is_accepted_as_jpeg(project):
    """PIL 把带 MPF 索引的 JPEG 认成 MPO：只按 PNG / JPEG / WebP 收的话，这类最常见的相机原片会被 400 拒掉，
    提示还是用户看不懂的「收到的是 MPO」。四处图片上传都要当 JPEG 收、存成 .jpg。"""

    photo = b64(mpo_bytes())
    assert uploads.sniff_image(mpo_bytes()) == (".jpg", 24, 24)
    assert uploads.sniff_image(mpo_bytes(), allowed={"PNG": ".png", "JPEG": ".jpg"})[0] == ".jpg"

    spec_id = _sticker()
    r = client.post(f"/api/sticker/{spec_id}/assets", json={"filename": "dslr.jpg", "data_base64": photo})
    assert r.status_code == 200, r.text
    assert r.json()["file"].endswith(".jpg") and (project / r.json()["file"]).is_file()

    clip_id = _clip()
    r = client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "upload", "filename": "dslr.jpg", "data_base64": photo})
    assert r.status_code == 200, r.text
    assert r.json()["first_frame"]["asset"].endswith(".jpg")

    book_id, scene = _book()
    r = client.post(f"/api/picturebook/{book_id}/assets/scene/{scene}", json={"filename": "dslr.jpg", "data_base64": photo})
    assert r.status_code == 200, r.text
    assert r.json()["file"].endswith(".jpg")
    character = next(iter(client.get(f"/api/picturebook/{book_id}").json()["spec"]["characters"]))
    r = client.post(f"/api/picturebook/{book_id}/assets/character/{character}", json={"filename": "dslr.jpeg", "data_base64": photo})
    assert r.status_code == 200, r.text
    assert r.json()["file"].endswith(".jpg")

    music_id = _music()
    r = client.post(f"/api/music/{music_id}/references", json={"filename": "dslr.jpg", "data_base64": photo})
    assert r.status_code == 200, r.text
    assert r.json()["file"].endswith(".jpg")


def test_music_reference_paths_dedupe_hand_edited_duplicates(project):
    spec_id = _music()
    ok = client.post(f"/api/music/{spec_id}/references", json={"filename": "ref.png", "data_base64": b64(image_bytes("PNG"))})
    spec = MS.load(spec_id, project_root=project)
    spec["reference_images"] = [ok.json()["file"], ok.json()["file"]]
    assert len(MS.reference_paths(spec)) == 1


# ---------------------------------------------------------------------------- 后处理的错误文案


def test_post_error_does_not_leak_absolute_path(tmp_path):
    fake = tmp_path / "01_raw" / "I01-9.png"
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"this is not a png")
    with pytest.raises(PO.PostError) as info:
        PO.open_rgba(fake)
    message = str(info.value)
    assert "I01-9.png" in message and str(tmp_path) not in message
    assert message == "图片读不出来：I01-9.png（UnidentifiedImageError）"
    assert Path(message.split("：", 1)[1].split("（")[0]).name == "I01-9.png"
