# services/resilient_llm.py
# ─────────────────────────────────────────────────────────────────────────────
# Gemini（無料枠）を第一候補、OpenAIを保険としたフォールバック付きLLMラッパー。
#
# 背景：
#   Router/Evaluator/Chronicle/Keeper/Pulse/Vision系（LLM_MODEL_FAST）は
#   コスト最適化のためGeminiへ移行したが、Gemini無料枠のレート制限（RPM/RPD）や
#   クォータ枯渇時にそのまま失敗すると、その日はチャット機能ごと止まってしまう
#   という弱点があった。
#
#   このモジュールは ChatGoogleGenerativeAI とほぼ同じ .ainvoke() /
#   .with_structured_output() / .bind_tools() インターフェースを提供しつつ、
#   Gemini呼び出しが例外（429レート制限・404モデル未対応・タイムアウト等）を
#   投げた場合に自動でOpenAI（デフォルト gpt-4o-mini）へ切り替えて再試行する。
#
#   既存コードは `ChatGoogleGenerativeAI(...)` を `build_fast_llm(...)` に
#   置き換えるだけで、呼び出し側（.ainvoke(...) や .with_structured_output(...)）
#   はそのまま動く。
#
#   注意：Synthesizer（llm_synth, gpt-4o）はキャラクター性維持のため
#   このラッパーの対象外（nodes.pyでOpenAI直結のまま）。
# ─────────────────────────────────────────────────────────────────────────────
import os

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI


class ResilientLLM:
    """
    2つのLLM（primary/fallback）をラップし、primaryが失敗したらfallbackに
    自動的に切り替えて再試行する。LangChainのChatModelやwith_structured_output()
    が返すRunnableと同じ「.ainvoke()を持つオブジェクト」を想定している。
    """

    def __init__(self, primary, fallback, name: str = "llm"):
        self._primary = primary
        self._fallback = fallback
        self._name = name

    async def ainvoke(self, *args, **kwargs):
        try:
            return await self._primary.ainvoke(*args, **kwargs)
        except Exception as e:
            print(f"[{self._name}] Gemini失敗 → OpenAI({self._fallback_model_name()})にフォールバック: {e}")
            try:
                result = await self._fallback.ainvoke(*args, **kwargs)
                _tag_used_fallback(result)
                return result
            except Exception as e2:
                print(f"[{self._name}] OpenAIフォールバックも失敗しました: {e2}")
                raise

    def _fallback_model_name(self) -> str:
        # ログ用に軽くモデル名を覗く（属性が無い場合は諦めて固定文字列）
        try:
            return getattr(self._fallback, "model_name", None) or getattr(self._fallback, "model", "openai")
        except Exception:
            return "openai"

    def bind_tools(self, *args, **kwargs):
        return ResilientLLM(
            self._primary.bind_tools(*args, **kwargs),
            self._fallback.bind_tools(*args, **kwargs),
            name=self._name,
        )

    def with_structured_output(self, *args, **kwargs):
        return ResilientLLM(
            self._primary.with_structured_output(*args, **kwargs),
            self._fallback.with_structured_output(*args, **kwargs),
            name=self._name,
        )


def _tag_used_fallback(result) -> None:
    """
    フォールバック（OpenAI）で得られた結果に「使った」印を付ける。
    AIMessage系（.response_metadataというdictを持つ）にのみ付与できる。
    RouterAnalysisのような素のPydanticモデルには付けられないため、
    その場合は何もしない（ベストエフォート・失敗しても本処理は止めない）。
    """
    try:
        metadata = getattr(result, "response_metadata", None)
        if isinstance(metadata, dict):
            metadata["ruki_used_fallback"] = True
    except Exception:
        pass


def used_fallback(response) -> bool:
    """
    ainvoke()の戻り値（AIMessage等）にフォールバック使用の印が付いているか確認する。
    印が付けられないタイプのオブジェクト（構造化出力のPydanticモデル等）の場合は
    常にFalseを返す（＝そのタスクではフォールバック検知を諦める）。
    """
    try:
        metadata = getattr(response, "response_metadata", None)
        if isinstance(metadata, dict):
            return bool(metadata.get("ruki_used_fallback"))
    except Exception:
        pass
    return False


def build_fast_llm(temperature: float = 0.7, name: str = "FAST-LLM") -> ResilientLLM:
    """
    LLM_MODEL_FAST（デフォルト gemini-2.5-flash-lite）を優先し、
    失敗時は FALLBACK_MODEL_FAST（デフォルト gpt-4o-mini）へ自動フォールバックする
    ResilientLLMインスタンスを構築する。
    Router / Evaluator / Chronicle / Keeper / Pulse / Vision / 各種バッチジョブ用。
    """
    primary = ChatGoogleGenerativeAI(
        model=os.getenv("LLM_MODEL_FAST", "gemini-2.5-flash-lite"),
        temperature=temperature,
        google_api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
    )
    fallback = ChatOpenAI(
        model=os.getenv("FALLBACK_MODEL_FAST", "gpt-4o-mini"),
        temperature=temperature,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )
    return ResilientLLM(primary, fallback, name=name)
