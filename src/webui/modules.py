"""模块注册表（DESIGN §38）：内容创作工作台下的功能模块。

模块 = 有自己的项目形态、自己的内容目录、自己的页面命名空间的一条产线（短剧是第一条，绘本 / 有声书是第二条）。
模型设置、音色库、统一网关、任务、导出这些是**共享服务**，不是模块，不在这张表里。

这张表是纯数据 + 几个只读辅助：``/api/modules`` 按它报模块与项目数，项目列表/概览按它给每行
打 ``module`` 标签，前端按它画首页的模块卡与侧栏分组。占位模块（``status="planned"``）只声明
名字与一句话说明，没有路由、没有内容目录——先把扩展点留出来，实现时只在这里把 ``status`` 改成
``ready`` 并填上 ``content_dir`` / ``route`` / ``project_kinds``。

内容目录不搬：短剧仍是 ``content/story_video``（``deps.STORYVIDEO_DIR``），这里只是登记。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

MODULE_STATUSES = ("ready", "planned")


@dataclass(frozen=True)
class Module:
    id: str
    name: str                      # 中文名（界面语言为中文时显示）
    english: str                   # 英文名
    description: str               # 一句话说明（中文）
    icon: str = ""
    content_dir: str | None = None  # 相对项目根；None = 还没有内容目录
    route: str | None = None        # 前端 hash 路由前缀；None = 还没有页面
    status: str = "planned"
    project_kinds: tuple[str, ...] = ()   # 这个模块认的 pipeline 名（项目列表按它归组）
    description_en: str = ""

    def __post_init__(self) -> None:
        if self.status not in MODULE_STATUSES:
            raise ValueError(f"module {self.id}: status must be one of {MODULE_STATUSES}, got {self.status!r}")
        if self.status == "ready" and not (self.route and self.content_dir):
            raise ValueError(f"module {self.id}: ready modules need route and content_dir")

    @property
    def available(self) -> bool:
        return self.status == "ready"


MODULES: list[Module] = [
    Module(
        id="drama",
        name="短剧",
        english="Short drama",
        description="剧本 → 分镜 → 首帧 → 视频 → 审片 → 后期 → 成片 → 海报",
        description_en="Script → storyboard → first frames → videos → review → post → final cut → poster",
        icon="🎬",
        content_dir="content/story_video",
        route="/drama",
        status="ready",
        project_kinds=("storyvideo",),
    ),
    Module(
        id="image",
        name="图片工作台",
        english="Image studio",
        description="单张 / 成组出图、多模型对比、图卡叠字",
        description_en="Single or batch image generation, multi-model comparison, text cards",
        icon="🖼",
        content_dir="content/image",
        route="/image",
        status="ready",
        project_kinds=("image",),
    ),
    Module(
        id="clip",
        name="单条视频",
        english="Single clip",
        description="一条提示词出一段视频：全片设定 / 分段时间线 / 衔接与收尾三段写法",
        description_en="One prompt → one clip: the global / timeline / continuity prompt pattern",
        icon="🎞",
        content_dir="content/clip",
        route="/clip",
        status="ready",
        project_kinds=("clip",),
    ),
    Module(
        id="picturebook",
        name="绘本 / 有声书",
        english="Picture book / audiobook",
        description="插画 + 朗读 + 双语字幕的有声绘本：每页停多久由朗读长度决定",
        description_en="Narrated picture books with illustrations and bilingual subtitles: each page stays on screen as long as its narration runs",
        icon="📖",
        content_dir="content/picturebook",
        route="/picturebook",
        status="ready",
        project_kinds=("picturebook",),
    ),
    Module(
        id="sticker",
        name="表情与头像",
        english="Stickers and avatars",
        description="按模板出一组图：表情包（微信规格）、贴图、头像多风格",
        description_en="Template-driven image sets: emoji packs (WeChat sizes), stickers, multi-style avatars",
        icon="🙂",
        content_dir="content/sticker",
        route="/sticker",
        status="ready",
        project_kinds=("sticker",),
    ),
    Module(
        id="portrait",
        name="纪念写真",
        english="Milestone portraits",
        description="满月 / 百天 / 周岁 / 婚纱：按场景清单出一组照片，可叠祝语与日期",
        description_en="Full-moon, 100-day, first-birthday and wedding sets from a scene list, with optional greeting and date overlay",
        icon="📷",
        content_dir="content/portrait",
        route="/portrait",
        status="ready",
        project_kinds=("portrait",),
    ),
    Module(
        id="music",
        name="配乐",
        english="Music",
        description="按时间线意图生成配乐，落到项目的配乐层",
        description_en="Generate music from timeline intent and drop it into a project's music layer",
        icon="🎵",
        content_dir="content/music",
        route="/music",
        status="ready",
        project_kinds=("music",),
    ),
    Module(
        id="podcast",
        name="AI 播客",
        english="AI podcast",
        description="对谈稿（环节 / 台词 / 听友问答）→ 逐句配音 → 混成一期，附节目单；稿子按真人播客的写法约束与检查",
        description_en="Talk script (segments, lines, listener Q&A) → per-line voices → one mixed episode with show notes; drafted and checked against how real podcasts sound",
        icon="🎙",
        content_dir="content/podcast",
        route="/podcast",
        status="ready",
        project_kinds=("podcast",),
    ),
    Module(
        id="script",
        name="剧本工作台",
        english="Script lab",
        description="台词调试台：一段换几种写法、并排比、挑一版，调好了投放到播客或短剧",
        description_en="A dialogue workbench: try several takes on one block, compare them side by side, pick one, then hand it off to a podcast or a short drama",
        icon="✍️",
        content_dir="content/script",
        route="/script",
        status="ready",
        project_kinds=("script",),
    ),
]

_BY_ID: dict[str, Module] = {m.id: m for m in MODULES}
_BY_KIND: dict[str, Module] = {kind: m for m in MODULES for kind in m.project_kinds}


def get_module(module_id: str) -> Module | None:
    return _BY_ID.get(module_id)


def module_for_pipeline(pipeline: str) -> str | None:
    """项目列表里的 ``pipeline``（storyvideo …）属于哪个模块；不认识返回 None。"""

    module = _BY_KIND.get(pipeline)
    return module.id if module else None


def module_payload(module: Module, project_count: int | None = None) -> dict[str, Any]:
    out = asdict(module)
    out["project_kinds"] = list(module.project_kinds)
    out["available"] = module.available
    out["project_count"] = int(project_count or 0) if module.available else 0
    return out


def modules_payload(counter: Callable[[Module], int]) -> list[dict[str, Any]]:
    """``counter(module)`` 只对 ready 模块调用（planned 模块没有内容目录，数不出来）。"""

    return [module_payload(m, counter(m) if m.available else 0) for m in MODULES]


__all__ = ["MODULE_STATUSES", "MODULES", "Module", "get_module", "module_for_pipeline", "module_payload", "modules_payload"]
