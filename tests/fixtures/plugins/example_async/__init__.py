"""示例插件（测试替身，也是 docs/plugins.md 的样例）：一个进程内的异步来源，生图 / 视频 / 配乐三档。

不联网、不起子进程：提交时把「结果」写进缓存目录、只给一个带前缀的远端任务 id，查进度时再交出 file:// 地址——
跟真实的异步来源走同一条路（提交 → 账本记任务 id → 轮询 → 下载）。
"""

STORYSTUDIO_API = 1
__version__ = "0.1.0"


def register(api):
    from . import adapter as A

    for role, adapter_id, builder, label, hint, model in (
        ("image", "example_async_images", A.build_images, "示例异步生图（测试替身，不联网）", "提交后给远端任务 id，查一次就完成", "demo-image"),
        ("video", "example_async_video", A.build_video, "示例异步视频（测试替身，不联网）", "首帧图生视频；提交后给远端任务 id", "demo-video"),
        ("music", "example_async_music", A.build_music, "示例异步配乐（测试替身，不联网）", "文生音乐；参考图只收 jpg / png", "demo-music"),
    ):
        api.add_adapter(role, adapter_id, builder, label=label, hint=hint, fields=["model"], defaults={"model": model})
    for prefix, adapter_id in ((A.PREFIX_IMAGE, "example_async_images"), (A.PREFIX_VIDEO, "example_async_video"),
                               (A.PREFIX_MUSIC, "example_async_music")):
        api.add_async_prefix(prefix, adapter_id)
    api.add_capabilities({
        ("image", "example_async_images", ""): {"reference_images": True, "aspect_ratios": ["9:16", "16:9", "1:1"],
                                                 "image_sizes": ["1K", "2K"], "source": "doc"},
        ("video", "example_async_video", "demo-video"): {"durations": {"min": 2, "max": 12}, "resolutions": ["720p"],
                                                          "first_frame": True, "audio": True, "source": "doc"},
        ("music", "example_async_music", ""): {"counts": {"min": 1, "max": 2}, "reference_images": True,
                                                "max_reference_images": 2, "source": "doc"},
    })
    api.add_i18n(
        exact={
            "示例异步生图（测试替身，不联网）": "Example async images (test double, offline)",
            "示例异步视频（测试替身，不联网）": "Example async video (test double, offline)",
            "示例异步配乐（测试替身，不联网）": "Example async music (test double, offline)",
            "提交后给远端任务 id，查一次就完成": "Returns a remote task id; done on the first poll",
            "首帧图生视频；提交后给远端任务 id": "First-frame image to video; returns a remote task id",
            "文生音乐；参考图只收 jpg / png": "Text to music; reference images must be jpg / png",
        },
        patterns=[
            (r"^找不到示例命令 (\S+)：示例来源需要本机装好它$", r"Example command \1 not found: the example source needs it installed on this machine"),
            (r"^示例来源的额度用完了：(.+)$", r"The example source is out of quota: \1"),
        ],
    )
    api.add_failure_hints("config", ["找不到示例命令"])
    api.add_failure_hints("quota", ["示例来源的额度用完了"])
