"""日志脱敏：密钥、Bearer 令牌、data URL 不落日志。

装在日志 handler 上（desktop._setup_logging）：``MaskingFormatter`` 对格式化好的整条文本（消息 + 整条异常链的堆栈）
过一遍；``MaskingFilter`` 只改 record 的消息（同进程里别的 handler——pytest 的 caplog 之类——看到的也是遮过的）。
规则宁可多遮：遮掉一个不是密钥的长串没有成本，漏掉一个真密钥有成本。
"""

from __future__ import annotations

import logging
import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    # 常见密钥形态：sk-…、sk-or-…、AKIA…、Google 的 AIza…、JWT（MiniMax 老式密钥就是这个形状）
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]{6,}"), r"\1…[masked]"),
    (re.compile(r"\b(AKIA[A-Z0-9]{4})[A-Z0-9]{12,}"), r"\1…[masked]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[masked]"),
    (re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "[masked]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[masked]"),
    # URL 查询串里的密钥：?key=… / &api_key=… / &access_token=…（Google 系接口把密钥放在 ?key= 里）
    (re.compile(r"(?i)([?&](?:key|api[_-]?key|access[_-]?token|token)=)[^&\s'\"]{6,}"), r"\1[masked]"),
    # key=/token=/「api key:」后面的长串；也认 dict / JSON 的 repr（'api_key': '…'、"token": "…"）——键名后面先跟引号再跟冒号。
    # 方舟的密钥是 UUID 形状，只有跟在这些键名后面时才遮：单独出现的 UUID 多半是任务 id / 素材 id，排障要用
    (re.compile(r"(?i)((?:api[_ -]?key|access[_-]?token|token|secret|password|authorization)['\"]?\s*[=:]\s*['\"]?)"
                r"(?!bearer\b|\[masked\])[^\s'\",;&]{6,}"), r"\1[masked]"),
    # data URL 的 base64 正文：几十 KB 到几 MB，日志里只留头
    (re.compile(r"(data:[a-z]+/[a-z0-9.+-]+;base64,)[A-Za-z0-9+/=]{24,}"), r"\1…[base64 omitted]"),
]


def mask(text: str) -> str:
    out = text
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return out


class MaskingFilter(logging.Filter):
    """把 record 变成脱敏后的纯文本消息。

    不再去改异常对象本身（以前改 ``exc.args[0]``）：那是调用方还在用的业务对象，而且只遮得到最外层——
    适配器 ``raise … from exc`` 包的上游原话在 ``__cause__`` 里照样进堆栈。堆栈交给 :class:`MaskingFormatter`。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 格式化失败就退回原样
            message = str(record.msg)
        record.msg = mask(message)
        record.args = ()
        return True


class MaskingFormatter(logging.Formatter):
    """格式化之后整条再遮一遍：消息、整条异常链（``__cause__`` / ``__context__``）的堆栈、stack_info 都在里面。"""

    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))

    def formatException(self, ei) -> str:  # noqa: N802 - logging.Formatter 的接口名
        # Formatter.format 会把这里的结果缓存进 record.exc_text，别的 handler 复用它：缓存的也得是遮过的
        return mask(super().formatException(ei))


__all__ = ["mask", "MaskingFilter", "MaskingFormatter"]
