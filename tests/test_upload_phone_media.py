"""发布前加固：上传按内容认——手机照片与各处音频。

* 手机照片：相机 / 手机直出、带 MPF 索引的 JPEG（PIL 认成 MPO）取第一帧另存为标准 JPEG（短剧定妆图以前直接 400
  「收到的是 MPO」）；带 EXIF 方向的竖拍照片把方向转进像素（以前原样存，模型按像素读就是横的）；HEIC 读不了说清
  「请在手机上导出为 JPG」（以前在扩展名那一关就拒，只说「只接受 png / jpg…」）。纪念写真的用户基本用手机传照片。
* 音频：播客音乐 / 短剧配乐 / 绘本 BGM / 音色参考音频不再按扩展名拒——ffprobe 读得出音频流且时长 > 0 就收，后缀按
  真实格式；AIFF 这类读得出、容器却不在范围内的转成 wav；图片 / 文本 / 0 时长给人话 400。没有 ffprobe 退回文件头。
"""

from __future__ import annotations

import base64
import io
import json
import math
import shutil
import struct
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.test_podcast_router import body as podcast_body
from webui import deps, i18n, uploads
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

client = TestClient(app)

# 只有文件头的 HEIC（iPhone 默认格式）：PIL 没装 HEIF 插件时认不出
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffprobe") and shutil.which("ffmpeg")), reason="本机没有 ffmpeg / ffprobe")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def mpo_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (200, 120, 90)).save(buf, "MPO", save_all=True, append_images=[Image.new("RGB", (12, 12), (1, 2, 3))])
    with Image.open(io.BytesIO(buf.getvalue())) as image:
        assert image.format == "MPO"
    return buf.getvalue()


def portrait_photo(orientation: int = 6, fmt: str = "JPEG") -> bytes:
    """手机竖拍的照片：像素按横的存（40×20，左半红、右半蓝），EXIF 写方向 6（看的时候顺时针转 90°）。"""

    image = Image.new("RGB", (40, 20), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, 20, 20))
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    image.save(buf, fmt, quality=95, exif=exif.tobytes())
    return buf.getvalue()


def assert_upright(data: bytes) -> None:
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG" and image.size == (20, 40)
        assert image.getexif().get(0x0112) in (None, 1)          # 方向已经转进像素，不再带着标记（否则看图软件会再转一次）
        top, bottom = image.convert("RGB").getpixel((10, 5)), image.convert("RGB").getpixel((10, 35))
    assert top[0] > 200 and top[2] < 60                           # 原来的左半边（红）转到了上面
    assert bottom[2] > 200 and bottom[0] < 60


def wav_bytes(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x10" * int(8000 * seconds))
    return buf.getvalue()


def aiff_bytes(seconds: float = 1.0, rate: int = 8000) -> bytes:
    """手写一段 AIFF（Mac 录音 / 导出常见）：FORM / COMM / SSND，16 位单声道。"""

    frames = int(rate * seconds)
    exponent = int(math.floor(math.log2(rate)))
    rate80 = struct.pack(">HQ", exponent + 16383, int(rate * (1 << (63 - exponent))))
    comm = struct.pack(">hLh", 1, frames, 16) + rate80
    ssnd = struct.pack(">LL", 0, 0) + b"\x10\x00" * frames
    chunks = b"AIFF" + b"COMM" + struct.pack(">L", len(comm)) + comm + b"SSND" + struct.pack(">L", len(ssnd)) + ssnd
    return b"FORM" + struct.pack(">L", len(chunks)) + chunks


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (9, 9, 9)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    for sub in ("portrait", "music", "podcast"):
        (tmp_path / "content" / sub).mkdir(parents=True)
    spec = {
        "id": "PHONE-1", "output_root": "output/story_video/PHONE-1", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。", "variants": {"rain": {"label": "淋雨", "delta": "湿发"}}}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []}],
    }
    (content_dir / "PHONE-1.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _portrait() -> str:
    r = client.post("/api/portrait", json={"title": "纪念写真", "template": "wedding", "subject": {"kind": "photo", "fidelity": 90},
                                           "options": {"scenes": ["教堂"]}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------- 手机照片


def test_mpo_is_stored_as_the_first_frame_in_standard_jpeg():
    accepted = uploads.accept_image(mpo_bytes())
    assert accepted.suffix == ".jpg" and (accepted.width, accepted.height) == (24, 24)
    with Image.open(io.BytesIO(accepted.data)) as image:
        assert image.format == "JPEG" and getattr(image, "n_frames", 1) == 1     # 不再是多帧的 MPO
        r, g, b = image.getpixel((12, 12))
    assert abs(r - 200) < 12 and abs(g - 120) < 12 and abs(b - 90) < 12            # 取的是主图（第一帧），不是预览图


def test_exif_orientation_is_baked_into_pixels_and_plain_photos_are_untouched():
    accepted = uploads.accept_image(portrait_photo(6))
    assert (accepted.width, accepted.height) == (20, 40)
    assert_upright(accepted.data)
    # 没有方向标记（或方向是 1）的照片原样收，不重新编码
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(buf, "JPEG")
    assert uploads.accept_image(buf.getvalue()).data == buf.getvalue()
    upright = portrait_photo(1)
    assert uploads.accept_image(upright).data == upright


def test_heic_that_pillow_cannot_read_says_export_as_jpg():
    with pytest.raises(uploads.UploadContentError) as info:
        uploads.accept_image(HEIC)
    assert str(info.value) == uploads.HEIF_HINT and "导出为 JPG" in str(info.value)
    assert info.value.detected == ""          # 调用方不会拿它拼成「只接受 …，收到的是 HEIC」


def test_drama_cast_anchor_accepts_phone_photos(project):
    """短剧定妆图（routers/project.py 的 _store_asset）以前自己用 PIL 验图：MPO 直接 400「收到的是 MPO」，竖拍照片原样存。"""

    r = client.post("/api/episodes/storyvideo/PHONE-1/cast/lin/asset", json={"filename": "IMG_0001.JPG", "data_base64": b64(mpo_bytes())})
    assert r.status_code == 200, r.text
    assert r.json()["asset"].endswith(".jpg") and (r.json()["width"], r.json()["height"]) == (24, 24)
    with Image.open(project / r.json()["asset"]) as image:
        assert image.format == "JPEG"
    r = client.post("/api/episodes/storyvideo/PHONE-1/cast/lin/variants/rain/asset",
                    json={"filename": "IMG_0002.JPG", "data_base64": b64(portrait_photo(6))})
    assert r.status_code == 200, r.text
    assert (r.json()["width"], r.json()["height"]) == (20, 40) and r.json()["bytes"] == (project / r.json()["asset"]).stat().st_size
    assert_upright((project / r.json()["asset"]).read_bytes())
    heic = client.post("/api/episodes/storyvideo/PHONE-1/cast/lin/asset", json={"filename": "IMG_0003.HEIC", "data_base64": b64(HEIC)})
    assert heic.status_code == 400 and heic.json()["detail"] == uploads.HEIF_HINT
    gif = io.BytesIO()
    Image.new("RGB", (4, 4)).save(gif, "GIF")
    bad = client.post("/api/episodes/storyvideo/PHONE-1/cast/lin/asset", json={"filename": "a.gif", "data_base64": b64(gif.getvalue())})
    assert bad.status_code == 400 and bad.json()["detail"] == "只接受 PNG / JPEG / WebP，收到的是 GIF"


def test_memorial_portrait_photos_from_a_phone(project):
    """纪念写真：竖拍照片存成竖的、同一张再传照样认得出（摘要按上传的原字节算）；HEIC 不在扩展名那一关被拒，给「导出为 JPG」。"""

    spec_id = _portrait()
    photo = b64(portrait_photo(6))
    first = client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "IMG_0001.jpg", "data_base64": photo, "who": "bride"})
    assert first.status_code == 200, first.text
    assert_upright((project / first.json()["file"]).read_bytes())
    again = client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "IMG_0001 (1).jpg", "data_base64": photo})
    assert again.status_code == 200 and again.json()["existing"] is True and again.json()["file"] == first.json()["file"]
    mpo = client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "DSC_0001.JPG", "data_base64": b64(mpo_bytes())})
    assert mpo.status_code == 200, mpo.text
    with Image.open(project / mpo.json()["file"]) as image:
        assert image.format == "JPEG"
    heic = client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "IMG_0002.HEIC", "data_base64": b64(HEIC)})
    assert heic.status_code == 400 and heic.json()["detail"] == uploads.HEIF_HINT
    assert len(client.get(f"/api/portrait/{spec_id}").json()["assets"]) == 2
    # 英文界面也说清怎么办
    en = client.post(f"/api/portrait/{spec_id}/assets", json={"filename": "IMG_0002.heic", "data_base64": b64(HEIC)},
                     headers={"X-StoryStudio-Lang": "en"})
    assert en.status_code == 400 and "export" in en.json()["detail"].lower() and not i18n._HAN.search(en.json()["detail"])


def test_music_reference_heic_keeps_the_export_hint(project):
    """配乐参考图有自己的「只接受 jpg / png，收到的是 X」措辞：HEIC 不能被拼成「收到的是 HEIC」把怎么办吞掉。"""

    spec_id = client.post("/api/music", json={"title": "纪念日", "brief": {"usage": "片尾"}, "duration": 60, "count": 1}).json()["id"]
    r = client.post(f"/api/music/{spec_id}/references", json={"filename": "IMG_0001.HEIC", "data_base64": b64(HEIC)})
    assert r.status_code == 400 and r.json()["detail"] == uploads.HEIF_HINT
    ok = client.post(f"/api/music/{spec_id}/references", json={"filename": "IMG_0002.jpg", "data_base64": b64(portrait_photo(6))})
    assert ok.status_code == 200, ok.text
    assert_upright((project / ok.json()["file"]).read_bytes())


# ---------------------------------------------------------------------------- 音频：按内容认


@needs_ffmpeg
def test_accept_audio_reads_content_not_the_extension():
    formats = (".mp3", ".wav", ".m4a")
    # AIFF 内容叫 .wav：读得出音频就收，转成真正的 wav（后缀与内容一致，浏览器也放得出来）
    got = uploads.accept_audio(aiff_bytes(), "voice.wav", allowed=formats)
    assert got.suffix == ".wav" and got.data[:4] == b"RIFF" and got.format == "aiff" and got.duration == pytest.approx(1.0, abs=0.05)
    # 叫 .aiff 的一样收（以前扩展名那一关就拒）
    assert uploads.accept_audio(aiff_bytes(), "voice.aiff", allowed=formats).suffix == ".wav"
    # WAV 字节叫 .mp3：原样收，后缀按内容
    wav = wav_bytes()
    got = uploads.accept_audio(wav, "song.mp3", allowed=formats)
    assert got.suffix == ".wav" and got.data == wav
    # 图片、文本、0 时长：说清原因，不带临时文件路径和解码器内存地址
    with pytest.raises(uploads.UploadContentError, match=r"^上传的文件不是能读的音频（cover\.mp3）：里面没有音频流——换一个 mp3 / wav / m4a 试试$"):
        uploads.accept_audio(png_bytes(), "cover.mp3", allowed=formats)
    with pytest.raises(uploads.UploadContentError) as info:
        uploads.accept_audio(b"hello, not audio", "notes.txt", allowed=formats)
    text = str(info.value)
    assert text.startswith("上传的文件不是能读的音频（notes.txt）：") and "storystudio-upload-" not in text and " @ " not in text
    with pytest.raises(uploads.UploadContentError, match="时长为 0"):
        uploads.accept_audio(wav_bytes(0), "empty.wav", allowed=formats)


def test_accept_audio_without_ffprobe_falls_back_to_the_header(monkeypatch):
    monkeypatch.setattr(uploads, "_tool", lambda name: None)
    aiff = aiff_bytes()
    assert uploads.sniff_audio(aiff) == ".aiff"
    # AIFF 内容叫 .wav：照收（装配时 ffmpeg 按内容读）；叫 .aiff 没法转，说清先转成什么
    assert uploads.accept_audio(aiff, "take.wav", allowed=(".mp3", ".wav")).suffix == ".wav"
    with pytest.raises(uploads.UploadContentError, match=r"^这种音频格式（aiff）这里用不了——先转成 mp3 / wav 再传$"):
        uploads.accept_audio(aiff, "take.aiff", allowed=(".mp3", ".wav"))
    with pytest.raises(uploads.UploadContentError, match="不是可用的音频文件"):
        uploads.accept_audio(b"junk bytes", "a.mp3", allowed=(".mp3", ".wav"))
    assert uploads.accept_audio(wav_bytes(), "a.mp3", allowed=(".mp3", ".wav")).suffix == ".wav"


@needs_ffmpeg
def test_podcast_music_accepts_aiff_named_wav_and_rejects_non_audio(project):
    pid = client.post("/api/podcast", json=podcast_body()).json()["id"]
    r = client.post(f"/api/podcast/{pid}/assets", json={"filename": "片头.wav", "data_base64": b64(aiff_bytes()), "slot": "intro"})
    assert r.status_code == 200, r.text
    stored = project / r.json()["file"]
    assert stored.suffix == ".wav" and stored.read_bytes()[:4] == b"RIFF" and r.json()["bytes"] == stored.stat().st_size
    assert client.get(f"/api/podcast/{pid}").json()["spec"]["music"]["intro"] == r.json()["file"]
    bad = client.post(f"/api/podcast/{pid}/assets", json={"filename": "cover.mp3", "data_base64": b64(png_bytes()), "slot": "bed"})
    assert bad.status_code == 400 and "里面没有音频流" in bad.json()["detail"]
    en = client.post(f"/api/podcast/{pid}/assets", json={"filename": "cover.mp3", "data_base64": b64(png_bytes()), "slot": "bed"},
                     headers={"X-StoryStudio-Lang": "en"})
    assert en.json()["detail"] == "The uploaded file is not readable audio (cover.mp3): it has no audio stream — try another mp3 / wav / m4a"
    assert client.get(f"/api/podcast/{pid}").json()["spec"]["music"]["bed"] == ""


@needs_ffmpeg
def test_voice_reference_audio_is_checked_by_content(project):
    client.post("/api/voices", json={"id": "mom", "name": "妈妈", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"})
    r = client.post("/api/voices/mom/reference-audio", json={"filename": "妈妈的声音.aiff", "data_base64": b64(aiff_bytes(2))})
    assert r.status_code == 200, r.text
    assert r.json()["reference_audio"].endswith(".wav")          # 克隆接口只收 mp3 / wav / m4a：AIFF 转成 wav
    got = client.get("/api/voices/mom/reference-audio")
    assert got.status_code == 200 and got.content[:4] == b"RIFF"
    junk = client.post("/api/voices/mom/reference-audio", json={"filename": "fake.mp3", "data_base64": b64(b"ID3" + b"\x00" * 64)})
    assert junk.status_code == 400 and junk.json()["detail"].startswith("上传的文件不是能读的音频（fake.mp3）：")


@needs_ffmpeg
def test_drama_music_upload_follows_content(project):
    r = client.post("/api/episodes/storyvideo/PHONE-1/music", json={"filename": "主题曲.aiff", "data_base64": b64(aiff_bytes())})
    assert r.status_code == 200, r.text
    assert r.json()["file"].endswith(".wav") and (project / r.json()["file"]).read_bytes()[:4] == b"RIFF"
    bad = client.post("/api/episodes/storyvideo/PHONE-1/music", json={"filename": "cover.mp3", "data_base64": b64(png_bytes())})
    assert bad.status_code == 400 and "里面没有音频流" in bad.json()["detail"]
    assert not list((project / "content" / "assets" / "PHONE-1" / "music").glob("cover-*"))


# ---------------------------------------------------------------------------- 音频：转换有上限、只留音轨（审查意见）


def ffmpeg_make(tmp_path, name: str, *args: str, pipe: bool = False) -> bytes:
    """用本机 ffmpeg 现做一段测试音频。``pipe``：写到管道再存盘——跟浏览器 MediaRecorder 录的 webm 一样，头里没有时长。"""

    target = tmp_path / name
    command = ["ffmpeg", "-v", "error", "-nostdin", "-y", *args]
    if pipe:
        done = subprocess.run([*command, "pipe:1"], capture_output=True, check=True)
        target.write_bytes(done.stdout)
    else:
        subprocess.run([*command, str(target)], capture_output=True, check=True)
    return target.read_bytes()


def probe_streams(data: bytes, tmp_path, suffix: str) -> list[tuple[str, str]]:
    path = tmp_path / f"probe{suffix}"
    path.write_bytes(data)
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(path)],
                          capture_output=True, text=True, check=True)
    return [(s["codec_type"], s["codec_name"]) for s in json.loads(done.stdout)["streams"]]


def opus_webm(tmp_path, seconds: int, *, pipe: bool = False) -> bytes:
    return ffmpeg_make(tmp_path, f"rec-{seconds}-{int(pipe)}.webm", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                       "-ac", "2", "-c:a", "libopus", "-b:a", "16k", *(["-f", "webm"] if pipe else []), pipe=pipe)


@needs_ffmpeg
def test_compressed_audio_converts_to_m4a_and_the_output_is_bounded(tmp_path):
    """审查：压缩过的音频（webm / opus…）以前一律转成 16 位 wav、不限大小、整份读进内存——一小时 26MB 的 webm 转出
    659MB。现在转 m4a（AAC），转出来超过上限直接 413：头里有时长的事先估算、不用真转；没有时长的靠 ffmpeg -fs 截住。"""

    everything = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
    rec = opus_webm(tmp_path, 60)
    got = uploads.accept_audio(rec, "rec.webm", allowed=everything)
    assert got.suffix == ".m4a" and got.duration == pytest.approx(60, abs=0.5) and got.format.startswith("matroska")
    assert probe_streams(got.data, tmp_path, ".m4a") == [("audio", "aac")]
    assert len(got.data) < 60 * 48000 * 2 * 2 / 4               # 比同长度的 wav 小得多
    # 转出来会超过上限：413，说清为什么要转、超了多少、怎么办
    with pytest.raises(uploads.UploadContentError) as info:
        uploads.accept_audio(rec, "rec.webm", allowed=everything, max_bytes=1024 * 1024)
    assert info.value.status == 413
    assert str(info.value) == "这段音频（rec.webm）要转成 m4a 才能用，转完会超过 1MB——剪短一些，或先转成 mp3 再传"
    # 头里没有时长（浏览器录音）：估不出来，靠 -fs 截住，同样 413 而不是转出一个大文件
    streamed = opus_webm(tmp_path, 60, pipe=True)
    with pytest.raises(uploads.UploadContentError) as info:
        uploads.accept_audio(streamed, "rec.webm", allowed=everything, max_bytes=1024 * 1024)
    assert info.value.status == 413
    # 只收 wav 的上传口：PCM 以外的源转 wav 也受上限管（一分钟立体声 48kHz 的 wav 约 11MB）
    with pytest.raises(uploads.UploadContentError) as info:
        uploads.accept_audio(streamed, "rec.webm", allowed=(".mp3", ".wav"), max_bytes=1024 * 1024)
    assert info.value.status == 413 and "要转成 wav 才能用" in str(info.value)


@needs_ffmpeg
def test_video_containers_keep_only_the_audio_track(tmp_path):
    """审查：mov / mp4 / 3gp 都报成 .m4a 容器，手机录的视频原样存进参考音频（带着画面发给克隆接口）。现在有画面 / 字幕 /
    数据流的只取音轨（AAC 直接拷贝，不重编码）；mp3 / m4a 里的封面图不算，照旧原样存。"""

    video = ffmpeg_make(tmp_path, "clip.mp4", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
                        "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest")
    got = uploads.accept_audio(video, "妈妈说话.mp4", allowed=(".mp3", ".wav", ".m4a"))
    assert got.suffix == ".m4a" and got.data != video and len(got.data) < len(video)
    assert probe_streams(got.data, tmp_path, ".m4a") == [("audio", "aac")]
    covered = ffmpeg_make(tmp_path, "cover.m4a", "-f", "lavfi", "-i", "sine=duration=2", "-f", "lavfi", "-i", "color=c=red:size=32x32:duration=1",
                          "-map", "0:a", "-map", "1:v", "-frames:v", "1", "-c:a", "aac", "-c:v", "png", "-disposition:v:0", "attached_pic")
    kept = uploads.accept_audio(covered, "带封面.m4a", allowed=(".mp3", ".wav", ".m4a"))
    assert kept.suffix == ".m4a" and kept.data == covered


@needs_ffmpeg
def test_upload_endpoints_turn_oversized_conversions_into_413(project, tmp_path, monkeypatch):
    """路由把转换超限翻成 413（以前一律 400 或者干脆 200 存下几百 MB）；音色参考音频的提示带上「克隆只要 10 秒到 5 分钟」。"""

    from genkit.adapters import minimax_speech
    from webui.routers import podcast as podcast_router

    rec = opus_webm(tmp_path, 60)
    monkeypatch.setattr(podcast_router, "MAX_UPLOAD_BYTES", 1024 * 1024)
    pid = client.post("/api/podcast", json=podcast_body()).json()["id"]
    r = client.post(f"/api/podcast/{pid}/assets", json={"filename": "垫乐.webm", "data_base64": b64(rec), "slot": "bed"})
    assert r.status_code == 413 and r.json()["detail"].startswith("这段音频（垫乐.webm）要转成 m4a 才能用")
    en = client.post(f"/api/podcast/{pid}/assets", json={"filename": "bed.webm", "data_base64": b64(rec), "slot": "bed"},
                     headers={"X-StoryStudio-Lang": "en"})
    assert en.status_code == 413 and en.json()["detail"].startswith("This audio (bed.webm) has to be converted to m4a")
    assert not list((project / "content" / "assets").rglob("*.m4a"))
    monkeypatch.setattr(post_router, "MAX_MUSIC_BYTES", 1024 * 1024)
    assert client.post("/api/episodes/storyvideo/PHONE-1/music", json={"filename": "bgm.webm", "data_base64": b64(rec)}).status_code == 413
    monkeypatch.setattr(minimax_speech, "MAX_REFERENCE_BYTES", 1024 * 1024)
    client.post("/api/voices", json={"id": "mom", "name": "妈妈", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"})
    r = client.post("/api/voices/mom/reference-audio", json={"filename": "妈妈.webm", "data_base64": b64(rec)})
    assert r.status_code == 413 and "克隆只要 10 秒到 5 分钟" in r.json()["detail"]
    # 上限够的时候照收，存成 m4a
    monkeypatch.setattr(minimax_speech, "MAX_REFERENCE_BYTES", 20 * 1024 * 1024)
    r = client.post("/api/voices/mom/reference-audio", json={"filename": "妈妈.webm", "data_base64": b64(rec)})
    assert r.status_code == 200, r.text
    assert r.json()["reference_audio"].endswith(".m4a")
