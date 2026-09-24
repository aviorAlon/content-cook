"""剧本工艺清单的静态判据（docs/script-craft-gap.md）：lint 五条（画面里带字的物件 / 生产说明 / 一镜多运镜 / 冲击后无反应 / 旁白与对白）、
剧本层两条（台词超长 / 开口人数）、交付包里的生成内容标注与工具清单。示例项目对这些新判据零告警（老项目不写新字段就零变化）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from storyvideo import lint as L
from storyvideo import screenplay as SP
from webui import export as export_lib
from webui.problems import problem_card

ROOT = Path(__file__).resolve().parents[1]


def _spec(**extra):
    base = {
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
        "shots": [
            {"id": "S1", "dur": 6, "cast": ["lin"], "shot": "中景，平视", "art": "林小满坐在工位", "motion": "她抬头",
             "lines": [{"who": "lin", "zh": "招财猫大人，求你了，让我下个月涨点工资吧。"}]},
            {"id": "S2", "dur": 5, "cast": ["lin"], "shot": "近景，平视", "art": "林小满看着招财猫", "motion": "她伸手"},
            {"id": "S3", "dur": 5, "cast": ["lin"], "shot": "特写，平视", "art": "手停在半空", "motion": "手指蜷起来"},
        ],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------- lint.py


def test_text_carriers_only_when_undeclared():
    spec = _spec()
    spec["shots"][1]["art"] = "门口挂着招牌，桌上一条短信亮着"
    msgs = L.check_text_carriers(spec)
    assert len(msgs) == 1 and msgs[0].startswith("shot S2: 正文里有文字载体「招牌、短信」") and "readable_text" in msgs[0]
    assert problem_card(msgs[0])["kind"] == "text"
    # 声明了可读文字 / 写了无文字就不提；招财猫不是招牌
    spec["shots"][1]["readable_text"] = [{"surface": "招牌", "text": "开业"}]
    assert L.check_text_carriers(spec) == []
    del spec["shots"][1]["readable_text"]
    spec["shots"][1]["neg_extra"] = "无文字"
    assert L.check_text_carriers(spec) == []
    assert L.check_text_carriers(_spec()) == []


def test_meta_instructions_join_prompt_hygiene():
    spec = _spec()
    spec["shots"][2]["motion"] = "手指蜷起来。让模型和上一镜对齐，后面的剧情会用到这个动作"
    msgs = L.check_prompt_hygiene(spec)
    assert any("生产说明「让模型、后面的剧情」" in m and "design_notes" in m for m in msgs)
    assert problem_card(next(m for m in msgs if "生产说明" in m))["kind"] == "text"
    assert not any("生产说明" in m for m in L.check_prompt_hygiene(_spec()))


def test_two_camera_motion_families_in_one_shot():
    spec = _spec()
    spec["shots"][0]["shot"] = "中景，平视，缓推到她脸上，再环绕半圈"
    msgs = L.check_camera_motion_single(spec)
    assert msgs == ["shot S1: 一镜里同时写了「推、环绕」这几种运镜——模型会二选一或来回晃；挑一个，另一个挪到下一镜"]
    assert problem_card(msgs[0])["kind"] == "camera"
    spec["shots"][0]["shot"] = "中景，平视，缓推到她脸上"
    assert L.check_camera_motion_single(spec) == []
    # 「推门」「拉开抽屉」不是运镜
    spec["shots"][0]["motion"] = "她推门进来，拉开抽屉"
    assert L.check_camera_motion_single(spec) == []


def test_reaction_after_impact_only_when_both_purposes_set():
    spec = _spec()
    spec["shots"][0]["purpose"] = "impact"
    spec["shots"][1]["purpose"] = "pressure"
    msgs = L.check_reaction_after_impact(spec)
    assert len(msgs) == 1 and msgs[0].startswith("shot S1: 这一镜是「冲击」，下一镜 S2 是「施压」，中间没有反应镜")
    assert problem_card(msgs[0])["kind"] == "performance"
    spec["shots"][1]["purpose"] = "reaction"
    assert L.check_reaction_after_impact(spec) == []
    spec["shots"][1]["purpose"] = "aftermath"
    assert L.check_reaction_after_impact(spec) == []
    # 下一镜没写 purpose 就不判；shift 也算强信息
    del spec["shots"][1]["purpose"]
    assert L.check_reaction_after_impact(spec) == []
    spec["shots"][1]["purpose"] = "shift"
    spec["shots"][2]["purpose"] = "detail"
    # S1（冲击）后面是转折、S2（转折）后面是细节：两处都提
    assert [m[:8] for m in L.check_reaction_after_impact(spec)] == ["shot S1:", "shot S2:"]


def test_voice_over_overlap_overflow_and_missing_shot():
    spec = _spec(voice_over={"edge": {"voice": "zh-CN-YunxiNeural"}, "lines": [
        {"shot": "S1", "at": 0.6, "zh": "招财猫说，这已经是它能做的极限了。"},     # 与对白同一时段
        {"shot": "S2", "at": 3.0, "zh": "凌晨一点，林小满还在加班，办公室只剩她一个。"},   # 超出本镜
        {"shot": "S9", "at": 0.0, "zh": "这一句指到不存在的镜。"},
        {"shot": "S3", "at": 0.0, "zh": "短短一句。"},
    ]})
    msgs = L.check_voice_over(spec)
    joined = "\n".join(msgs)
    assert "shot S1: 旁白「招财猫说，这已经是它能做…」" in joined and "与对白（约 0.8–" in joined and "同一时段" in joined
    assert "shot S2: 旁白「凌晨一点，林小满还在加班…」" in joined and "会超出本镜 5s" in joined
    assert "全集：旁白第 3 句「这一句指到不存在的镜。…」指到不存在的镜 S9" in joined
    assert not any("S3" in m for m in msgs)
    assert {problem_card(m)["kind"] for m in msgs} == {"dialogue"}
    assert L.check_voice_over(_spec()) == []


def test_example_episode_has_no_new_warnings():
    spec = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    assert L.check_text_carriers(spec) == []
    assert not any("生产说明" in m for m in L.check_prompt_hygiene(spec))
    assert L.check_camera_motion_single(spec) == []
    assert L.check_reaction_after_impact(spec) == []
    assert L.check_voice_over(spec) == []


# ---------------------------------------------------------------------------- screenplay.py


def _doc(lines):
    return {
        "title": "t", "logline": "l", "contract": {},
        "cast": [{"id": "ye", "name": "叶澄"}, {"id": "qi", "name": "齐曼"}, {"id": "lb", "name": "老板"}, {"id": "lr", "name": "路人"}],
        "scenes": [{"id": "S01", "heading": "内 · 厨房 · 清晨", "beats": ["齐曼把订单拍在桌上"], "dialogue": lines}],
    }


def test_screenplay_line_length_and_speaking_cast():
    doc = _doc([{"who": "qi", "zh": "把完整的排班记录拿出来，现在就拿，我一个字都不想再听你解释了。"}, {"who": "ye", "zh": "记录就在这里。"}])
    msgs = SP.check_line_length(doc)
    assert msgs == ["scene S01: 「齐曼」的一句台词 31 字——一口气说不完，拆镜时要占满一整镜；拆成两句，或把说明性的半句改成动作"]
    assert problem_card(msgs[0])["scope"] == "scene"
    assert SP.check_speaking_cast(doc) == []
    many = _doc([{"who": w, "zh": "嗯。"} for w in ("ye", "qi", "lb", "lr", "旁白")])
    msgs = SP.check_speaking_cast(many)
    assert msgs == ["全集：剧本里有 4 个人物开口说话（叶澄、齐曼、老板、路人）——竖屏一集观众只记得住三张脸，模型也更容易把人认混；把只说一两句的人改成不开口的在场者，或合并成一个人"]
    assert problem_card(msgs[0])["kind"] == "screenplay"
    assert SP.check_line_length(many) == []
    # 都进 lint_screenplay
    assert any("开口说话" in m for m in SP.lint_screenplay(many))
    # 形状不对的原始文件只是跳过那条判据，不炸
    assert SP.check_line_length({"scenes": [{"dialogue": [{"who": "x", "zh": None}]}], "cast": "no"}) == []
    assert SP.check_speaking_cast({"scenes": "nope"}) == []


# ---------------------------------------------------------------------------- 交付包：AI 生成说明与工具清单


def _isolated_settings(monkeypatch, tmp_path, roles=None):
    """工具清单会读「模型设置」：指到临时目录，不读这台机器的真设置；``roles`` 给了就写一份设置文件。"""

    import os

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("STORYSTUDIO_HOME", raising=False)
    for key in [k for k in os.environ if k.startswith("STORYSTUDIO_VOICE_")]:
        monkeypatch.delenv(key)
    path = tmp_path / "settings.json"
    if roles is not None:
        path.write_text(json.dumps({"roles": roles}), encoding="utf-8")
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(path))
    return path


def test_ai_disclosure_from_spec_and_settings(tmp_path, monkeypatch):
    _isolated_settings(monkeypatch, tmp_path)
    spec = _spec(image_params={"model": "@image"}, video_params={"model": "seedance-1.5-pro"},
                 voice_over={"engine": "", "edge": {"voice": "zh-CN-YunxiNeural"}, "lines": [{"shot": "S3", "at": 0.0, "zh": "一句旁白。"}]},
                 post_graphics={"dubbing": {"mode": "all"}})
    d = export_lib.ai_disclosure(spec)
    assert d["statement"].startswith("本片由 AI 辅助生成") and d["statement_en"].startswith("This episode")
    tools = d["tools"]
    assert set(tools) == {"script", "frames", "videos", "narration", "dubbing", "music"}
    assert tools["videos"]["model"] == "seedance-1.5-pro"        # 项目文件里写了具体模型名就用它
    assert tools["frames"]["model"] == "unknown" and tools["frames"]["configured"] is False   # 别名 + 没配设置 = 说不清
    # edge 整块没写的语速 / 音调按合成时的默认值补上（溯源写实际念出来的声线）
    assert tools["narration"] == {"label": "旁白", "engine": "edge", "voice": "zh-CN-YunxiNeural", "rate": "-6%", "pitch": "-8Hz",
                                  "used": True, "line_voices": []}
    assert tools["dubbing"] == {"label": "台词配音", "mode": "all", "voices": []}   # 角色都没绑配音声线
    assert tools["music"] == {"label": "配乐", "used": False, "tracks": []}
    assert "账本里没有记录" in d["basis"] and tools["frames"]["from"] == "settings" and tools["frames"]["sources"] == []
    silent = export_lib.ai_disclosure(_spec())
    assert silent["tools"]["narration"]["used"] is False and silent["tools"]["narration"]["engine"] == "none"
    assert silent["tools"]["narration"]["voice"] == "" and silent["tools"]["narration"]["line_voices"] == []
    assert silent["tools"]["dubbing"]["mode"] == "off"


def test_disclosure_markdown_lists_tools(tmp_path, monkeypatch):
    _isolated_settings(monkeypatch, tmp_path)   # 旁白没写引擎：设置里没配语音角色才是 edge
    spec = _spec(voice_over={"lines": [{"shot": "S3", "at": 0.0, "zh": "一句旁白。"}]}, title_zh="加班夜")
    text = export_lib._disclosure_markdown(export_lib.ai_disclosure(spec), spec)
    assert text.startswith("# AI 生成说明与工具清单 · 加班夜")
    assert "【AI 生成说明】" in text and "【用到的生成工具】" in text and "- 旁白：edge" in text and "【备案】上线前" in text
    # 没有账本：三行都按设置（这台机器配了什么就写什么，这里只看口径），尾注说清账本里没有记录
    assert len(re.findall(r"^- (?:剧本 / 分镜起草|首帧|视频)：.+（按导出时的设置）$", text, re.M)) == 3 and "账本里没有记录" in text
    none = export_lib._disclosure_markdown(export_lib.ai_disclosure(_spec()), _spec())
    assert "- 旁白：本集没有旁白" in none and "- 配乐：本集没有配乐" in none


def _voiced_spec(**extra):
    """两个角色绑了配音声线（一个在配音镜里开口、一个没开口），旁白整块 + 一句逐句换声线，三条配乐。"""

    cast = {
        "lin": {"name": "林小满", "block": "黑发。",
                "voice_tts": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%", "pitch": "-6Hz"}},
        "chen": {"name": "陈默", "block": "短发。", "voice_tts": {"voice": "zh-CN-YunxiNeural"}},
        "passerby": {"name": "路人", "block": "背影。"},
    }
    voice_over = {
        "engine": "edge", "edge": {"voice": "zh-CN-YunyangNeural", "rate": "-4%"},
        "lines": [
            {"shot": "S2", "at": 0.5, "zh": "第一句。"},
            {"shot": "S3", "at": 0.0, "zh": "第二句。", "edge": {"voice": "zh-CN-XiaoxiaoNeural", "pitch": "+2Hz"}},
            {"shot": "S3", "at": 1.0, "zh": "  ", "edge": {"voice": "zh-CN-XiaoyiNeural"}},   # 空句不合成，覆盖也不算
        ],
    }
    graphics = {
        "dubbing": {"mode": "shots", "shots": ["S1"]},
        "music": [
            {"label": "BGM-A 单音钢琴", "file": "content/assets/EP/music/MU-A-1-abcd1234.mp3", "start": 0},
            {"file": "C:/Users/someone/Music/private.mp3"},
            {"file": "/home/someone/theme.wav"},
        ],
    }
    return _spec(cast=cast, voice_over=voice_over, post_graphics=graphics, **extra)


def test_ai_disclosure_lists_narration_cast_voices_and_music():
    spec = _voiced_spec()
    tools = export_lib.ai_disclosure(spec)["tools"]
    # 逐句换声线没写的语速沿用整块的（合成时叠在同一套 DEFAULT_EDGE + voice_over.edge 上），记实际念出来的
    assert tools["narration"] == {
        "label": "旁白", "engine": "edge", "voice": "zh-CN-YunyangNeural", "rate": "-4%", "pitch": "-8Hz", "used": True,
        "line_voices": [{"index": 1, "shot": "S3", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%", "pitch": "+2Hz"}],
    }
    assert tools["dubbing"] == {"label": "台词配音", "mode": "shots", "voices": [
        {"cast": "lin", "name": "林小满", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%", "pitch": "-6Hz", "used": True},
        {"cast": "chen", "name": "陈默", "engine": "edge", "voice": "zh-CN-YunxiNeural", "rate": "", "pitch": "", "used": False},
    ]}
    # 配乐原样记项目内相对路径；写成本机绝对路径的只留文件名；没写 label 的按合成时的叫法
    assert tools["music"] == {"label": "配乐", "used": True, "tracks": [
        {"label": "BGM-A 单音钢琴", "file": "content/assets/EP/music/MU-A-1-abcd1234.mp3"},
        {"label": "配乐 2", "file": "private.mp3"},
        {"label": "配乐 3", "file": "theme.wav"},
    ]}

    text = export_lib._disclosure_markdown(export_lib.ai_disclosure(spec), spec)
    assert "- 旁白：edge / zh-CN-YunyangNeural（语速 -4%，音调 -8Hz）" in text
    assert "  - 第 2 句（S3）换声线：edge / zh-CN-XiaoxiaoNeural（语速 -4%，音调 +2Hz）" in text
    assert "- 台词配音：shots" in text
    assert "  - 「林小满」（lin）：edge / zh-CN-XiaoxiaoNeural（语速 -4%，音调 -6Hz）\n" in text
    assert "  - 「陈默」（chen，本集没用到）：edge / zh-CN-YunxiNeural\n" in text
    assert "- 配乐：3 条" in text and "  - 「BGM-A 单音钢琴」：content/assets/EP/music/MU-A-1-abcd1234.mp3" in text
    assert "  - 「配乐 2」：private.mp3" in text and "someone" not in text   # 本机路径不进包
    # 这几行的口径（按项目文件、不走账本）单独写一句尾注
    assert "> 旁白、台词配音与配乐按导出时的项目文件记声线与曲目" in text


def test_ai_disclosure_voice_edge_cases():
    # 配音关着：manifest 里仍列出绑定（used=false），md 不列——片子里没有这些声音
    spec = _voiced_spec()
    spec["post_graphics"]["dubbing"] = {"mode": "off"}
    disclosure = export_lib.ai_disclosure(spec)
    assert [v["used"] for v in disclosure["tools"]["dubbing"]["voices"]] == [False, False]
    text = export_lib._disclosure_markdown(disclosure, spec)
    assert "- 台词配音：off" in text and "林小满" not in text
    # 角色没写名字：只写 id
    unnamed = _spec(cast={"lin": {"block": "x", "voice_tts": {"voice": "zh-CN-XiaoxiaoNeural"}},
                          "bob": {"block": "y", "voice_tts": {"voice": "zh-CN-YunxiNeural"}}},
                    post_graphics={"dubbing": {"mode": "all"}})
    text = export_lib._disclosure_markdown(export_lib.ai_disclosure(unnamed), unnamed)
    assert "  - lin：edge / zh-CN-XiaoxiaoNeural\n" in text and "  - bob（本集没用到）：edge / zh-CN-YunxiNeural\n" in text
    # 克隆音色（minimax）：合成时只传音色与语速（语速没写的按 DEFAULT_EDGE 的 -6%，post_layers 就是这么传的），音调不传；
    # 逐句覆盖只在 edge / 没写引擎时生效
    cloned = _spec(voice_over={"engine": "minimax", "edge": {"voice": "clone-1"},
                               "lines": [{"shot": "S1", "zh": "一句。", "edge": {"voice": "zh-CN-XiaoyiNeural"}}]})
    narration = export_lib.ai_disclosure(cloned)["tools"]["narration"]
    assert narration == {"label": "旁白", "engine": "minimax", "voice": "clone-1", "rate": "-6%", "pitch": "", "used": True,
                         "line_voices": []}
    # 写坏的配音配置（duck 不是数）不让导出失败，只是认不出谁开口
    broken = _voiced_spec()
    broken["post_graphics"]["dubbing"]["duck"] = "loud"
    assert [v["used"] for v in export_lib.ai_disclosure(broken)["tools"]["dubbing"]["voices"]] == [False, False]


def test_ai_disclosure_line_voice_records_what_is_synthesized():
    """逐句换声线按合成时的规则合并：DEFAULT_EDGE ← 整块 voice_over.edge ← 这一句写了的项（空值不算）。"""

    spec = _spec(voice_over={"engine": "edge", "edge": {"voice": "zh-CN-YunyangNeural", "rate": "-4%"}, "lines": [
        {"shot": "S1", "at": 0.0, "zh": "第一句。"},
        {"shot": "S1", "at": 2.0, "zh": "第二句。", "edge": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "", "pitch": "+2Hz"}},
        {"shot": "S2", "at": 0.0, "zh": "第三句。", "edge": {"rate": " +10% "}},   # 只换语速：音色仍是整块的
        {"shot": "S2", "at": 2.0, "zh": "第四句。", "edge": {"voice": "  "}},       # 全是空值：不算换声线
    ]})
    rows = export_lib.ai_disclosure(spec)["tools"]["narration"]["line_voices"]
    assert rows == [
        {"index": 1, "shot": "S1", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%", "pitch": "+2Hz"},
        {"index": 2, "shot": "S2", "engine": "edge", "voice": "zh-CN-YunyangNeural", "rate": "+10%", "pitch": "-8Hz"},
    ]


def _render_route(monkeypatch, spec, tmp_path):
    """后期合成真正走哪条合成路（post_layers.render_vo_lines 的 default_tts）：设置里的语音记 settings，edge-tts 记 edge。"""

    from types import SimpleNamespace

    import genkit.router as router
    from genkit.adapters import speech
    from storyvideo import post_layers as P

    routes = []

    class Speech:
        def synthesize(self, text, destination, voice=None, **kwargs):
            routes.append(("settings", voice))
            destination.write_bytes(b"x")

    def edge_synth(self, text, destination, voice=None, **kwargs):
        routes.append(("edge", voice))
        destination.write_bytes(b"x")

    monkeypatch.setattr(router, "build_provider", lambda *a, **k: SimpleNamespace(speech=lambda: Speech()))
    monkeypatch.setattr(speech.EdgeSpeech, "synthesize", edge_synth)
    monkeypatch.setattr(P, "probe_media", lambda *a, **k: SimpleNamespace(duration_seconds=1.0))
    P.render_vo_lines(spec, tmp_path / "vo")
    return routes


_GATEWAY_VOICE = {"voice": {"adapter": "openai_speech", "api_key": "sk-test", "model": "gpt-4o-mini-tts"}}


def test_ai_disclosure_unset_engine_follows_the_settings_voice_role(tmp_path, monkeypatch):
    """旁白没写引擎、设置里配了能用的非 edge 语音角色：后期合成走设置里的语音，溯源也得这么记，不能写成 edge。"""

    _isolated_settings(monkeypatch, tmp_path, roles=_GATEWAY_VOICE)
    spec = _spec(voice_over={"lines": [{"shot": "S1", "at": 0.0, "zh": "第一句。"}]})
    assert _render_route(monkeypatch, spec, tmp_path) == [("settings", "zh-CN-YunxiNeural")]

    narration = export_lib.ai_disclosure(spec)["tools"]["narration"]
    # 设置里的语音只收到音色（cfg 里的，没写就是 DEFAULT_EDGE 的名字），语速 / 音调不传；来源与模型按导出时的设置
    assert narration == {"label": "旁白", "engine": "settings", "adapter": "openai_speech", "model": "gpt-4o-mini-tts",
                         "voice": "zh-CN-YunxiNeural", "rate": "", "pitch": "", "used": True, "line_voices": [],
                         "from": "settings"}
    text = export_lib._disclosure_markdown(export_lib.ai_disclosure(spec), spec)
    assert "- 旁白：settings / openai_speech / gpt-4o-mini-tts / zh-CN-YunxiNeural（按导出时的设置）\n" in text

    # 逐句换声线不看设置、总是走 edge-tts，叠在 DEFAULT_EDGE + 整块 edge 上
    spec["voice_over"]["lines"].append({"shot": "S2", "at": 0.0, "zh": "第二句。", "edge": {"voice": "zh-CN-XiaoxiaoNeural"}})
    rows = export_lib.ai_disclosure(spec)["tools"]["narration"]["line_voices"]
    assert rows == [{"index": 1, "shot": "S2", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-6%", "pitch": "-8Hz"}]


def test_ai_disclosure_narration_engine_rules(tmp_path, monkeypatch):
    lines = [{"shot": "S1", "at": 0.0, "zh": "一句。"}]

    def narration(voice_over, roles=None, raw=None):
        path = _isolated_settings(monkeypatch, tmp_path, roles=roles)
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        return export_lib.ai_disclosure(_spec(voice_over=voice_over))["tools"]["narration"]

    # 写明 edge：不看设置
    assert narration({"engine": "edge", "lines": lines}, roles=_GATEWAY_VOICE)["engine"] == "edge"
    # 没写引擎，设置里的语音是 edge_tts、或缺项用不了：合成走 edge-tts
    assert narration({"lines": lines}, roles={"voice": {"adapter": "edge_tts", "model": "zh-CN-YunjianNeural"}})["engine"] == "edge"
    assert narration({"lines": lines}, roles={"voice": {"adapter": "openai_speech", "model": "gpt-4o-mini-tts"}})["engine"] == "edge"
    # 写明 settings：合成不看设置配没配全，照样按设置记
    row = narration({"engine": "settings", "lines": lines}, roles={"voice": {"adapter": "openai_speech"}})
    assert (row["engine"], row["adapter"], row["model"], row["from"]) == ("settings", "openai_speech", "gpt-4o-mini-tts", "settings")
    # 设置文件读不出来：说不清就写 unknown，不猜 edge
    assert narration({"lines": lines}, raw="{broken")["engine"] == "unknown"
    row = narration({"engine": "settings", "lines": lines}, raw="{broken")
    assert (row["engine"], row["adapter"], row["model"]) == ("settings", "unknown", "unknown")


def _ledger(project_root, run_id, jobs):
    """在 runtime 的账本位置建一个 run：jobs = [(step_id, kind, provider, model)]，None 表示提交了但没记来源（老行）。"""

    from genkit.store import RunStore
    from storyvideo.runtime import ledger_path

    run_dir = project_root / "output" / "story_video" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with RunStore(ledger_path(project_root)) as store:
        store.create_run(run_id, "storyvideo", run_id, "in", "plan", run_dir)
        for index, (step_id, kind, provider, model) in enumerate(jobs):
            store.ensure_job(run_id, step_id, kind, f"h{index}", f"{kind}-{index}.bin")
            if provider is None and model is None:
                store.mark_submitting(run_id, step_id)
            else:
                store.mark_submitting(run_id, step_id, provider=provider, model=model)
            store.mark_submitted(run_id, step_id, f"{run_id}-task-{index}", "finished")   # 任务 id 全账本唯一（kind, task_id）


def test_ai_disclosure_prefers_ledger_sources(tmp_path, monkeypatch):
    from webui import deps

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    _ledger(tmp_path, "EP-L", [
        ("frame-S1", "image", "ark_images", "doubao-seedream-4-0-250828"),
        ("frame-S2", "image", "ark_images", "doubao-seedream-4-0-250828"),
        ("frame-S3", "image", "openai_images", "gpt-image-1"),
        ("video-S1", "video", "openai_video", "sora-2"),
    ])
    spec = _spec(id="EP-L", output_root=str(tmp_path / "output" / "story_video" / "EP-L"),
                 image_params={"model": "@image"}, video_params={"model": "seedance-1.5-pro"})
    d = export_lib.ai_disclosure(spec)
    assert "首帧 / 视频按账本逐 job 记录" in d["basis"] and "以各行标注为准" in d["basis"]   # 混合口径：剧本起草仍按设置
    frames, videos = d["tools"]["frames"], d["tools"]["videos"]
    assert frames["from"] == "ledger" and frames["sources"] == [
        {"provider": "ark_images", "model": "doubao-seedream-4-0-250828", "count": 2},
        {"provider": "openai_images", "model": "gpt-image-1", "count": 1},
    ]
    assert frames["adapter"] == "ark_images" and frames["model"] == "doubao-seedream-4-0-250828"   # 最多的那家
    # 视频按账本是 openai_video / sora-2，项目文件里写的 seedance 只是导出时的别名解析，不算数
    assert videos["from"] == "ledger" and videos["adapter"] == "openai_video" and videos["model"] == "sora-2"
    assert d["tools"]["script"]["from"] == "settings"
    text = export_lib._disclosure_markdown(d, spec)
    assert "- 首帧：ark_images / doubao-seedream-4-0-250828 ×2, openai_images / gpt-image-1 ×1（按账本）" in text
    assert "- 视频：openai_video / sora-2 ×1（按账本）" in text
    assert "- 剧本 / 分镜起草：unknown / unknown（按导出时的设置）" in text
    assert "首帧 / 视频按账本逐 job 记录" in text and "以各行标注为准" in text


def test_ai_disclosure_falls_back_for_old_schema_ledger(tmp_path, monkeypatch):
    """schema 1 的老账本：只读打开不迁移 → 退回按设置；可写打开一次（服务启动也会做这一下）升级后，整集都是老行
    （提交过但没记来源）照样按设置。账本按 spec 自带的 project_root 找，不看服务的 PROJECT_ROOT。"""

    from genkit.store import RunStore
    from storyvideo.runtime import ledger_path
    from tests.test_small_fixes import _old_ledger
    from webui import deps

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path / "elsewhere")
    run_dir = tmp_path / "output" / "story_video" / "EP1"
    run_dir.mkdir(parents=True)
    _old_ledger(ledger_path(tmp_path), run_dir, user_version=1)
    spec = _spec(id="EP1", project_root=str(tmp_path), video_params={"model": "seedance-1.5-pro"})
    before = export_lib.ai_disclosure(spec)
    assert "账本里没有记录" in before["basis"] and before["tools"]["frames"]["from"] == "settings" and before["tools"]["frames"]["sources"] == []
    RunStore(ledger_path(tmp_path)).close()
    after = export_lib.ai_disclosure(spec)
    assert "账本里没有记录" in after["basis"] and after["tools"]["frames"]["from"] == "settings"
    assert after["tools"]["frames"]["sources"] == [{"provider": "", "model": "", "count": 1}]   # 读到了账本，只是老行没记来源
    assert after["tools"]["videos"]["sources"] == [] and after["tools"]["videos"]["model"] == "seedance-1.5-pro"
    text = export_lib._disclosure_markdown(after, spec)
    assert "- 首帧：" in text and "（按账本）" not in text


def test_ai_disclosure_falls_back_when_ledger_has_no_record(tmp_path, monkeypatch):
    from webui import deps

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    # 老账本里的老行：提交过但两列是 NULL → 不算有记录，退回按设置；只提交了首帧的集，视频那行也按设置
    _ledger(tmp_path, "EP-O", [("frame-S1", "image", None, None), ("frame-S2", "image", "ark_images", "m")])
    _ledger(tmp_path, "EP-N", [("frame-S1", "image", None, None)])
    old = export_lib.ai_disclosure(_spec(id="EP-N", video_params={"model": "seedance-1.5-pro"}))
    assert "账本里没有记录" in old["basis"] and old["tools"]["frames"]["from"] == "settings"
    assert old["tools"]["frames"]["sources"] == [{"provider": "", "model": "", "count": 1}]
    assert old["tools"]["videos"]["sources"] == [] and old["tools"]["videos"]["model"] == "seedance-1.5-pro"
    mixed = export_lib.ai_disclosure(_spec(id="EP-O"))
    assert "首帧 / 视频按账本逐 job 记录" in mixed["basis"] and mixed["tools"]["frames"]["adapter"] == "ark_images"
    assert mixed["tools"]["videos"]["from"] == "settings"
    assert "- 首帧：unknown / unknown ×1, ark_images / m ×1（按账本）" in export_lib._disclosure_markdown(mixed, _spec(id="EP-O"))
    # 账本里没有这个 run / 账本文件坏了 / 账本不存在：都退回按设置，绝不抛
    assert "账本里没有记录" in export_lib.ai_disclosure(_spec(id="EP-X"))["basis"]
    from storyvideo.runtime import ledger_path

    ledger_path(tmp_path).write_bytes(b"not a database")
    assert "账本里没有记录" in export_lib.ai_disclosure(_spec(id="EP-O"))["basis"]
    ledger_path(tmp_path).unlink()
    assert "账本里没有记录" in export_lib.ai_disclosure(_spec(id="EP-O"))["basis"]
