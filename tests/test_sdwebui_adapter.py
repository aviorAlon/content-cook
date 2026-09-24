"""本机 Stable Diffusion WebUI / Forge 适配器（docs/local-models-spec.md §4.1）：txt2img / img2img 的请求体、
尺寸映射、模型切换、错误文案、以及注册表与能力表登记。全部是假 HTTP——**没有对着真实 SD WebUI 跑过**。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from genkit import settings as S
from genkit.adapters import REGISTRY
from genkit.adapters import sdwebui as SD
from genkit.adapters.base import HttpResponse, local_token
from genkit.capabilities import lookup
from genkit.providers import ImageRequest, ProviderCommandError, ProviderTimeout, QuotaExhausted, TaskState

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
CURRENT = "sd_xl_base_1.0.safetensors [31e35c80fc]"


class FakeHTTP:
    """按 URL 片段回放；记录每次调用（方法、URL、正文）。"""

    def __init__(self, routes: dict[str, object], *, raises: Exception | None = None):
        self.routes = routes
        self.raises = raises
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers=None, json_body=None, data=None, timeout=0):
        self.calls.append((method, url, json_body or {}))
        if self.raises is not None:
            raise self.raises
        for match, payload in self.routes.items():
            if match in url:
                if isinstance(payload, HttpResponse):
                    return payload
                return HttpResponse(200, json.dumps(payload).encode())
        raise AssertionError(f"no route for {url}")

    def body_of(self, match: str) -> dict:
        for _method, url, body in self.calls:
            if match in url and body:
                return body
        return {}

    def count(self, match: str, method: str | None = None) -> int:
        return sum(1 for m, url, _b in self.calls if match in url and (method is None or m == method))


def image_response(info: str = '{"seed": 42}') -> dict:
    return {"images": [base64.b64encode(PNG).decode()], "parameters": {}, "info": info}


def routes(**extra) -> dict[str, object]:
    base: dict[str, object] = {
        "/sdapi/v1/options": {"sd_model_checkpoint": CURRENT},
        "/sdapi/v1/txt2img": image_response(),
        "/sdapi/v1/img2img": image_response(),
        "/sdapi/v1/sd-models": [{"title": CURRENT, "model_name": "sd_xl_base_1.0"},
                                {"title": "dreamshaper_8.safetensors [x]", "model_name": "dreamshaper_8"}],
    }
    base.update(extra)
    return base


def make(tmp_path, http, **config):
    cfg = {"base_url": "http://127.0.0.1:7860", "model": "sd_xl_base_1.0.safetensors", "api_key": ""}
    cfg.update(config)
    return SD.SDWebUI(cfg, http=http, cache_dir=tmp_path / "cache")


def request(**kw) -> ImageRequest:
    params = {"name": "frame_01", "prompt": "窗前的人", "negative_prompt": "文字, 水印", "model": "@image",
              "aspect_ratio": "9:16", "image_size": "2K"}
    params.update(kw)
    return ImageRequest(**params)


@pytest.fixture
def ref(tmp_path) -> Path:
    path = tmp_path / "anchor.png"
    path.write_bytes(PNG)
    return path


# --------------------------------------------------------------------------- 尺寸


def test_dimensions_cover_every_aspect_ratio_and_stay_multiples_of_8():
    assert SD.dimensions("1:1", "2K") == (1024, 1024)
    assert SD.dimensions("9:16", "2K") == (1024, 1824)
    assert SD.dimensions("16:9", "2K") == (1824, 1024)
    assert SD.dimensions("3:4", "2K") == (1024, 1368)
    assert SD.dimensions("4:3", "2K") == (1368, 1024)
    assert SD.dimensions("2:3", "2K") == (1024, 1536)
    assert SD.dimensions("3:2", "2K") == (1536, 1024)
    assert SD.dimensions("21:9", "2K") == (2392, 1024)
    for ratio in ("1:1", "9:16", "16:9", "3:4", "4:3", "2:3", "3:2", "21:9", "4:5", "adaptive"):
        for size in ("1K", "2K", "4K", "512", "什么档都没写"):
            w, h = SD.dimensions(ratio, size)
            assert w % 8 == 0 and h % 8 == 0 and w >= 8 and h >= 8


def test_short_side_by_size_and_fallbacks():
    assert SD.dimensions("1:1", "1K") == (1024, 1024)      # SDXL 原生：1K / 2K 都出 1024 短边
    assert SD.dimensions("1:1", "2K") == (1024, 1024)
    assert SD.dimensions("1:1", "4K") == (1536, 1536)      # 4K 折中到 1536，再高就出重复肢体
    assert SD.dimensions("1:1", "512") == (512, 512)       # SD1.5 一档显式要小图
    assert SD.dimensions("1:1", "sd15") == (512, 512)
    assert SD.dimensions("adaptive", "2K") == (1024, 1824)  # 认不出的画幅按竖屏兜底
    assert SD.round8(1820.4) == 1824 and SD.round8(0) == 8


# --------------------------------------------------------------------------- txt2img


def test_txt2img_body_shape_and_defaults(tmp_path):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http)
    submission = adapter.submit_image(request())
    assert submission.status is TaskState.FINISHED and submission.task_id.startswith("file:")
    body = http.body_of("/sdapi/v1/txt2img")
    assert body == {
        "prompt": "窗前的人", "negative_prompt": "文字, 水印", "width": 1024, "height": 1824,
        "steps": 30, "cfg_scale": 7.0, "sampler_name": "DPM++ 2M Karras", "seed": -1,
        "batch_size": 1, "n_iter": 1,
    }
    assert "init_images" not in body and "denoising_strength" not in body
    assert http.count("/sdapi/v1/img2img") == 0
    # 落盘的是真 PNG
    path = Path(submission.task_id[len("file:"):])
    assert path.read_bytes() == PNG and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and path.suffix == ".png"


def test_extra_params_override_sampling_and_size(tmp_path):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http, extra={"steps": 12, "sampler_name": "Euler a"})
    adapter.submit_image(request(extra_params={"cfg_scale": 3.5, "seed": 7, "width": 900, "height": 1201}))
    body = http.body_of("/sdapi/v1/txt2img")
    assert body["steps"] == 12 and body["sampler_name"] == "Euler a"          # 来自角色配置的 extra
    assert body["cfg_scale"] == 3.5 and body["seed"] == 7                     # 来自 spec 的 extra_params（优先）
    assert body["width"] == 904 and body["height"] == 1200                    # 显式尺寸也对齐到 8


def test_empty_api_key_is_accepted_and_no_auth_header(tmp_path):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http, api_key="")
    assert adapter.auth_headers() == {}
    assert adapter.submit_image(request()).status is TaskState.FINISHED
    # 反向代理后面才需要 key，填了就带上
    assert make(tmp_path, http, api_key="k").auth_headers() == {"Authorization": "Bearer k"}


def test_default_base_url_when_settings_left_blank(tmp_path):
    http = FakeHTTP(routes())
    adapter = SD.SDWebUI({"model": "sd_xl_base_1.0.safetensors"}, http=http, cache_dir=tmp_path)
    adapter.submit_image(request())
    assert http.calls[0][1].startswith("http://127.0.0.1:7860/sdapi/v1/")


# --------------------------------------------------------------------------- img2img


def test_img2img_when_references_present(tmp_path, ref):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http)
    submission = adapter.submit_image(request(reference_asset_ids=(local_token(ref),)))
    assert submission.status is TaskState.FINISHED
    assert http.count("/sdapi/v1/txt2img") == 0
    body = http.body_of("/sdapi/v1/img2img")
    assert body["init_images"] == [base64.b64encode(PNG).decode("ascii")]
    assert body["denoising_strength"] == 0.6 and body["width"] == 1024 and body["height"] == 1824
    assert "local:" not in json.dumps(http.calls) and str(ref) not in json.dumps(http.calls)
    assert adapter.last_info["endpoint"] == "img2img" and adapter.last_info["notes"] == []
    assert adapter.last_info["server_info"] == {"seed": 42}


def test_img2img_records_the_dropped_references(tmp_path, ref):
    second = ref.parent / "prop.png"
    second.write_bytes(PNG)
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http)
    submission = adapter.submit_image(request(reference_asset_ids=(local_token(ref), local_token(second))))
    body = http.body_of("/sdapi/v1/img2img")
    assert len(body["init_images"]) == 1
    note = adapter.last_info["notes"][0]
    assert "只重绘一张底图" in note and "anchor.png" in note and "prop.png" in note
    # 备注也写在产物旁边，事后能查到第二张参考图去哪了
    sidecar = Path(submission.task_id[len("file:"):]).with_suffix(".info.json")
    saved = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "prop.png" in saved["notes"][0] and saved["request"]["width"] == 1024 and "init_images" not in saved["request"]


def test_missing_reference_file_is_a_clear_error(tmp_path):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http)
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(request(reference_asset_ids=(local_token(tmp_path / "nope.png"),)))
    assert "reference image does not exist" in str(info.value)


def test_denoising_strength_is_overridable(tmp_path, ref):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http)
    adapter.submit_image(request(reference_asset_ids=(local_token(ref),), extra_params={"denoising_strength": 0.35}))
    assert http.body_of("/sdapi/v1/img2img")["denoising_strength"] == 0.35


# --------------------------------------------------------------------------- 切模型


def test_checkpoint_switch_posted_once_per_instance(tmp_path):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http, model="dreamshaper_8.safetensors")
    adapter.submit_image(request())
    adapter.submit_image(request())
    adapter.submit_image(request())
    assert http.count("/sdapi/v1/options", "POST") == 1
    assert http.count("/sdapi/v1/options", "GET") == 1      # 当前模型只读一次
    assert http.body_of("/sdapi/v1/options") == {"sd_model_checkpoint": "dreamshaper_8.safetensors"}
    assert adapter.last_info["switched_checkpoint"] == ""    # 第三张不再记「切过模型」
    assert http.count("/sdapi/v1/txt2img") == 3


def test_checkpoint_switch_skipped_when_already_current(tmp_path):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http, model="sd_xl_base_1.0.safetensors")   # 服务端标题带哈希，仍算同一个
    adapter.submit_image(request())
    assert http.count("/sdapi/v1/options", "POST") == 0
    assert adapter.last_info["switched_checkpoint"] == ""


def test_checkpoint_name_matching_ignores_hash_path_and_extension():
    assert SD.same_checkpoint(CURRENT, "sd_xl_base_1.0.safetensors")
    assert SD.same_checkpoint("SDXL/sd_xl_base_1.0.safetensors", "sd_xl_base_1.0")
    assert SD.same_checkpoint("sd_xl_base_1.0.ckpt", "SD_XL_BASE_1.0")
    assert not SD.same_checkpoint(CURRENT, "dreamshaper_8.safetensors")
    assert not SD.same_checkpoint("", "x") and not SD.same_checkpoint("x", "")


def test_empty_model_uses_whatever_is_loaded(tmp_path):
    """本机服务没有「模型必填」的道理：设置里留空就用 WebUI 界面上选好的那个，不去切。"""

    http = FakeHTTP(routes())
    adapter = make(tmp_path, http, model="")
    assert adapter.submit_image(request()).status is TaskState.FINISHED
    assert http.count("/sdapi/v1/options") == 0
    # spec 里写了具体模型时以 spec 为准
    assert adapter.model_for(request(model="dreamshaper_8.safetensors")) == "dreamshaper_8.safetensors"
    assert adapter.model_for(request(model="@image")) == ""


# --------------------------------------------------------------------------- 错误


def test_connection_refused_says_the_local_service_is_not_running(tmp_path):
    from webui.humanize import classify_failure

    http = FakeHTTP(routes(), raises=ConnectionRefusedError(61, "Connection refused"))
    adapter = make(tmp_path, http, model="")
    with pytest.raises(ProviderTimeout) as info:
        adapter.submit_image(request())
    text = str(info.value)
    assert "连不上本机 Stable Diffusion WebUI" in text and "http://127.0.0.1:7860" in text and "--api" in text
    assert classify_failure(f"ProviderTimeout: {text}")["kind"] == "timeout"


def test_requests_style_connection_error_is_also_recognized(tmp_path):
    """default_http 已经把 requests 的异常包成 ProviderTimeout，文本里只剩一句英文——也要认出来。"""

    boom = ProviderTimeout("POST http://127.0.0.1:7860/sdapi/v1/txt2img: HTTPConnectionPool(host='127.0.0.1', port=7860): "
                           "Max retries exceeded (Caused by NewConnectionError('Connection refused'))")
    adapter = make(tmp_path, FakeHTTP(routes(), raises=boom), model="")
    with pytest.raises(ProviderTimeout) as info:
        adapter.submit_image(request())
    assert "连不上本机 Stable Diffusion WebUI" in str(info.value)


def test_http_error_surfaces_the_server_text(tmp_path):
    body = json.dumps({"error": "OutOfMemoryError", "detail": "", "body": "",
                       "errors": "CUDA out of memory. Tried to allocate 2.00 GiB"}).encode()
    http = FakeHTTP(routes(**{"/sdapi/v1/txt2img": HttpResponse(500, body)}))
    adapter = make(tmp_path, http, model="")
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(request())
    text = str(info.value)
    assert "HTTP 500" in text and "OutOfMemoryError" in text and "CUDA out of memory" in text and "127.0.0.1:7860" in text


def test_http_404_reads_as_api_not_enabled(tmp_path):
    http = FakeHTTP(routes(**{"/sdapi/v1/txt2img": HttpResponse(404, b'{"detail":"Not Found"}')}))
    adapter = make(tmp_path, http, model="")
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(request())
    assert "HTTP 404" in str(info.value) and "Not Found" in str(info.value)


def test_validation_error_detail_list_is_flattened(tmp_path):
    body = json.dumps({"detail": [{"loc": ["body", "sampler_name"], "msg": "sampler not found"}]}).encode()
    http = FakeHTTP(routes(**{"/sdapi/v1/txt2img": HttpResponse(422, body)}))
    adapter = make(tmp_path, http, model="")
    with pytest.raises(ProviderCommandError) as info:
        adapter.submit_image(request())
    assert "sampler not found" in str(info.value)


def test_quota_classification_still_works_even_though_local_has_none(tmp_path):
    """公共 raise_for_status 的分类没被绕过：本机服务不会真的报 402，但分类链路要还在。"""

    http = FakeHTTP(routes(**{"/sdapi/v1/txt2img": HttpResponse(402, b'{"error":"insufficient balance"}')}))
    adapter = make(tmp_path, http, model="")
    with pytest.raises(QuotaExhausted):
        adapter.submit_image(request())


def test_empty_images_array_is_a_failed_submission(tmp_path):
    http = FakeHTTP(routes(**{"/sdapi/v1/txt2img": {"images": [], "info": "{}"}}))
    adapter = make(tmp_path, http, model="")
    submission = adapter.submit_image(request())
    assert submission.status is TaskState.FAILED and "没有返回图片" in submission.error_message


def test_broken_base64_is_reported(tmp_path):
    http = FakeHTTP(routes(**{"/sdapi/v1/txt2img": {"images": [""], "info": "{}"}}))
    adapter = make(tmp_path, http, model="")
    with pytest.raises(ProviderCommandError):
        adapter.submit_image(request())


def test_data_url_and_jpeg_payloads(tmp_path):
    assert SD.decode_image("data:image/png;base64," + base64.b64encode(PNG).decode()) == PNG
    assert SD.suffix_for(PNG) == ".png" and SD.suffix_for(b"\xff\xd8\xff\xe0rest") == ".jpg"


# --------------------------------------------------------------------------- 登记


def test_ping_lists_models(tmp_path):
    http = FakeHTTP(routes())
    adapter = make(tmp_path, http)
    text = adapter.ping()
    assert "2 个模型" in text and "127.0.0.1:7860" in text and "sd_xl_base_1.0" in text
    assert adapter.list_models()[1].startswith("dreamshaper_8")


def test_registry_settings_and_capabilities():
    assert REGISTRY["image"]["sdwebui"] is SD.build
    row = S.ADAPTERS["image"]["sdwebui"]
    assert row["fields"] == ["base_url", "model"] and "api_key" not in row["fields"]
    assert row["defaults"]["base_url"] == "http://127.0.0.1:7860"
    assert "--api" in row["hint"] and row["label"].startswith("本机 Stable Diffusion WebUI")
    caps = lookup("image", "sdwebui", "sd_xl_base_1.0.safetensors", use_catalog=False)
    assert caps["known"] and caps["local"] is True and caps["source"] == "doc" and caps["cost"] == "free"
    assert caps["max_reference_images"] == 1 and "9:16" in caps["aspect_ratios"] and "2K" in caps["image_sizes"]


def test_role_validation_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    st = S.Settings()
    role = st.role("image")
    role.adapter, role.model = "sdwebui", "sd_xl_base_1.0.safetensors"
    assert S.validate_role(st, "image") == []            # base_url 有默认值、不要 key
    assert S.effective(role, "image").base_url == "http://127.0.0.1:7860"
