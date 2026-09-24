"""生成应用图标：desktop/storystudio.ico（Windows exe / 安装包 / 浏览器标签）与 desktop/storystudio.icns（macOS .app）。

    python desktop/make_icon.py

图是画出来的，不依赖外部素材：界面主色（frontend/src/styles.css 的 --accent）的圆角方块 + Content Cook 的首字母 CC。
字体用 Pillow 自带的（``ImageFont.load_default(size)``，Pillow ≥ 10.1），跨平台产物一致。两个文件都进仓库——
打包机上不用再跑这个脚本；改了配色 / 字样再跑一次就行。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ACCENT = (85, 104, 74)          # --accent: #55684a
ACCENT_DARK = (70, 84, 60)      # --accent-strong: #46543c
SIZE = 256
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def draw_icon(size: int = SIZE) -> Image.Image:
    # 大画布抗锯齿：4 倍画完再缩
    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = big * 0.22
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=radius, fill=ACCENT)
    # 右下角压一层深色，让方块有点体积感（不然小尺寸下像一块色板）
    draw.rounded_rectangle((0, big * 0.55, big - 1, big - 1), radius=radius, fill=ACCENT_DARK)
    draw.rounded_rectangle((0, 0, big - 1, big * 0.72), radius=radius, fill=ACCENT)
    font = ImageFont.load_default(size=int(big * 0.46))
    text = "CC"
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = (big - w) / 2 - box[0]
    y = (big - h) / 2 - box[1] - big * 0.02
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    draw_icon().save(HERE / "storystudio.ico", sizes=ICO_SIZES)
    # .icns 最大一档是 512@2x（1024 像素），从 256 放大在 Retina 的 Dock 上会糊，单独按 1024 画
    draw_icon(1024).save(HERE / "storystudio.icns")
    for name in ("storystudio.ico", "storystudio.icns"):
        print(name, (HERE / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
