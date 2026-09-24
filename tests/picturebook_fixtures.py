"""绘本测试共用：示例书、临时项目根、假图 / 假音频、ffmpeg 探测。"""

from __future__ import annotations

import copy
import io
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = ROOT / "examples" / "picturebook" / "fox_three_socks.json"
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def example_data() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8-sig"))


def example_spec(project_root: Path, book_id: str = "PB-T-1") -> dict:
    from picturebook.spec import normalize_book

    return normalize_book(example_data(), project_root=project_root, book_id=book_id)


def small_book(project_root: Path, book_id: str = "PB-S-1", *, mode: str = "sentence_alternate", pages: int = 3) -> dict:
    """三页小书（封面 / 正文 / 封底），装配与朗读测试用；正文页两句，封面封底一句。"""

    from picturebook.spec import normalize_book

    rows = [
        {"id": "P01", "kind": "cover", "scene": "小狐狸站在院子里，全身", "characters": ["fox"], "zh": "小狐狸的三只袜子", "en": "Little Fox's Three Socks",
         "kb": {"dir": "push"}, "flip": "page_turn"},
        {"id": "P02", "kind": "page", "scene": "风吹起晾衣绳上的袜子，中景", "characters": ["fox"], "zh": "呼呼呼，红袜子飞到哪儿去了？",
         "en": "Whoosh — where did the red sock go?", "hook": "它会落在哪儿呢？", "kb": {"dir": "left"}, "flip": "cut"},
        {"id": "P03", "kind": "back", "scene": "小狐狸睡着了，夜色温柔", "characters": ["fox"], "zh": "小狐狸回家了。晚安。", "en": "Little Fox goes home. Good night.",
         "kb": {"dir": "pull"}, "flip": "cut"},
    ][:pages]
    data = {
        "title_zh": "小狐狸的三只袜子", "title_en": "Little Fox's Three Socks", "age_band": "2-4", "bilingual_mode": mode,
        "education": {"cognition": "", "vocab": [], "emotion": ""},
        "characters": {"fox": {"name": "小狐狸", "name_en": "Little Fox", "block": "橘红色小狐狸，浅绿色小背心"}},
        "pages": rows,
    }
    return normalize_book(data, project_root=project_root, book_id=book_id)


def write_book(project_root: Path, data: dict, book_id: str) -> Path:
    from picturebook.spec import book_path

    path = book_path(project_root, book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = copy.deepcopy(data)
    payload.pop("output_root", None)
    payload.pop("project_root", None)
    payload["id"] = book_id
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def png_bytes(color=(210, 180, 140), size=(300, 400)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def lavfi_png(path: Path, color: str = "0xC8A878", size: str = "768x1024") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}",
                    "-frames:v", "1", "-update", "1", str(path)], check=True)
    return path


def sine_mp3(path: Path, seconds: float = 0.6, freq: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "44100", "-c:a", "libmp3lame", "-q:a", "6", str(path)], check=True)
    return path


def wait_task(client, task_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.05)
    raise AssertionError("task did not finish")
