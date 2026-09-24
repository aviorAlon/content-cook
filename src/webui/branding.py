"""产品名（DESIGN §38）：唯一一处。

2026-09-09 定位调整：从「AI 短剧平台」走向「内容创作工具」，短剧只是其中一个功能模块。仓库叫
content-cook，这里先用它做工作名；以后要改名只改这两个常量——界面头部、窗口标题、FastAPI 标题、
``/api/health`` 与 ``/api/modules`` 都从这里读。Python 包名、命令行（storystudio）、设置目录
（~/.storystudio）与文件布局都不跟着改：那些是技术标识，不是产品名。
"""

from __future__ import annotations

PRODUCT_NAME = "Content Cook · 内容创作工作台"
PRODUCT_NAME_EN = "Content Cook · Content creation workbench"
PRODUCT_SHORT = "Content Cook"


def product_payload() -> dict[str, str]:
    """接口里带给前端的形状（前端按界面语言二选一）。"""

    return {"name": PRODUCT_NAME, "name_en": PRODUCT_NAME_EN, "short": PRODUCT_SHORT}


__all__ = ["PRODUCT_NAME", "PRODUCT_NAME_EN", "PRODUCT_SHORT", "product_payload"]
