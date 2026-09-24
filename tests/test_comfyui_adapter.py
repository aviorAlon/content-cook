"""本机 ComfyUI 适配器：工作流打补丁（提示词真的落到映射指的那个位置）、提交 /prompt、轮询 /history + /queue、
/view 取件、参考图上传与 sha1 缓存、映射报错、种子记录、用户工作流目录、内置模板的映射自检。

全部是假 HTTP：这个适配器还没对真实 ComfyUI 验证过（docs/local-models-spec.md §7 / §8）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genkit import settings as S
from genkit.adapters import REGISTRY
from genkit.adapters import comfyui as C
from genkit.adapters.base import HttpResponse, local_token
from genkit.capabilities import lookup
from genkit.providers import AssetReference, ImageRequest, ProviderCommandError, TaskState, TaskType, VideoRequest
from genkit.router import RoutedProvider

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c636460f8cf0000020700813a1e7a9a0000000049454e44ae426082")

PROMPT_OK = {"prompt_id": "p-1", "number": 1, "node_errors": {}}
UPLOAD_OK = {"name": "frame.png", "subfolder": "", "type": "input"}
EMPTY_QUEUE = {"queue_running": [], "queue_pending": []}


class FakeHTTP:
    """按 URL 片段回放；同一片段给列表就依次出（最后一条重复用）。记录每次调用。"""

    def __init__(self, routes: list[tuple[str, object]]):
        self.routes = [(m, list(p) if isinstance(p, list) else [p]) for m, p in routes]
        self.calls: list[dict] = []

    def __call__(self, method, url, headers=None, json_body=None, data=None, timeout=0):
        self.calls.append({"method": method, "url": url, "json": json_body, "data": data, "headers": dict(headers or {})})
        for match, queue in self.routes:
            if match in url:
                payload = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(payload, HttpResponse):
                    return payload
                if isinstance(payload, bytes):
                    return HttpResponse(200, payload)
                return HttpResponse(200, json.dumps(payload).encode())
        raise AssertionError(f"没有为 {url} 准备的假响应")

    def hits(self, needle: str) -> list[dict]:
        return [c for c in self.calls if needle in c["url"]]

    def graph(self, index: int = 0) -> dict:
        return self.hits("/prompt")[index]["json"]["prompt"]


@pytest.fixture
def ref(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(PNG)
    return path


def _image_request(**kw):
    base = dict(name="frame", prompt="窗前的人", negative_prompt="文字, 水印", model="@image",
                aspect_ratio="9:16", image_size="1K")
    base.update(kw)
    return ImageRequest(**base)


def _adapter(http, tmp_path, **config):
    cfg = {"base_url": "http://127.0.0.1:8188", "model": "sd_xl_base_1.0.safetensors"}
    cfg.update(config)
    return C.ComfyUI(cfg, http=http, cache_dir=tmp_path / "cache")


# --------------------------------------------------------------------------- 提交


def test_submit_patches_the_workflow_at_the_mapped_paths(tmp_path):
    http = FakeHTTP([("/prompt", PROMPT_OK)])
    adapter = _adapter(http, tmp_path)
    sub = adapter.submit_image(_image_request(extra_params={"steps": 18}))

    assert sub.status is TaskState.PENDING and sub.task_id == "comfy:p-1"
    body = http.hits("/prompt")[0]["json"]
    assert body["client_id"] and isinstance(body["client_id"], str)
    graph = body["prompt"]
    # 提示词真的落在映射指的位置（txt2img_sdxl.map.json: prompt → ["6","inputs","text"]）
    assert graph["6"]["inputs"]["text"] == "窗前的人"
    assert graph["7"]["inputs"]["text"] == "文字, 水印"
    assert graph["5"]["inputs"]["width"] == 1024 and graph["5"]["inputs"]["height"] == 1824   # 9:16，短边 1K
    assert graph["3"]["inputs"]["steps"] == 18
    assert graph["4"]["inputs"]["ckpt_name"] == "sd_xl_base_1.0.safetensors"
    # 注释键不能发给 ComfyUI（它只认 {class_type, inputs}）
    assert "_comment" not in graph and all("class_type" in node for node in graph.values())


def test_seed_is_random_per_request_recorded_and_pinnable(tmp_path):
    http = FakeHTTP([("/prompt", PROMPT_OK)])
    adapter = _adapter(http, tmp_path)
    adapter.submit_image(_image_request())
    first = http.graph(0)["3"]["inputs"]["seed"]
    assert isinstance(first, int) and first != 0
    assert adapter.last_info["seed"] == first and adapter.last_info["workflow"] == "txt2img_sdxl"
    record = json.loads((tmp_path / "cache" / "comfyui_run_p-1.json").read_text(encoding="utf-8"))
    assert record["seed"] == first and record["output"] == "9" and record["task"] == "image"

    adapter.submit_image(_image_request())
    assert http.graph(1)["3"]["inputs"]["seed"] != first, "每次请求都该换种子"
    # 钉住种子就能复现
    adapter.submit_image(_image_request(extra_params={"seed": 12345}))
    assert http.graph(2)["3"]["inputs"]["seed"] == 12345
    with pytest.raises(ProviderCommandError):
        adapter.submit_image(_image_request(extra_params={"seed": "不是数字"}))


def test_reference_image_uploads_once_and_switches_to_img2img(tmp_path, ref):
    http = FakeHTTP([("/upload/image", UPLOAD_OK), ("/prompt", PROMPT_OK)])
    adapter = _adapter(http, tmp_path)
    adapter.submit_image(_image_request(reference_asset_ids=(local_token(ref),)))

    upload = http.hits("/upload/image")[0]
    assert upload["method"] == "POST" and "multipart/form-data" in upload["headers"]["Content-Type"]
    body = upload["data"].decode("latin-1")
    assert 'name="image"; filename="frame.png"' in body and 'name="overwrite"' in body and "true" in body
    # 没显式选工作流 + 带了参考图 → 自动用带输入图的那份
    assert adapter.last_info["workflow"] == "img2img_sdxl"
    graph = http.graph(0)
    assert graph["10"]["inputs"]["image"] == "frame.png"
    assert graph["6"]["inputs"]["text"] == "窗前的人"
    assert "width" not in graph.get("5", {}).get("inputs", {})   # 图生图没有 width 映射，跳过
    assert "local:" not in json.dumps(http.calls, default=str)

    # 同一张图第二次不再上传（按文件 sha1 缓存）
    adapter.submit_image(_image_request(reference_asset_ids=(local_token(ref),)))
    assert len(http.hits("/upload/image")) == 1
    table = json.loads((tmp_path / "cache" / "comfyui_uploads.json").read_text(encoding="utf-8"))
    assert list(table.values()) == ["frame.png"]
    # 换一台 ComfyUI 就是另一份缓存
    other = _adapter(http, tmp_path, base_url="http://127.0.0.1:8189")
    other.submit_image(_image_request(reference_asset_ids=(local_token(ref),)))
    assert len(http.hits("/upload/image")) == 2


def test_workflow_needing_an_input_image_says_so(tmp_path):
    http = FakeHTTP([("/prompt", PROMPT_OK)])
    adapter = _adapter(http, tmp_path, extra={"workflow": "img2img_sdxl"})
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(_image_request())
    assert "img2img_sdxl" in str(info.value) and "参考图" in str(info.value)


def test_prompt_rejection_names_the_bad_node(tmp_path):
    bad = HttpResponse(400, json.dumps({"error": {"message": "Prompt has no outputs"},
                                        "node_errors": {"4": {"class_type": "CheckpointLoaderSimple",
                                                              "errors": [{"message": "Value not in list: ckpt_name"}]}}}).encode())
    adapter = _adapter(FakeHTTP([("/prompt", bad)]), tmp_path)
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(_image_request())
    text = str(info.value)
    assert "txt2img_sdxl" in text and "4 号节点" in text and "ckpt_name" in text


# --------------------------------------------------------------------------- 轮询与取件


def test_poll_processing_then_finished_from_images(tmp_path):
    history = [{}, {"p-1": {"status": {"status_str": "success", "completed": True},
                            "outputs": {"9": {"images": [{"filename": "ComfyUI_00012_.png", "subfolder": "storystudio", "type": "output"}]}}}}]
    http = FakeHTTP([("/prompt", PROMPT_OK), ("/history/", history), ("/queue", {"queue_running": [[0, "p-1", {}, {}, []]], "queue_pending": []})])
    adapter = _adapter(http, tmp_path)
    sub = adapter.submit_image(_image_request())

    first = adapter.get_task(sub.task_id)
    assert first.status is TaskState.PROCESSING and first.task_type is TaskType.IMAGE
    assert http.hits("/queue"), "历史里没有就该看一眼队列"
    done = adapter.get_task(sub.task_id)
    assert done.status is TaskState.FINISHED
    assert done.output_urls == ("http://127.0.0.1:8188/view?filename=ComfyUI_00012_.png&subfolder=storystudio&type=output",)


def test_video_submit_and_outputs_from_gifs(tmp_path):
    finished = {"p-1": {"status": {"status_str": "success", "completed": True},
                        "outputs": {"10": {"gifs": [{"filename": "wan_00003.mp4", "subfolder": "", "type": "output", "format": "video/h264-mp4"}]}}}}
    http = FakeHTTP([("/prompt", PROMPT_OK), ("/history/", finished)])
    adapter = _adapter(http, tmp_path, model="wan2.1_t2v_1.3B_fp16.safetensors")
    request = VideoRequest(name="clip", prompt="她转头", duration=5, negative_prompt="抖动", resolution="480p", aspect_ratio="9:16")
    sub = adapter.submit_video(request)

    assert sub.task_id == "comfy:p-1" and sub.task_type is TaskType.VIDEO
    graph = http.graph(0)
    assert graph["4"]["inputs"]["text"] == "她转头" and graph["5"]["inputs"]["text"] == "抖动"
    assert graph["6"]["inputs"]["length"] == 81 and graph["10"]["inputs"]["frame_rate"] == 16   # 5 秒 × 16 帧 → 4n+1
    assert graph["6"]["inputs"]["width"] == 480 and graph["6"]["inputs"]["height"] == 856
    assert graph["1"]["inputs"]["unet_name"] == "wan2.1_t2v_1.3B_fp16.safetensors"

    snap = adapter.get_task(sub.task_id)
    assert snap.status is TaskState.FINISHED and snap.task_type is TaskType.VIDEO
    assert snap.output_urls[0].endswith("filename=wan_00003.mp4&subfolder=&type=output")


def test_vanished_prompt_fails_instead_of_polling_forever(tmp_path):
    http = FakeHTTP([("/prompt", PROMPT_OK), ("/history/", {}), ("/queue", EMPTY_QUEUE)])
    adapter = _adapter(http, tmp_path)
    sub = adapter.submit_image(_image_request())
    snap = adapter.get_task(sub.task_id)
    assert snap.status is TaskState.FAILED
    assert "历史" in snap.error_message and "队列" in snap.error_message and "p-1" in snap.error_message


def test_execution_error_and_completed_without_output(tmp_path):
    error = {"p-1": {"status": {"status_str": "error", "completed": False,
                                "messages": [["execution_start", {}],
                                             ["execution_error", {"node_type": "KSampler", "exception_message": "CUDA out of memory"}]]},
                     "outputs": {}}}
    adapter = _adapter(FakeHTTP([("/history/", error)]), tmp_path)
    snap = adapter.get_task("comfy:p-1")
    assert snap.status is TaskState.FAILED and "CUDA out of memory" in snap.error_message

    empty = {"p-1": {"status": {"status_str": "success", "completed": True}, "outputs": {}}}
    snap = _adapter(FakeHTTP([("/history/", empty)]), tmp_path).get_task("comfy:p-1")
    assert snap.status is TaskState.FAILED and "输出节点" in snap.error_message


def test_download_writes_bytes(tmp_path):
    http = FakeHTTP([("/view", b"PNGBYTES")])
    adapter = _adapter(http, tmp_path)
    out = adapter.download("http://127.0.0.1:8188/view?filename=a.png&subfolder=&type=output", tmp_path / "out" / "a.png")
    assert Path(out).read_bytes() == b"PNGBYTES"
    assert http.hits("/view")[0]["method"] == "GET"
    with pytest.raises(ProviderCommandError):
        _adapter(FakeHTTP([("/view", b"")]), tmp_path).download("http://127.0.0.1:8188/view?filename=a.png", tmp_path / "b.png")


def test_ping_reports_version_and_workflow_count(tmp_path):
    stats = {"system": {"comfyui_version": "0.3.40", "python_version": "3.12"}, "devices": [{"name": "cuda:0 NVIDIA"}]}
    adapter = _adapter(FakeHTTP([("/system_stats", stats)]), tmp_path)
    text = adapter.ping()
    assert "reachable" in text and "0.3.40" in text and "3 份" in text


# --------------------------------------------------------------------------- 工作流：加载、映射、用户目录


def test_builtin_workflows_are_api_format_and_their_mappings_resolve():
    rows = C.builtin_workflows()
    assert [r["name"] for r in rows] == ["txt2img_sdxl", "img2img_sdxl", "txt2video_wan"]
    assert [r["task"] for r in rows] == ["image", "image", "video"]
    for row in rows:
        workflow = C.load_workflow(row["name"])
        assert row["missing"] == [] and row["ok"], f"{row['name']} 的映射指到了不存在的位置：{row['missing']}"
        # API 格式：节点号 → {class_type, inputs}
        for node_id, node in workflow.graph.items():
            assert node_id.isdigit() and isinstance(node["inputs"], dict), node_id
        assert workflow.output_node in workflow.graph
        assert "prompt" in workflow.mapping and "seed" in workflow.mapping
    assert C.default_workflow_name("image") == "txt2img_sdxl" and C.default_workflow_name("video") == "txt2video_wan"
    # 图生图那份才吃参考图；文生图那份没有 image 映射（缺的映射跳过，不是错）
    assert "image" in C.load_workflow("img2img_sdxl").mapping
    assert "image" not in C.load_workflow("txt2img_sdxl").mapping


def _write_workflow(folder: Path, name: str, graph: dict, mapping: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    (folder / f"{name}.map.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")


def test_user_workflows_are_discovered_and_usable(tmp_path):
    root = tmp_path / "proj"
    graph = {"_comment": "自己导出的",
             "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "my.safetensors"}},
             "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
             "3": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["2", 0]}}}
    _write_workflow(root / "local_workflows", "mine", graph, {"task": "image", "title": "我的", "prompt": ["2", "inputs", "text"], "output": "3"})

    rows = C.list_workflows(root)
    names = [r["name"] for r in rows]
    assert "mine" in names and "txt2img_sdxl" in names
    mine = next(r for r in rows if r["name"] == "mine")
    assert mine["source"] == "user" and mine["ok"] and mine["keys"] == ["prompt"] and mine["title"] == "我的"

    http = FakeHTTP([("/prompt", PROMPT_OK)])
    adapter = C.ComfyUI({"base_url": "http://127.0.0.1:8188", "extra": {"workflow": "mine"}},
                        http=http, cache_dir=tmp_path / "cache", project_root=root)
    adapter.submit_image(_image_request())
    sent = http.graph(0)
    assert sent["2"]["inputs"]["text"] == "窗前的人" and "_comment" not in sent
    # 映射里没有 width/height/seed/model → 一律跳过，工作流原样
    assert sent["1"]["inputs"]["ckpt_name"] == "my.safetensors"


def test_check_mapping_reports_missing_paths(tmp_path):
    root = tmp_path / "proj"
    graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
             "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}}
    _write_workflow(root / "local_workflows", "broken", graph,
                    {"task": "image", "prompt": ["9", "inputs", "text"], "steps": ["1", "inputs", "steps"], "output": "7"})

    report = C.check_mapping("broken", root)
    assert report["ok"] is False
    by_key = {m["key"]: m for m in report["missing"]}
    assert by_key["prompt"]["path"] == "9/inputs/text" and "9 号节点" in by_key["prompt"]["reason"]
    assert by_key["steps"]["path"] == "1/inputs/steps"
    assert by_key["output"]["path"] == "7"
    # 一份没问题的工作流报 ok
    assert C.check_mapping("txt2img_sdxl")["ok"] is True and C.check_mapping("txt2img_sdxl")["missing"] == []
    # 名字不存在 / 不合法：不抛，报一句话
    unknown = C.check_mapping("nope", root)
    assert unknown["ok"] is False and "找不到工作流 nope" in unknown["error"]
    assert "不合法" in C.check_mapping("../etc/passwd", root)["error"]


def test_patch_errors_name_the_workflow_key_and_path(tmp_path):
    root = tmp_path / "proj"
    _write_workflow(root / "local_workflows", "broken",
                    {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
                    {"task": "image", "prompt": ["9", "inputs", "text"], "output": "1"})
    adapter = C.ComfyUI({"base_url": "http://127.0.0.1:8188", "extra": {"workflow": "broken"}},
                        http=FakeHTTP([("/prompt", PROMPT_OK)]), cache_dir=tmp_path / "cache", project_root=root)
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(_image_request())
    text = str(info.value)
    assert "broken" in text and "prompt" in text and "9/inputs/text" in text

    with pytest.raises(ProviderCommandError) as info:
        C.load_workflow("does_not_exist", root)
    assert "找不到工作流 does_not_exist" in str(info.value)
    # 少了 .map.json
    (root / "local_workflows" / "nomap.json").write_text(json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}), encoding="utf-8")
    with pytest.raises(ProviderCommandError) as info:
        C.load_workflow("nomap", root)
    assert "nomap.map.json" in str(info.value)
    # 界面导出的那份（带 nodes/links，不是 API 格式）
    _write_workflow(root / "local_workflows", "uiexport", {"nodes": [], "links": []}, {"task": "image", "output": "1"})
    with pytest.raises(ProviderCommandError) as info:
        C.load_workflow("uiexport", root)
    assert "API 格式" in str(info.value)


def test_video_workflow_cannot_be_used_for_images(tmp_path):
    adapter = _adapter(FakeHTTP([("/prompt", PROMPT_OK)]), tmp_path, extra={"workflow": "txt2video_wan"})
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(_image_request())
    assert "txt2video_wan" in str(info.value)


def test_size_and_frame_math():
    assert C.size_for("9:16", "1K") == (1024, 1824)
    assert C.size_for("16:9", "1K") == (1824, 1024)
    assert C.size_for("1:1", "2K") == (1440, 1440)
    assert C.size_for("乱写", None) == (1024, 1824)          # 认不出来按竖版
    assert C.frames_for(5, 16) == 81 and C.frames_for(2, 16) == 33 and C.frames_for(0, 16) == 17


# --------------------------------------------------------------------------- 登记：注册表 / 设置表 / 能力表 / 路由


def test_registry_settings_capabilities_and_router(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    assert REGISTRY["image"]["comfyui"] is C.build and REGISTRY["video"]["comfyui"] is C.build
    for role in ("image", "video"):
        row = S.ADAPTERS[role]["comfyui"]
        assert row["fields"] == ["base_url", "model"] and "api_key" not in row["fields"]
        assert row["defaults"]["base_url"] == "http://127.0.0.1:8188"
        assert "工作流" in row["hint"]
    # 能力表：只有画幅与尺寸区间，不吹参考图与原生音频
    caps = lookup("image", "comfyui", "", use_catalog=False)
    assert caps["known"] and caps["local"] is True and caps["source"] == "doc"
    assert "reference_images" not in caps and "9:16" in caps["aspect_ratios"]
    vcaps = lookup("video", "comfyui", "", use_catalog=False)
    assert vcaps["local"] is True and "audio" not in vcaps and "first_frame" not in vcaps

    # 本机来源不用填 key
    st = S.Settings()
    st.role("image").adapter = "comfyui"
    assert S.validate_role(st, "image") == []
    # 路由按 comfy: 前缀分派
    st.role("video").adapter = "comfyui"
    finished = {"p-9": {"status": {"status_str": "success", "completed": True},
                        "outputs": {"10": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}}}}
    adapter = _adapter(FakeHTTP([("/history/", finished)]), tmp_path)
    snap = RoutedProvider(st, adapters={"video": adapter}).get_task("comfy:p-9", "video")
    assert snap.status is TaskState.FINISHED and snap.output_urls[0].startswith("http://127.0.0.1:8188/view?")


def test_first_frame_reference_for_video_workflow_that_takes_one(tmp_path, ref):
    root = tmp_path / "proj"
    graph = {"1": {"class_type": "LoadImage", "inputs": {"image": "example.png", "upload": "image"}},
             "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
             "3": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["2", 0], "frame_rate": 24}}}
    _write_workflow(root / "local_workflows", "i2v", graph,
                    {"task": "video", "prompt": ["2", "inputs", "text"], "image": ["1", "inputs", "image"],
                     "fps": ["3", "inputs", "frame_rate"], "output": "3"})
    http = FakeHTTP([("/upload/image", UPLOAD_OK), ("/prompt", PROMPT_OK)])
    adapter = C.ComfyUI({"base_url": "http://127.0.0.1:8188", "extra": {"workflow": "i2v"}},
                        http=http, cache_dir=tmp_path / "cache", project_root=root)
    request = VideoRequest(name="clip", prompt="她转头", duration=4, request_type="first_frame",
                           references=(AssetReference(local_token(ref), "first_frame"),))
    adapter.submit_video(request)
    assert http.graph(0)["1"]["inputs"]["image"] == "frame.png"
    assert adapter.last_info["fps"] == 24 and adapter.last_info["length"] == 97   # 4 秒 × 工作流自己的 24 帧
