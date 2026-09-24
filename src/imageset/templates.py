"""模板注册（契约 §2 ``templates.py``、§3 / §4 的七个模板）。

``Template(id, module, name, english, description, styles[], options_schema, expand, post, post_extra, export_layout)``：

* ``styles``：风格预设 ``{id, name, english, anchor_zh, anchor_en}``；spec 的 ``style`` 选一个，或 ``custom`` + 自写 ``style_anchor``。
* ``options_schema``：前端可直接渲染的小表单描述 ``{key, label, label_en, type: list|bool|string|int, default, choices?, min?, max?, hint?}``。
* ``expand(spec) -> items[]``：只产出每张图的 label / prompt / negative / aspect_ratio / meta，不调模型。
* ``post(spec, item, path, ctx) -> outputs[]`` / ``post_extra(spec, items, ctx) -> outputs[]``：Pillow 后处理（post.py），
  每条产物 ``{item, role, file, width, height, bytes}``。
* ``export_layout``：``role → 包内路径模板``（``{item}`` = 图片 id），export.py 按它组织 zip。

提示词里的画面描写写具体材料与颜色（十六进制），不写「精美 / 高质量」；文字一律不让模型画。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from imagecraft import cards as CARDS

from . import post as P
from . import prompts as PR

# ---------------------------------------------------------------------------- 风格预设

STICKER_STYLES: list[dict[str, str]] = [
    {"id": "flat_cute", "name": "扁平可爱", "english": "Flat cute",
     "anchor_zh": "扁平矢量风表情贴纸：粗细均匀的深棕描边 #3B2A20，两三层平涂色块，脸大身小的二头身，眼睛是大而简洁的圆点，脸颊两团淡粉 #F7B8C4",
     "anchor_en": "Flat vector sticker style: even dark-brown outline #3B2A20, two or three flat color layers, two-head-tall chibi proportion with a big face, large simple dot eyes, soft pink #F7B8C4 cheeks"},
    {"id": "line_doodle", "name": "线稿涂鸦", "english": "Line doodle",
     "anchor_zh": "手绘涂鸦线稿贴纸：黑色马克笔线条 #111111，粗细略有起伏，只有一两处单色填充，纸面干净",
     "anchor_en": "Hand-drawn doodle line sticker: black marker lines #111111 with slight weight variation, only one or two spots of single-color fill, clean paper"},
    {"id": "chibi_3d", "name": "Q 版 3D", "english": "Chibi 3D",
     "anchor_zh": "Q 版 3D 渲染贴纸：二头身，光滑哑光黏土质感，柔和顶光加一圈轻微边缘光，颜色饱和",
     "anchor_en": "Chibi 3D rendered sticker: two-head-tall, smooth matte clay material, soft top light with a thin rim light, saturated colors"},
    {"id": "pixel", "name": "像素", "english": "Pixel",
     "anchor_zh": "像素风贴纸：32×32 网格感的清晰像素块，无抗锯齿，16 色限定调色板，像素边缘干净",
     "anchor_en": "Pixel-art sticker: crisp blocks on a 32x32 grid feel, no anti-aliasing, a limited 16-color palette, clean pixel edges"},
]

AVATAR_STYLES: list[dict[str, str]] = [
    {"id": "clean_studio", "name": "干净棚拍底", "english": "Clean studio",
     "anchor_zh": "单色米灰背景 #EDE7DD，正面柔光箱主光，轻微轮廓光把人物从背景里分开",
     "anchor_en": "Plain warm-gray background #EDE7DD, frontal softbox key light, a subtle rim light separating the figure from the background"},
    {"id": "soft_gradient", "name": "柔和渐变底", "english": "Soft gradient",
     "anchor_zh": "从 #F6E7D8 到 #D9E4F5 的对角渐变背景，柔光，画面干净",
     "anchor_en": "Diagonal gradient background from #F6E7D8 to #D9E4F5, soft light, clean frame"},
    {"id": "scene", "name": "带环境", "english": "In a setting",
     "anchor_zh": "人物所在环境轻微虚化、景深浅，环境色调与人物服装呼应",
     "anchor_en": "The surrounding setting softly blurred with shallow depth of field, its palette echoing the outfit"},
]

PORTRAIT_STYLES: list[dict[str, str]] = [
    {"id": "studio_soft", "name": "柔光棚拍", "english": "Soft studio",
     "anchor_zh": "影楼柔光棚拍：大柔光箱 45 度主光、白色反光板补光、背景干净无杂物，85mm 人像镜头，肤色真实",
     "anchor_en": "Studio portrait with a large softbox key at 45 degrees, white bounce fill, clean uncluttered backdrop, 85mm portrait lens, true-to-life skin"},
    {"id": "natural_window", "name": "窗光自然", "english": "Window light",
     "anchor_zh": "室内窗光：侧面大窗的漫射日光，浅色亚麻织物，阴影柔和过渡，白平衡偏暖",
     "anchor_en": "Indoor window light: diffused daylight from a large side window, light linen fabrics, gently graded shadows, slightly warm white balance"},
    {"id": "film_warm", "name": "暖调胶片", "english": "Warm film",
     "anchor_zh": "暖调胶片质感：Portra 400 风格的色彩，细腻颗粒，高光柔和，肤色偏暖不偏黄",
     "anchor_en": "Warm film look: Portra 400-style color, fine grain, soft highlights, warm but not yellow skin"},
]

IMAGE_STYLES: list[dict[str, str]] = [
    {"id": "photo_real", "name": "真实照片", "english": "Photographic",
     "anchor_zh": "真实摄影质感：自然的光比、清楚的材质纹理、颜色克制不过饱和，画面干净",
     "anchor_en": "Photographic realism: natural contrast, clear material texture, restrained color, clean frame"},
    {"id": "cinematic", "name": "电影感", "english": "Cinematic",
     "anchor_zh": "电影感画面：单一主光从侧面打来，暗部保留细节，浅景深，空气里有薄雾，色调偏冷 #2B3A4A",
     "anchor_en": "Cinematic look: a single key light from one side, detail kept in the shadows, shallow depth of field, "
                  "haze in the air, cool #2B3A4A grade"},
    {"id": "illustration", "name": "插画", "english": "Illustration",
     "anchor_zh": "手绘插画：清晰的线条与平涂色块，颜色分三到四层，纸面有轻微颗粒",
     "anchor_en": "Hand-drawn illustration: crisp lines over flat color, three or four color layers, a slight paper grain"},
    {"id": "flat_graphic", "name": "平面图形", "english": "Flat graphic",
     "anchor_zh": "平面图形海报：大色块与几何形状，两三个主色，边缘干净利落，没有渐变噪点",
     "anchor_en": "Flat graphic poster: large color fields and geometric shapes, two or three primary colors, crisp edges, no gradient noise"},
]

WEDDING_STYLES: list[dict[str, str]] = PORTRAIT_STYLES + [
    {"id": "editorial", "name": "杂志感", "english": "Editorial",
     "anchor_zh": "时装杂志感婚纱片：单一硬光源、浓重的明暗对比、构图留大片负空间，色彩克制",
     "anchor_en": "Editorial fashion-magazine wedding look: a single hard light source, strong contrast, generous negative space, restrained color"},
]


# ---------------------------------------------------------------------------- 画面描写表（label → 描写；用户改了 label 就用 label 原文）

EXPRESSIONS: dict[str, tuple[str, str]] = {
    "开心": ("眼睛弯成月牙，嘴角上扬露出笑容，双手在胸前轻轻握拳", "eyes curved into crescents, corners of the mouth lifted in a smile, both hands lightly clenched at the chest"),
    "大笑": ("闭着眼张大嘴哈哈大笑，头微微后仰，一只手拍着肚子", "eyes shut and mouth wide open laughing, head tipped slightly back, one hand patting the belly"),
    "哭": ("大颗眼泪往下掉，嘴角向下，双手捂着眼睛", "big tears rolling down, mouth turned down, both hands covering the eyes"),
    "委屈": ("眉毛下垂，嘴巴撇成倒 V，眼眶泛着泪光，双手绞在一起", "brows drooping, mouth pulled into an upside-down V, eyes glistening, hands wringing together"),
    "生气": ("眉毛竖起，脸颊鼓起泛红，双手叉腰", "brows raised sharply, cheeks puffed and flushed, hands on hips"),
    "惊讶": ("眼睛睁圆，嘴巴张成 O 形，双手举到脸颊两侧", "eyes round, mouth open in an O, both hands raised beside the cheeks"),
    "疑惑": ("一侧眉毛挑起，歪着头，一根手指抵着下巴，眼睛看向斜上方", "one brow raised, head tilted, a finger on the chin, eyes looking up to one side"),
    "害羞": ("脸颊两团红晕，眼睛看向一边，双手食指相抵", "two blushes on the cheeks, eyes looking away, index fingers touching"),
    "无语": ("半闭着眼，嘴巴抿成一条直线，肩膀垮下来", "half-closed eyes, mouth pressed into a flat line, shoulders slumped"),
    "困": ("眼皮耷拉，嘴巴打着哈欠，一只手揉眼睛，头顶飘着一个小小的椭圆睡眠气泡", "drooping eyelids, mouth mid-yawn, one hand rubbing an eye, a small oval sleep bubble floating above the head"),
    "点赞": ("咧嘴笑，一只手大拇指高高竖起，另一只手插腰", "grinning, one thumb held high, the other hand on the hip"),
    "比心": ("双手在胸前比出爱心手势，笑眯眯", "both hands forming a heart at the chest, eyes smiling"),
    "加油": ("双手握拳举到肩膀两侧，眼神坚定，嘴巴用力抿着", "fists raised beside the shoulders, determined eyes, lips pressed firmly"),
    "谢谢": ("双手合十举到胸前，微微鞠躬，眼睛闭着笑", "palms pressed together at the chest, a slight bow, eyes closed in a smile"),
    "晚安": ("抱着一个小枕头，眼睛闭上，嘴角带笑，头微微歪", "hugging a small pillow, eyes closed, a faint smile, head tilted"),
    "再见": ("一只手举高挥动，另一只手在身侧，笑着侧身", "one hand raised waving, the other at the side, smiling and turned slightly away"),
}

THEMES: dict[str, tuple[str, str]] = {
    "咖啡": ("双手捧着一杯冒热气的白色马克杯，眯眼享受", "holding a steaming white mug with both hands, eyes narrowed in enjoyment"),
    "加班": ("坐在笔记本电脑前敲键盘，眼下两道黑眼圈，旁边一盏小台灯", "typing at a laptop, dark circles under the eyes, a small desk lamp beside"),
    "周末": ("躺在一张黄色沙滩椅上，戴墨镜，手里一杯插着吸管的饮料", "lying on a yellow deck chair in sunglasses, a drink with a straw in hand"),
    "早安": ("伸着懒腰，身后一轮橙色太阳，头发翘起一撮", "stretching with arms up, an orange sun behind, a tuft of hair sticking up"),
    "晚安": ("裹在蓝色被子里只露出脸，旁边一个小月亮", "wrapped in a blue blanket with only the face showing, a small moon beside"),
    "摸鱼": ("趴在桌上，手里拿着一条绿色的小鱼玩偶，眼神飘忽", "slumped on a desk holding a small green fish plush, eyes wandering"),
    "干饭": ("捧着一个大碗，筷子夹着面条，嘴巴塞得鼓鼓的", "holding a big bowl, chopsticks lifting noodles, cheeks stuffed"),
    "健身": ("举着一对粉色小哑铃，额头一滴汗，牙关咬紧", "lifting a pair of pink dumbbells, a bead of sweat on the forehead, jaw clenched"),
}

SHAPES: list[dict[str, str]] = [
    {"id": "circle", "name": "圆形", "english": "Circle",
     "zh": "整体轮廓收在一个圆形贴纸里，外围一圈约 3% 宽的白色贴纸描边 #FFFFFF",
     "en": "The whole design sits inside a circular sticker with a white #FFFFFF die-cut border about 3% wide"},
    {"id": "square", "name": "方形", "english": "Square",
     "zh": "方形贴纸，四角微圆，外围一圈白色贴纸描边 #FFFFFF",
     "en": "A square sticker with slightly rounded corners and a white #FFFFFF die-cut border"},
    {"id": "freeform", "name": "异形", "english": "Die-cut",
     "zh": "异形贴纸，白色贴纸描边 #FFFFFF 贴合主体轮廓",
     "en": "A die-cut sticker whose white #FFFFFF border follows the subject's outline"},
]

AVATAR_ART: dict[str, tuple[str, str]] = {
    "写实": ("真实摄影质感，85mm 人像镜头，皮肤纹理与发丝清晰", "photographic realism, 85mm portrait lens, visible skin texture and hair strands"),
    "油画": ("厚涂油画，可见笔触与画布纹理，暖色调", "impasto oil painting with visible brushstrokes and canvas texture, warm palette"),
    "水彩": ("湿画法水彩，边缘自然晕染，冷压水彩纸纹", "wet-on-wet watercolor with soft bleeding edges, cold-pressed paper texture"),
    "赛博": ("赛博朋克，霓虹青 #00E5FF 与品红 #FF2BD6 双色光打在脸上，背景电路光纹", "cyberpunk, neon cyan #00E5FF and magenta #FF2BD6 dual light on the face, circuit-like glow in the background"),
    "国风": ("中国工笔风，细线勾勒、淡彩渲染，宣纸质感", "Chinese gongbi style, fine ink outlines with light color washes, rice-paper texture"),
    "像素": ("像素画，清晰像素块，16 色调色板", "pixel art with crisp blocks and a 16-color palette"),
    "黏土": ("定格动画黏土质感，表面有手作的指纹痕迹，柔和棚光", "stop-motion clay look with handmade fingerprint texture, soft studio light"),
    "剪纸": ("分层剪纸，纸层之间有柔和投影，色纸饱和", "layered paper cut-out with soft shadows between layers, saturated colored paper"),
    "线稿": ("单色线稿，细而均匀的墨线 #1A1A1A，留白多", "monochrome line drawing, thin even ink lines #1A1A1A, generous white space"),
}

CROPS: list[dict[str, str]] = [
    {"id": "headshot", "name": "头肩", "english": "Head and shoulders", "zh": "头肩像", "en": "head-and-shoulders portrait"},
    {"id": "half", "name": "半身", "english": "Half body", "zh": "半身像，双手可见", "en": "half-body portrait with both hands visible"},
]

NEWBORN_SCENES: dict[str, tuple[str, str]] = {
    "襁褓 + 小鹿玩偶": ("婴儿裹在米白色针织襁褓里安睡，身旁靠着一只棕色绒布小鹿玩偶，底下铺奶油色羊羔绒毯",
                   "the baby sleeping wrapped in an off-white knit swaddle, a brown plush fawn resting beside, on a cream sherpa blanket"),
    "云朵毯": ("婴儿趴在蓬松的白色云朵形状毛毯上，背景淡蓝 #DCE9F5，周围几朵棉花做的小云",
             "the baby lying on a fluffy white cloud-shaped blanket, pale blue #DCE9F5 backdrop, a few small cotton clouds around"),
    "月亮道具": ("婴儿躺在木质弯月摇篮道具里，裹着浅黄色包巾，背景深蓝夜色 #1F2A44 带柔和的星点",
              "the baby lying in a wooden crescent-moon cradle prop wrapped in a pale yellow wrap, deep navy #1F2A44 night backdrop with soft star points"),
    "姓名字母块（留空位）": ("婴儿侧躺在原木色底板上，身旁摆一排空白的木质小方块，方块表面干净无字，位置留给后期",
                    "the baby lying on its side on a natural wood board, a row of blank wooden blocks beside it with clean faces, left for text added later"),
    "花环": ("婴儿躺在干花花环中央，米色亚麻布底，尤加利叶与浅粉干花，花环离脸有一掌距离",
            "the baby lying in the center of a dried-flower wreath on beige linen, eucalyptus and pale pink dried flowers, the wreath a hand's width away from the face"),
    "父母手心": ("婴儿被父母的双手托在手心，画面只露出父母的手与前臂，背景纯黑 #000000",
               "the baby cradled in the parents' cupped hands, only the hands and forearms visible, pure black #000000 backdrop"),
}

BABY100_SCENES: dict[str, tuple[str, str]] = {
    "百天蛋糕（无火）": ("婴儿坐在奶油色单层蛋糕旁的高脚椅上，蛋糕上没有蜡烛，背景浅粉 #F8DDE3 配几个哑光气球",
                  "the baby in a high chair beside a single-tier cream cake with no candles, pale pink #F8DDE3 backdrop with a few matte balloons"),
    "数字气球「100」（留空位）": ("婴儿坐在米色地毯上，身后一面浅色墙留出大片干净区域给后期叠字，脚边几个奶油色气球",
                       "the baby sitting on a beige rug, a plain light wall behind with a large clean area left for text added later, a few cream balloons at the feet"),
    "抓周预演": ("婴儿趴在木托盘前，托盘里摆着木算盘、毛笔、小书、拨浪鼓",
              "the baby lying on its tummy in front of a wooden tray holding a wooden abacus, a brush, a small book and a rattle drum"),
    "小马甲": ("婴儿穿深蓝色针织小马甲配白衬衫，坐在原木小椅子上",
             "the baby in a navy knit vest over a white shirt, sitting on a small natural-wood chair"),
    "趴姿抬头": ("婴儿趴在白色床单上抬头看向镜头，窗边柔光从一侧打来",
              "the baby on its tummy on a white sheet, head lifted toward the camera, soft window light from one side"),
    "全家福": ("父母抱着婴儿坐在浅灰色沙发上，三人都看向镜头，背景干净",
             "the parents holding the baby on a light gray sofa, all three looking at the camera, clean backdrop"),
}

FIRST_BIRTHDAY_SCENES: dict[str, tuple[str, str]] = {
    "生日帽 + 蛋糕（无火）": ("宝宝戴金色圆锥生日帽坐在小圆桌前，桌上一个奶油蛋糕，没有蜡烛，背景暖黄 #F5E1B8",
                    "the toddler in a gold cone party hat at a small round table with a cream cake and no candles, warm yellow #F5E1B8 backdrop"),
    "抓周物件": ("宝宝坐在红色地毯中央，面前一圈抓周物件：木印章、算盘、毛笔、听诊器、小勺",
              "the toddler sitting in the center of a red rug with a ring of zhuazhou objects in front: a wooden seal, an abacus, a brush, a stethoscope, a small spoon"),
    "学步": ("宝宝扶着木质学步车站立，侧光，背景浅色墙",
            "the toddler standing while holding a wooden walker, side light, plain light wall"),
    "气球墙": ("宝宝坐在一面奶油色与金色气球墙前，气球是哑光乳胶",
             "the toddler sitting in front of a wall of cream and gold matte latex balloons"),
    "唐装或小西装": ("宝宝穿红色缎面唐装坐在中式圈椅上，身后一扇浅色屏风",
                "the toddler in a red satin tang suit sitting on a Chinese round-back chair, a light folding screen behind"),
    "全家福": ("父母各牵宝宝一只手站在浅色背景前，三人看向镜头",
             "the parents each holding one of the toddler's hands, standing before a light backdrop, all three looking at the camera"),
}

WEDDING_SCENES: dict[str, tuple[str, str]] = {
    "教堂": ("新娘与新郎站在教堂彩色玻璃窗前，彩窗的光洒在白色婚纱上", "the bride and groom standing before a church stained-glass window, its colored light falling on the white gown"),
    "海边": ("两人赤脚站在退潮后的海滩上，头纱被风吹起，日落金光从侧后方打来", "the couple barefoot on a beach at low tide, the veil lifted by wind, golden sunset light from behind one side"),
    "城市夜景": ("两人站在天台边，背后是城市灯火，暖色轮廓光", "the couple at the edge of a rooftop with city lights behind, warm rim light"),
    "中式红妆": ("新娘穿红色秀禾服、新郎穿红色长衫马褂，背景朱红木门与两盏灯笼", "the bride in a red xiuhe gown and the groom in a red changshan and magua, a vermilion wooden door and two lanterns behind"),
    "森林": ("两人走在晨雾中的松林小径上，光从树梢漏下", "the couple walking a pine-forest path in morning mist, light filtering through the treetops"),
    "复古胶片": ("两人坐在一辆老式汽车的前盖上，胶片颗粒感，褪色暖调", "the couple sitting on the hood of a vintage car, film grain, faded warm tones"),
    "黑白棚拍": ("黑白影棚肖像，两人额头相抵，单灯侧光", "black-and-white studio portrait, foreheads touching, a single side light"),
    "雨中伞": ("两人共撑一把透明伞站在雨夜街头，路灯光晕", "the couple sharing a clear umbrella on a rainy night street, halos around the streetlights"),
}


# ---------------------------------------------------------------------------- Template


@dataclass
class Template:
    id: str
    module: str
    name: str
    english: str
    description: str
    description_en: str
    styles: list[dict[str, str]]
    options_schema: list[dict[str, Any]]
    expand: Callable[[Mapping[str, Any]], list[dict[str, Any]]]
    post: Callable[..., list[dict[str, Any]]]
    export_layout: dict[str, str]
    post_extra: Callable[..., list[dict[str, Any]]] | None = None
    scene_table: dict[str, tuple[str, str]] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id, "module": self.module, "name": self.name, "english": self.english,
            "description": self.description, "description_en": self.description_en,
            "styles": [{"id": s["id"], "name": s["name"], "english": s["english"], "anchor": s["anchor_zh"], "anchor_en": s["anchor_en"]}
                       for s in self.styles],
            "options_schema": self.options_schema, "export_layout": self.export_layout,
        }


def _style_anchor(spec: Mapping[str, Any], template: Template) -> str:
    """风格锚：模板预设 > 风格卡（DESIGN §41）> 自写。预设永远先查，老项目零变化。"""

    style = str(spec.get("style") or "")
    if style == "custom":
        return str(spec.get("style_anchor") or "")
    zh = PR.is_zh(spec)
    for preset in template.styles:
        if preset["id"] == style:
            return preset["anchor_zh"] if zh else preset["anchor_en"]
    card = PR.style_card(spec)
    if card:
        return CARDS.anchor_text(card, zh)
    return str(spec.get("style_anchor") or "")


def style_cards_for(template: Template) -> list[dict[str, Any]]:
    """这个模板「适合」的风格卡（``fits`` 命中 ``<module>.<id>``）；只做筛选，不做硬限制。"""

    return CARDS.cards_for(fits=f"{template.module}.{template.id}")


def _card_parts(spec: Mapping[str, Any], zh: bool) -> tuple[dict[str, Any] | None, str, str]:
    """(卡, 补强句, 约束句)；没选卡时两句都是空串——``join_zh`` 会跳过空段，提示词一个字不变。"""

    card = PR.style_card(spec)
    return card, PR.card_detail_sentence(card, zh), PR.card_constraint_sentence(card, zh)


def _describe(table: Mapping[str, tuple[str, str]], label: str, zh: bool) -> str:
    entry = table.get(label)
    if entry:
        return entry[0] if zh else entry[1]
    return label


def _item(index: int, label: str, prompt: str, negative: str, aspect: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"I{index:02d}", "label": label, "prompt": prompt, "negative": negative, "aspect_ratio": aspect, "meta": meta}


def _list_option(key: str, label: str, label_en: str, default: list[str], hint: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "label_en": label_en, "type": "list", "default": list(default), "hint": hint}


# ---------------------------------------------------------------------------- sticker · emoji_pack


def _emoji_expand(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = TEMPLATES[("sticker", "emoji_pack")]
    zh = PR.is_zh(spec)
    options = spec.get("options") or {}
    style = _style_anchor(spec, template)
    card, card_detail, card_rule = _card_parts(spec, zh)
    identity = PR.identity_block(spec)
    negative = PR.negative(spec, PR.NEGATIVE_STICKER_ZH if zh else PR.NEGATIVE_STICKER_EN)
    fixed = PR.STICKER_CONSTRAINTS_ZH if zh else PR.STICKER_CONSTRAINTS_EN
    items = []
    for index, label in enumerate(options.get("expressions") or [], 1):
        face = _describe(EXPRESSIONS, label, zh)
        scene = (f"这一张的表情「{label}」：{face}" if zh else f"This sticker's expression, \"{label}\": {face}")
        prompt = PR.assemble([style, card_detail, identity, scene, fixed, card_rule], zh,
                             spec=spec, subject=identity, scene=scene, use_case="WeChat emoji sticker",
                             details=[style] + PR.card_details(card, zh),
                             constraints=[fixed, card_rule], aspect="1:1")
        items.append(_item(index, label, prompt, negative, "1:1",
                           {"expression": label, "caption": label if options.get("with_caption") else ""}))
    return items


def _emoji_post(spec: Mapping[str, Any], item: Mapping[str, Any], path: Path, ctx: P.PostContext) -> list[dict[str, Any]]:
    im = P.to_transparent(P.open_rgba(path))
    base = P.fit_square(im, 1024)
    caption = str((item.get("meta") or {}).get("caption") or "")
    if caption:
        base = P.caption_below(base, caption)
    from PIL import Image

    main = P.save_png(base.resize((P.WECHAT_MAIN, P.WECHAT_MAIN), Image.LANCZOS), ctx.name(item["id"], "main"), max_bytes=P.WECHAT_MAX_BYTES)
    thumb = P.save_png(base.resize((P.WECHAT_THUMB, P.WECHAT_THUMB), Image.LANCZOS), ctx.name(item["id"], "thumb"))
    return [P.output_record(main, role="main", item=item["id"]), P.output_record(thumb, role="thumb", item=item["id"])]


def _emoji_extra(spec: Mapping[str, Any], items: list[Mapping[str, Any]], ctx: P.PostContext) -> list[dict[str, Any]]:
    """横幅 750×400（第一张放大 + 叠标题）与面板图标 50×50（第一张裁头部）。"""

    from PIL import Image

    from .spec import current_path

    first = P.to_transparent(P.open_rgba(current_path(spec, items[0])))
    w, h = P.WECHAT_BANNER
    canvas = Image.new("RGBA", (w, h), (255, 247, 236, 255))
    subject = P.fit_square(first, h - 24, pad=0.04)
    canvas.alpha_composite(subject, (28, 12))
    canvas = P.draw_text_in_box(canvas, str(spec.get("title") or spec.get("id")), (h + 20, 40, w - 30, h - 40), color=(59, 42, 32, 255))
    banner = P.save_png(canvas, ctx.name(None, "banner"))
    icon = P.save_png(P.head_crop(first, P.WECHAT_ICON), ctx.name(None, "icon"))
    return [P.output_record(banner, role="banner", item=None), P.output_record(icon, role="icon", item=None)]


# ---------------------------------------------------------------------------- sticker · sticker_set


def _sticker_expand(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = TEMPLATES[("sticker", "sticker_set")]
    zh = PR.is_zh(spec)
    options = spec.get("options") or {}
    style = _style_anchor(spec, template)
    card, card_detail, card_rule = _card_parts(spec, zh)
    identity = PR.identity_block(spec)
    shape = next((s for s in SHAPES if s["id"] == options.get("shape")), SHAPES[0])
    negative = PR.negative(spec, PR.NEGATIVE_STICKER_ZH if zh else PR.NEGATIVE_STICKER_EN)
    fixed = PR.STICKER_CONSTRAINTS_ZH if zh else PR.STICKER_CONSTRAINTS_EN
    items = []
    for index, label in enumerate(options.get("themes") or [], 1):
        action = _describe(THEMES, label, zh)
        scene = (f"贴图主题「{label}」：{action}，配一件与主题相关的道具" if zh else f"Sticker theme \"{label}\": {action}, with one prop tied to the theme")
        prompt = PR.assemble([style, card_detail, identity, scene, shape["zh"] if zh else shape["en"], fixed, card_rule], zh,
                             spec=spec, subject=identity, scene=scene, use_case="sticker set",
                             details=[style] + PR.card_details(card, zh) + [shape["zh"] if zh else shape["en"]],
                             constraints=[fixed, card_rule], aspect="1:1")
        items.append(_item(index, label, prompt, negative, "1:1", {"theme": label, "shape": shape["id"]}))
    return items


def _sticker_post(spec: Mapping[str, Any], item: Mapping[str, Any], path: Path, ctx: P.PostContext) -> list[dict[str, Any]]:
    im = P.to_transparent(P.open_rgba(path))
    out = []
    for size in P.STICKER_SIZES:
        saved = P.save_png(P.fit_square(im, size, pad=0.03), ctx.name(item["id"], f"s{size}"))
        out.append(P.output_record(saved, role=f"s{size}", item=item["id"]))
    return out


# ---------------------------------------------------------------------------- sticker · avatar_set


def _avatar_expand(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = TEMPLATES[("sticker", "avatar_set")]
    zh = PR.is_zh(spec)
    options = spec.get("options") or {}
    background = _style_anchor(spec, template)
    card, card_detail, card_rule = _card_parts(spec, zh)
    identity = PR.identity_block(spec)
    crop = next((c for c in CROPS if c["id"] == options.get("crop")), CROPS[0])
    negative = PR.negative(spec, PR.NEGATIVE_STICKER_ZH if zh else PR.NEGATIVE_STICKER_EN)
    fixed = PR.AVATAR_CONSTRAINTS_ZH if zh else PR.AVATAR_CONSTRAINTS_EN
    items = []
    for index, label in enumerate(options.get("styles") or [], 1):
        art = _describe(AVATAR_ART, label, zh)
        style = (f"画风「{label}」：{art}" if zh else f"Art style \"{label}\": {art}")
        prompt = PR.assemble([style, identity, crop["zh"] if zh else crop["en"], background, card_detail, fixed, card_rule], zh,
                             spec=spec, subject=identity, scene=background, use_case="avatar set",
                             details=[style, crop["zh"] if zh else crop["en"]] + PR.card_details(card, zh),
                             constraints=[fixed, card_rule], aspect="1:1")
        items.append(_item(index, label, prompt, negative, "1:1", {"art": label, "crop": crop["id"]}))
    return items


def _avatar_post(spec: Mapping[str, Any], item: Mapping[str, Any], path: Path, ctx: P.PostContext) -> list[dict[str, Any]]:
    from PIL import Image

    square = P.crop_aspect(P.open_rgba(path), "1:1")
    out = []
    for size in P.AVATAR_SIZES:
        saved = P.save_png(P.cover(square, size, size), ctx.name(item["id"], f"a{size}"))
        out.append(P.output_record(saved, role=f"a{size}", item=item["id"]))
    if (spec.get("options") or {}).get("circle"):
        saved = P.save_png(P.circle(P.cover(square, 512, 512)), ctx.name(item["id"], "circle"))
        out.append(P.output_record(saved, role="circle", item=item["id"]))
    return out


# ---------------------------------------------------------------------------- portrait


def _portrait_expand_for(template_key: tuple[str, str], *, baby: bool, fixed_zh: str, fixed_en: str):
    def expand(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        template = TEMPLATES[template_key]
        zh = PR.is_zh(spec)
        options = spec.get("options") or {}
        style = _style_anchor(spec, template)
        card, card_detail, card_rule = _card_parts(spec, zh)
        identity = PR.identity_block(spec)
        aspect = str(options.get("aspect") or "3:4")
        count = int(options.get("count_per_scene") or 1)
        negatives = [PR.NEGATIVE_PORTRAIT_ZH if zh else PR.NEGATIVE_PORTRAIT_EN]
        if baby:
            negatives.append(PR.NEGATIVE_BABY_ZH if zh else PR.NEGATIVE_BABY_EN)
        negative = PR.negative(spec, *negatives)
        items = []
        index = 0
        for label in options.get("scenes") or []:
            scene = _describe(template.scene_table, label, zh)
            reserve = "留空位" in label or "留空" in label or "叠字" in label
            scene_text = (f"场景「{label}」：{scene}" if zh else f"Scene \"{label}\": {scene}")
            for k in range(count):
                index += 1
                parts = [style, card_detail, identity, scene_text,
                         fixed_zh if zh else fixed_en, PR.PORTRAIT_SAFETY_ZH if zh else PR.PORTRAIT_SAFETY_EN]
                rules = [fixed_zh if zh else fixed_en, PR.PORTRAIT_SAFETY_ZH if zh else PR.PORTRAIT_SAFETY_EN]
                if baby:
                    parts.append(PR.BABY_SAFETY_ZH if zh else PR.BABY_SAFETY_EN)
                    rules.append(PR.BABY_SAFETY_ZH if zh else PR.BABY_SAFETY_EN)
                if reserve or str(options.get("text") or "").strip():
                    parts.append(PR.TEXT_SPACE_ZH if zh else PR.TEXT_SPACE_EN)
                    rules.append(PR.TEXT_SPACE_ZH if zh else PR.TEXT_SPACE_EN)
                parts.append(PR.exclusivity(spec))
                parts.append(card_rule)
                rules += [PR.exclusivity(spec), card_rule]
                item_label = label if count == 1 else f"{label} #{k + 1}"
                prompt = PR.assemble(parts, zh, spec=spec, subject=identity, scene=scene_text,
                                     use_case="keepsake portrait album",
                                     details=[style] + PR.card_details(card, zh), constraints=rules, aspect=aspect)
                items.append(_item(index, item_label, prompt, negative, aspect, {"scene": label, "take": k + 1}))
        return items

    return expand


def _portrait_post(spec: Mapping[str, Any], item: Mapping[str, Any], path: Path, ctx: P.PostContext) -> list[dict[str, Any]]:
    options = spec.get("options") or {}
    text = str(options.get("text") or "").strip()
    source = P.open_rgba(path)
    main = P.crop_aspect(source, str(options.get("aspect") or "3:4"))
    side = P.crop_aspect(source, "4:3")
    if text:
        title, _, sub = text.partition("|")
        main = P.overlay_text(main, title.strip(), subtitle=sub.strip(), position="bottom")
        side = P.overlay_text(side, title.strip(), subtitle=sub.strip(), position="bottom")
    a = P.save_png(main, ctx.name(item["id"], "album"))
    b = P.save_png(side, ctx.name(item["id"], "album_4x3"))
    return [P.output_record(a, role="album", item=item["id"]), P.output_record(b, role="album_4x3", item=item["id"])]


def _portrait_extra(spec: Mapping[str, Any], items: list[Mapping[str, Any]], ctx: P.PostContext) -> list[dict[str, Any]]:
    from .spec import current_path

    paths = [current_path(spec, i) for i in items]
    sheet = P.save_jpeg(P.contact_sheet([p for p in paths if p]), ctx.name(None, "contact_sheet", ".jpg"))
    return [P.output_record(sheet, role="contact_sheet", item=None)]


PORTRAIT_LAYOUT = {"album": "album/{item}.png", "album_4x3": "album_4x3/{item}.png", "contact_sheet": "contact_sheet.jpg"}


def _portrait_options(scenes: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        _list_option("scenes", "场景", "Scenes", list(scenes), "可增删改；改了名字就按你写的原文出图"),
        {"key": "count_per_scene", "label": "每个场景几张", "label_en": "Shots per scene", "type": "int", "default": 1, "min": 1, "max": 4},
        {"key": "text", "label": "叠字（祝语｜日期）", "label_en": "Overlay text (wish | date)", "type": "string", "default": "",
         "hint": "本机叠字，不让模型画；用「|」分隔祝语与日期，留空不叠"},
        {"key": "aspect", "label": "主版画幅", "label_en": "Main aspect", "type": "string", "default": "3:4",
         "choices": [{"id": "3:4", "name": "3:4"}, {"id": "4:3", "name": "4:3"}, {"id": "1:1", "name": "1:1"}]},
    ]


# ---------------------------------------------------------------------------- image（图片工作台）

IMAGE_ASPECTS = ("1:1", "3:4", "4:3", "9:16", "16:9")
# 默认对比的三个模型：@image = 设置里配好的生图模型，另两个是常见候选（前端会用模型目录里的真实 id 覆盖）
DEFAULT_COMPARE_MODELS = ["@image", "gpt-image-2", "nano-banana"]
CARD_LAYOUTS = [{"id": "auto", "name": "自动", "english": "Auto"}, {"id": "top", "name": "上", "english": "Top"},
                {"id": "bottom", "name": "下", "english": "Bottom"}]

FRAME_ZH = "按 {aspect} 画幅构图，主体完整、不被画面边缘切断，四周没有额外边框。"
FRAME_EN = "Compose for a {aspect} frame, the subject complete and not cut by the edges, no extra border around the image. "


def _aspect_option(default: str = "1:1") -> dict[str, Any]:
    return {"key": "aspect", "label": "画幅", "label_en": "Aspect", "type": "string", "default": default,
            "choices": [{"id": a, "name": a} for a in IMAGE_ASPECTS]}


def _image_parts(spec: Mapping[str, Any], template: Template) -> tuple[bool, str, dict[str, Any] | None, str, str, str]:
    """(中文?, 风格锚, 卡, 卡补强句, 卡约束句, 主体身份块)；主体为空时身份块是空串（契约 §2）。"""

    zh = PR.is_zh(spec)
    style = _style_anchor(spec, template)
    card, card_detail, card_rule = _card_parts(spec, zh)
    return zh, style, card, card_detail, card_rule, PR.subject_block(spec)


def _image_prompt(spec: Mapping[str, Any], template: Template, *, brief: str, aspect: str,
                  extra_rules: list[str] | None = None) -> str:
    """图片工作台的一张提示词：风格锚 + 卡补强 + 主体（可空）+ 画面一句话 + 画幅约束 + 排他锁（有主体才写）。"""

    zh, style, card, card_detail, card_rule, identity = _image_parts(spec, template)
    scene = (f"画面：{brief}" if zh else f"Scene: {brief}")
    frame = (FRAME_ZH if zh else FRAME_EN).format(aspect=aspect)
    rules = [frame, *(extra_rules or [])]
    if identity:
        rules.append(PR.exclusivity(spec))
    rules.append(card_rule)
    parts = [style, card_detail, identity, scene, *rules]
    return PR.assemble(parts, zh, spec=spec, subject=identity, scene=scene, use_case="image studio",
                       details=[style] + PR.card_details(card, zh), constraints=rules, aspect=aspect)


def _freeform_expand(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = TEMPLATES[("image", "freeform")]
    options = spec.get("options") or {}
    zh = PR.is_zh(spec)
    brief = str(options.get("brief") or "").strip() or str(spec.get("title") or "")
    aspect = str(options.get("aspect") or "1:1")
    variants = max(1, int(options.get("variants") or 1))
    negative = PR.negative(spec, str(options.get("negative") or ""))
    prompt = _image_prompt(spec, template, brief=brief, aspect=aspect)
    items = []
    for index in range(1, variants + 1):
        label = (f"候选 {index}" if zh else f"Variant {index}")
        items.append(_item(index, label, prompt, negative, aspect, {"variant": index, "brief": brief}))
    return items


def _compare_expand(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = TEMPLATES[("image", "model_compare")]
    options = spec.get("options") or {}
    brief = str(options.get("brief") or "").strip() or str(spec.get("title") or "")
    aspect = str(options.get("aspect") or "1:1")
    negative = PR.negative(spec, str(options.get("negative") or ""))
    prompt = _image_prompt(spec, template, brief=brief, aspect=aspect)
    models = list(dict.fromkeys(str(m).strip() for m in (options.get("models") or []) if str(m).strip()))
    items = []
    for index, model in enumerate(models, 1):
        # meta.model 覆盖 image_params.model（generate.py 唯一的模块专属分支）
        items.append(_item(index, model, prompt, negative, aspect, {"model": model, "brief": brief}))
    return items


def _text_card_expand(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = TEMPLATES[("image", "text_card")]
    options = spec.get("options") or {}
    zh = PR.is_zh(spec)
    brief = str(options.get("brief") or "").strip() or str(spec.get("title") or "")
    aspect = str(options.get("aspect") or "3:4")
    variants = max(1, int(options.get("variants") or 1))
    layout = str(options.get("layout") or "auto")
    bottom = layout == "bottom"
    space = (PR.TEXT_SPACE_BOTTOM_ZH if bottom else PR.TEXT_SPACE_ZH) if zh else \
        (PR.TEXT_SPACE_BOTTOM_EN if bottom else PR.TEXT_SPACE_EN)
    negative = PR.negative(spec, str(options.get("negative") or ""))
    prompt = _image_prompt(spec, template, brief=brief, aspect=aspect, extra_rules=[space])
    title = str(options.get("title") or "").strip() or str(spec.get("title") or "")
    items = []
    for index in range(1, variants + 1):
        label = (f"底图 {index}" if zh else f"Base {index}")
        items.append(_item(index, label, prompt, negative, aspect,
                           {"variant": index, "brief": brief, "title": title, "layout": layout}))
    return items


def _image_post(spec: Mapping[str, Any], item: Mapping[str, Any], path: Path, ctx: P.PostContext) -> list[dict[str, Any]]:
    """按这张图自己的画幅居中裁一版（原分辨率）+ 长边 2048 一版。"""

    aspect = str(item.get("aspect_ratio") or (spec.get("options") or {}).get("aspect") or "1:1")
    source = P.open_rgba(path)
    main = P.save_png(P.crop_aspect(source, aspect), ctx.name(item["id"], "image"))
    small = P.save_png(P.crop_aspect(source, aspect, max_side=P.IMAGE_LONG_SIDE), ctx.name(item["id"], "w2048"))
    return [P.output_record(main, role="image", item=item["id"]),
            P.output_record(small, role="w2048", item=item["id"])]


def _text_card_post(spec: Mapping[str, Any], item: Mapping[str, Any], path: Path, ctx: P.PostContext) -> list[dict[str, Any]]:
    """底图 → 海报那套叠字（``storyvideo.poster.render_poster``，换成图卡画幅）。"""

    options = spec.get("options") or {}
    meta = item.get("meta") or {}
    aspect = str(item.get("aspect_ratio") or options.get("aspect") or "3:4")
    title = str(meta.get("title") or options.get("title") or spec.get("title") or "")
    card = P.render_card(path, ctx.name(item["id"], "card"), title=title,
                         subtitle=str(options.get("subtitle") or ""), aspect=aspect,
                         layout=str(meta.get("layout") or options.get("layout") or "auto"))
    return [P.output_record(card, role="card", item=item["id"])]


# ---------------------------------------------------------------------------- 注册表

TEMPLATES: dict[tuple[str, str], Template] = {}


def _register(template: Template) -> Template:
    TEMPLATES[(template.module, template.id)] = template
    return template


_register(Template(
    id="emoji_pack", module="sticker", name="表情包", english="Emoji pack",
    description="一个角色的一组表情：每个表情一张 1:1 白底图 → 转透明 → 微信表情规格（240 主图 / 120 缩略 / 750×400 横幅 / 50 图标）",
    description_en="One character, a set of expressions: one 1:1 white-background image each → transparent → WeChat sticker sizes",
    styles=STICKER_STYLES,
    options_schema=[
        _list_option("expressions", "表情清单", "Expressions", list(EXPRESSIONS), "可增删改；清单里没有的表情按你写的原文出图"),
        {"key": "with_caption", "label": "叠中文短语", "label_en": "Caption below", "type": "bool", "default": False,
         "hint": "本机叠字（表情名），不让模型画字"},
    ],
    expand=_emoji_expand, post=_emoji_post, post_extra=_emoji_extra,
    export_layout={"main": "stickers/{item}.png", "thumb": "thumbs/{item}.png", "banner": "banner.png", "icon": "icon.png"},
    scene_table=EXPRESSIONS,
))

_register(Template(
    id="sticker_set", module="sticker", name="贴图", english="Sticker set",
    description="按主题出一组贴图（咖啡 / 加班 / 周末…）：每主题一张 1:1，白底转透明，输出 512 与 1024",
    description_en="A themed sticker set: one 1:1 image per theme, background removed, exported at 512 and 1024",
    styles=STICKER_STYLES,
    options_schema=[
        _list_option("themes", "主题清单", "Themes", list(THEMES)),
        {"key": "shape", "label": "外形", "label_en": "Shape", "type": "string", "default": "circle",
         "choices": [{"id": s["id"], "name": s["name"], "english": s["english"]} for s in SHAPES]},
    ],
    expand=_sticker_expand, post=_sticker_post,
    export_layout={"s512": "stickers/512/{item}.png", "s1024": "stickers/1024/{item}.png"},
    scene_table=THEMES,
))

_register(Template(
    id="avatar_set", module="sticker", name="头像", english="Avatar set",
    description="同一个人 / 角色的多风格头像：每种画风一张 1:1，输出 1080 / 512 / 200，可选圆形版本",
    description_en="One subject in many art styles: one 1:1 avatar per style, exported at 1080 / 512 / 200 with an optional circular version",
    styles=AVATAR_STYLES,
    options_schema=[
        _list_option("styles", "画风清单", "Art styles", list(AVATAR_ART)),
        {"key": "crop", "label": "取景", "label_en": "Crop", "type": "string", "default": "headshot",
         "choices": [{"id": c["id"], "name": c["name"], "english": c["english"]} for c in CROPS]},
        {"key": "circle", "label": "圆形版本", "label_en": "Circular version", "type": "bool", "default": True},
    ],
    expand=_avatar_expand, post=_avatar_post,
    export_layout={"a1080": "avatars/1080/{item}.png", "a512": "avatars/512/{item}.png", "a200": "avatars/200/{item}.png",
                   "circle": "avatars/circle/{item}.png"},
    scene_table=AVATAR_ART,
))

_register(Template(
    id="newborn_30", module="portrait", name="满月", english="Newborn (30 days)",
    description="满月照：襁褓 / 云朵毯 / 月亮道具 / 姓名字母块 / 花环 / 父母手心；柔光棚拍，婴儿安睡或安静",
    description_en="Newborn set: swaddle / cloud blanket / moon prop / name blocks / wreath / parents' hands; soft studio light, baby asleep or calm",
    styles=PORTRAIT_STYLES, options_schema=_portrait_options(NEWBORN_SCENES),
    expand=_portrait_expand_for(("portrait", "newborn_30"), baby=True,
                                fixed_zh="柔光棚拍，婴儿安睡或安静躺着，脸周只有柔软织物，道具离脸一掌以上",
                                fixed_en="Soft studio light, the baby asleep or lying calmly, only soft fabric around the face, props at least a hand's width from the face"),
    post=_portrait_post, post_extra=_portrait_extra, export_layout=dict(PORTRAIT_LAYOUT), scene_table=NEWBORN_SCENES,
))

_register(Template(
    id="baby_100", module="portrait", name="百天", english="100 days",
    description="百天照：百天蛋糕（无火）/ 数字气球「100」（叠字）/ 抓周预演 / 小马甲 / 趴姿抬头 / 全家福",
    description_en="100-day set: cake (no flame) / \"100\" balloons (text added locally) / zhuazhou rehearsal / knit vest / tummy time / family",
    styles=PORTRAIT_STYLES, options_schema=_portrait_options(BABY100_SCENES),
    expand=_portrait_expand_for(("portrait", "baby_100"), baby=True,
                                fixed_zh="柔光棚拍，婴儿安静或微笑，蛋糕与道具上没有火焰",
                                fixed_en="Soft studio light, the baby calm or smiling, no flame on the cake or props"),
    post=_portrait_post, post_extra=_portrait_extra, export_layout=dict(PORTRAIT_LAYOUT), scene_table=BABY100_SCENES,
))

_register(Template(
    id="first_birthday", module="portrait", name="周岁", english="First birthday",
    description="周岁照：生日帽 + 蛋糕（无火）/ 抓周物件 / 学步 / 气球墙 / 唐装或小西装 / 全家福",
    description_en="First-birthday set: party hat + cake (no flame) / zhuazhou objects / first steps / balloon wall / tang suit or little suit / family",
    styles=PORTRAIT_STYLES, options_schema=_portrait_options(FIRST_BIRTHDAY_SCENES),
    expand=_portrait_expand_for(("portrait", "first_birthday"), baby=True,
                                fixed_zh="柔光棚拍，宝宝坐稳或有人扶着，蛋糕与道具上没有火焰",
                                fixed_en="Soft studio light, the toddler seated securely or supported, no flame on the cake or props"),
    post=_portrait_post, post_extra=_portrait_extra, export_layout=dict(PORTRAIT_LAYOUT), scene_table=FIRST_BIRTHDAY_SCENES,
))

_register(Template(
    id="wedding", module="portrait", name="婚纱", english="Wedding",
    description="婚纱照：教堂 / 海边 / 城市夜景 / 中式红妆 / 森林 / 复古胶片 / 黑白棚拍 / 雨中伞；参考图标新娘 / 新郎，逐人点名 + 排他锁",
    description_en="Wedding set: church / beach / city night / Chinese red / forest / vintage film / B&W studio / umbrella in rain; references tagged bride / groom",
    styles=WEDDING_STYLES, options_schema=_portrait_options(WEDDING_SCENES),
    expand=_portrait_expand_for(("portrait", "wedding"), baby=False,
                                fixed_zh="婚纱与礼服的面料质感清楚（缎面、蕾丝、羊毛呢），两人的姿态自然，视线有交流",
                                fixed_en="Fabric textures of the gown and suit read clearly (satin, lace, wool), natural poses, eye contact between the two"),
    post=_portrait_post, post_extra=_portrait_extra, export_layout=dict(PORTRAIT_LAYOUT), scene_table=WEDDING_SCENES,
))


_register(Template(
    id="freeform", module="image", name="自由出图", english="Freeform",
    description="一句话画面出 N 张候选：同一提示词、同一画幅，挑一张；后处理按画幅居中裁一版 + 长边 2048 一版",
    description_en="One line of scene text, N candidates: same prompt and aspect, pick one; post crops to the aspect and adds a 2048 long-side copy",
    styles=IMAGE_STYLES,
    options_schema=[
        {"key": "brief", "label": "画面一句话", "label_en": "Scene in one line", "type": "string", "default": "",
         "hint": "写具体的材料与颜色，别写「精美 / 高质量」；留空就用标题"},
        {"key": "variants", "label": "候选张数", "label_en": "Variants", "type": "int", "default": 4, "min": 1, "max": 8},
        _aspect_option("1:1"),
        {"key": "negative", "label": "额外负面词", "label_en": "Extra negative words", "type": "string", "default": "",
         "hint": "逗号分隔；文字 / 水印这些已经默认加了"},
    ],
    expand=_freeform_expand, post=_image_post,
    export_layout={"image": "images/{item}.png", "w2048": "images/2048/{item}.png"},
))

_register(Template(
    id="model_compare", module="image", name="多模型对比", english="Model comparison",
    description="同一句提示词换模型各出一张，横向比：每个模型一张图，包内按模型名归档",
    description_en="One prompt across several models, one image each, packaged under the model name",
    styles=IMAGE_STYLES,
    options_schema=[
        {"key": "brief", "label": "画面一句话", "label_en": "Scene in one line", "type": "string", "default": "",
         "hint": "所有模型用同一句，差别才只来自模型"},
        _list_option("models", "参与对比的模型", "Models", list(DEFAULT_COMPARE_MODELS),
                     "2–6 个模型 id；@image = 设置里配好的生图模型"),
        _aspect_option("1:1"),
        {"key": "negative", "label": "额外负面词", "label_en": "Extra negative words", "type": "string", "default": ""},
    ],
    expand=_compare_expand, post=_image_post,
    export_layout={"image": "compare/{label}.png", "w2048": "compare/2048/{label}.png"},
))

_register(Template(
    id="text_card", module="image", name="图卡", english="Text card",
    description="出底图后在本机叠标题：海报那套粗体中文字体、渐变压暗与柔和投影，副标题限 24 字（文字不让模型画）",
    description_en="Generate a base image, then overlay the title locally with the poster typography (bold CJK font, gradient scrim, soft shadow)",
    styles=IMAGE_STYLES,
    options_schema=[
        {"key": "brief", "label": "底图一句话", "label_en": "Base image in one line", "type": "string", "default": ""},
        {"key": "title", "label": "标题（本机叠）", "label_en": "Title (drawn locally)", "type": "string", "default": "",
         "hint": "留空就用项目标题；模型不画字"},
        {"key": "subtitle", "label": "副标题", "label_en": "Subtitle", "type": "string", "default": "", "hint": "超过 24 字会截断"},
        _aspect_option("3:4"),
        {"key": "layout", "label": "标题位置", "label_en": "Title position", "type": "string", "default": "auto",
         "choices": [dict(c) for c in CARD_LAYOUTS], "hint": "自动 = 挑细节少的一端"},
        {"key": "variants", "label": "底图张数", "label_en": "Base images", "type": "int", "default": 2, "min": 1, "max": 4},
    ],
    expand=_text_card_expand, post=_text_card_post,
    export_layout={"card": "cards/{item}.png"},
))


def templates_for(module: str) -> list[Template]:
    return [t for (m, _), t in TEMPLATES.items() if m == module]


def template_ids(module: str) -> list[str]:
    return [t.id for t in templates_for(module)]


def get_template(module: str, template_id: str) -> Template | None:
    return TEMPLATES.get((module, template_id))


def template_for_spec(spec: Mapping[str, Any]) -> Template:
    template = get_template(str(spec.get("module")), str(spec.get("template")))
    if template is None:
        raise KeyError(f"unknown template {spec.get('module')}/{spec.get('template')}")
    return template


def expand_items(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """按模板展开 items；已有同 id 的项保留其 current / attempts / request_hash（提示词按模板重算）。"""

    template = template_for_spec(spec)
    fresh = template.expand(spec)
    existing = {str(i["id"]): i for i in spec.get("items") or ()}
    out = []
    for item in fresh:
        old = existing.get(item["id"]) or {}
        out.append({
            **item,
            "status": old.get("status") or "todo",
            "current": old.get("current"),
            "request_hash": old.get("request_hash"),
            "error": old.get("error"),
            "attempts": list(old.get("attempts") or []),
        })
    return out


__all__ = ["Template", "TEMPLATES", "STICKER_STYLES", "AVATAR_STYLES", "PORTRAIT_STYLES", "WEDDING_STYLES", "IMAGE_STYLES",
           "EXPRESSIONS", "THEMES",
           "SHAPES", "AVATAR_ART", "CROPS", "NEWBORN_SCENES", "BABY100_SCENES", "FIRST_BIRTHDAY_SCENES", "WEDDING_SCENES",
           "IMAGE_ASPECTS", "DEFAULT_COMPARE_MODELS", "CARD_LAYOUTS",
           "templates_for", "template_ids", "get_template", "template_for_spec", "expand_items", "style_cards_for"]
