"""语音（旁白 TTS）：edge-tts 库（免费）/ OpenAI 兼容 / OpenRouter。统一接口 ``synthesize(text, destination, voice=, model=)``。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from ..providers import ProviderCommandError
from .base import Adapter


class EdgeSpeech(Adapter):
    """微软 Edge 在线音色，走开源库 ``edge-tts``（不再依赖命令行）。"""

    name = "edge_tts"

    def synthesize(self, text: str, destination: Path, *, voice: str | None = None, model: str | None = None,
                   rate: str | None = None, pitch: str | None = None) -> Path:
        try:
            import edge_tts
        except ImportError as exc:  # pragma: no cover
            raise ProviderCommandError("edge-tts is not installed: pip install edge-tts") from exc
        chosen = voice or model or self.model or "zh-CN-YunxiNeural"
        kwargs: dict[str, Any] = {"text": text, "voice": chosen}
        if rate:
            kwargs["rate"] = rate
        if pitch:
            kwargs["pitch"] = pitch
        destination.parent.mkdir(parents=True, exist_ok=True)

        async def _run() -> None:
            await edge_tts.Communicate(**kwargs).save(str(destination))

        try:
            asyncio.run(_run())
        except RuntimeError:
            # 已在事件循环里（如 webui 线程）：开一个新循环
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()
        except Exception as exc:  # noqa: BLE001
            raise ProviderCommandError(f"edge-tts failed for 「{text[:30]}」: {exc}") from exc
        if not destination.is_file() or destination.stat().st_size == 0:
            raise ProviderCommandError(f"edge-tts produced no audio for 「{text[:30]}」")
        return destination

    def ping(self) -> str:
        """「测试连接」真正联一次网：取一次 Edge 在线音色表（免费、不合成）。之前只 import 一下库就报 ready，
        断网 / 代理不通时也是 ✓，真合成时才失败（ADP-01）。30 秒封顶，别让测试连接卡住。"""

        try:
            import edge_tts
        except ImportError as exc:  # pragma: no cover
            raise ProviderCommandError("edge-tts is not installed: pip install edge-tts") from exc

        async def _list() -> list[Any]:
            return await asyncio.wait_for(edge_tts.list_voices(), timeout=30.0)

        try:
            try:
                voices = asyncio.run(_list())
            except RuntimeError:
                # 已在事件循环里（如 webui 线程）：开一个新循环
                loop = asyncio.new_event_loop()
                try:
                    voices = loop.run_until_complete(_list())
                finally:
                    loop.close()
        except Exception as exc:  # noqa: BLE001
            raise ProviderCommandError(f"连不上 Edge 在线语音服务：{str(exc) or type(exc).__name__}——检查网络或代理") from exc
        return f"edge-tts reachable ({len(voices or [])} voices), voice {self.model or 'zh-CN-YunxiNeural'}"


def build_edge(config: Mapping[str, Any], **kw: Any) -> EdgeSpeech:
    return EdgeSpeech(config, **kw)
