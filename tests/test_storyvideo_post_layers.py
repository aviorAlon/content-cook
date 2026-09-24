"""storyvideo.post_layers：旁白 VO + 插卡 + 本子字 + 混音的后期层。合成用 ffmpeg 合成的小片段跑真实 ffmpeg。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from storyvideo import post_layers as pl

FFMPEG = shutil.which("ffmpeg")
FONT = Path("C:/Windows/Fonts/msyh.ttc")


def _spec(root: Path) -> dict:
    return {
        "id": "POST-T", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0.25},
        "subtitle": {"chinese_font_size": 20, "safe_bottom_percent": 7, "band_alpha": 0.2},
        "voice_over": {"edge": {"voice": "zh-CN-YunxiNeural", "rate": "-6%", "pitch": "-8Hz"},
                       "lines": [{"shot": "S1", "at": 0.5, "zh": "楼下的面包店今天没开门。"}, {"shot": "UI_1", "at": 0.2, "zh": "还有一条消息。"}]},
        "post_graphics": {
            "fonts": {"notebook": str(FONT), "ui": str(FONT), "subtitle": str(FONT)},
            "phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 2, "screen": "「系统通知」→ 今晚有雨｜记得带伞"}],
            "notebook_text": [{"shot": "S2", "text": "你好", "y": 0.5, "size": 40}],
        },
        "shots": [{"id": "S1", "dur": 3, "lines": []}, {"id": "S2", "dur": 3, "lines": [{"who": "x", "zh": "你怎么才来？"}]}],
    }




def test_split_screen_text_and_timeline(tmp_path):
    assert pl._split_screen_text("「A」→ B｜C；D") == ["A", "B", "C", "D"]
    spec = _spec(tmp_path)
    items = pl.build_timeline(spec, video_dir=tmp_path / "02_video", ui_dir=tmp_path / "08_ui", durations={"S1": 3.0, "S2": 3.0})
    assert [x.item_id for x in items] == ["S1", "UI_1", "S2"]
    assert [round(x.start, 2) for x in items] == [0.0, 2.75, 4.5]      # 交叉溶解 0.25 重叠
    assert pl.absolute_time(items, "UI_1", 0.2) == pytest.approx(2.95)


@pytest.mark.skipif(not FFMPEG or not FONT.is_file(), reason="needs ffmpeg and a CJK font")
def test_compose_post_end_to_end(tmp_path):
    root = tmp_path / "EP"
    (root / "02_video").mkdir(parents=True)
    for sid, color in (("S1", "blue"), ("S2", "red")):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:r=12:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(root / "02_video" / f"{sid}.mp4")], check=True)

    def fake_tts(text: str, dest: Path) -> None:   # 1 秒正弦波代替 edge-tts
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1",
                        "-c:a", "libmp3lame", str(dest)], check=True)

    manifest = pl.compose_post(_spec(root), tts=fake_tts)
    out = Path(manifest["output"])
    assert out.is_file() and out.name == "POST-T_post.mp4"
    assert manifest["duration_seconds"] == pytest.approx(7.5, abs=0.6)   # 3 + 2 + 3 - 2×0.25
    assert [x["id"] for x in manifest["timeline"]] == ["S1", "UI_1", "S2"]
    assert (root / "08_ui" / "ui_000.png").is_file()   # 卡图按时间线编号起名（F1）
    assert len(manifest["vo"]) == 2 and all(Path(v["path"]).is_file() for v in manifest["vo"])
    saved = json.loads((root / "manifests" / "post.json").read_text(encoding="utf-8"))
    assert saved["timeline"][1]["kind"] == "ui"
    # 二次调用复用已渲染的 VO（不再调 tts）
    calls = []
    pl.render_vo_lines(_spec(root), root / "07_vo", tts=lambda t, d: calls.append(t))
    assert calls == []
