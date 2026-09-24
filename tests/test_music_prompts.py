"""配乐提示词编译（docs/music-spec.md §2）：顺序、中英两版、空字段跳过、按 prompt_language 取。"""

from __future__ import annotations

from music import prompts as P
from music import spec as S

FULL = {
    "title": "睡前摇篮曲",
    "duration": 120,
    "brief": {
        "usage": "绘本睡前朗读的底噪", "mood": ["温柔", "安定"], "genre": "摇篮曲",
        "instruments": ["音乐盒", "轻钢琴"], "bpm": 70, "structure": "前 8 秒渐入，最后 3 秒渐弱到无",
        "avoid": ["鼓组", "人声"],
    },
}


def _spec(tmp_path, **over):
    return S.normalize({**FULL, **over}, project_root=tmp_path, spec_id="MU-1")


def test_zh_order_follows_the_contract(tmp_path):
    text = P.compile_zh(_spec(tmp_path))
    assert text.startswith("用途：绘本睡前朗读的底噪")
    assert text.endswith("不要：鼓组、人声。")
    order = [text.index(x) for x in ("用途：", "体裁：", "情绪：", "乐器：", "速度：", "时长：", "结构：", "不要：")]
    assert order == sorted(order)
    assert "约 70 BPM" in text and "约 120 秒" in text
    assert "纯器乐、无人声" in text


def test_en_mirrors_zh(tmp_path):
    text = P.compile_en(_spec(tmp_path))
    assert text.startswith("Use: 绘本睡前朗读的底噪")
    assert text.endswith("Avoid: 鼓组, 人声.")
    order = [text.index(x) for x in ("Use:", "Genre:", "Mood:", "Instruments:", "Tempo:", "Length:", "Structure:", "Avoid:")]
    assert order == sorted(order)
    assert "around 70 BPM" in text and "about 120 seconds" in text


def test_empty_fields_are_skipped(tmp_path):
    spec = _spec(tmp_path, brief={"usage": "", "genre": "氛围", "mood": [], "instruments": [], "avoid": []})
    zh = P.compile_zh(spec)
    assert "用途：" not in zh and "情绪：" not in zh and "不要：" not in zh and "速度：" not in zh
    assert zh.startswith("体裁：氛围")
    en = P.compile_en(spec)
    assert "Use:" not in en and "Avoid:" not in en and en.startswith("Genre: 氛围")


def test_language_switch_and_both(tmp_path):
    zh_spec = _spec(tmp_path)
    en_spec = _spec(tmp_path, prompt_language="en")
    assert P.compile_prompt(zh_spec) == P.compile_zh(zh_spec)
    assert P.compile_prompt(en_spec) == P.compile_en(en_spec)
    assert P.compile_prompt(zh_spec, language="en") == P.compile_en(zh_spec)
    both = P.compile_both(zh_spec)
    assert set(both) == {"zh", "en"} and both["zh"] != both["en"]


def test_has_intent_ignores_the_fixed_tail(tmp_path):
    """只填时长 / 只有固定尾句不算"说清楚要什么音乐"——不该拿去花钱。"""

    empty = _spec(tmp_path, brief={"usage": "", "genre": "", "mood": [], "instruments": [], "avoid": []}, duration=300)
    assert P.has_intent(empty) is False
    assert "纯器乐" in P.compile_zh(empty)          # 编译出来只剩尾句
    assert P.has_intent(_spec(tmp_path)) is True
    for key, value in (("usage", "睡前"), ("genre", "摇篮曲"), ("mood", ["温柔"]), ("instruments", ["钢琴"]), ("structure", "渐入")):
        one = _spec(tmp_path, brief={"usage": "", "genre": "", "mood": [], "instruments": [], "avoid": [], key: value})
        assert P.has_intent(one) is True, key
    # avoid / bpm 单独填不算意图
    assert P.has_intent(_spec(tmp_path, brief={"usage": "", "genre": "", "mood": [], "instruments": [], "avoid": ["鼓组"], "bpm": 90})) is False


def test_compile_is_pure(tmp_path):
    spec = _spec(tmp_path)
    assert P.compile_zh(spec) == P.compile_zh(spec)
    assert P.compile_zh(dict(spec)) == P.compile_zh(spec)
