"""Summary LLM protocol + ChatClient adapter for the period ladder.

Scope: narrow ``SummaryLlm`` surface used by ``ladder``; optional adapter that
wraps ``ChatClient`` with ``reasoning=False`` and hard-stop → error for
template fallback. Ladder must not import presence; tests inject stubs.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from elyra.llm.usage import UsageHardStopError


class SummaryLlmError(Exception):
    """Hard failure from a summary LLM call (adapter maps this to template)."""


@runtime_checkable
class SummaryLlm(Protocol):
    """Minimal completion surface for ladder narrative generation."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
    ) -> str:
        """Return assistant content text; raise SummaryLlmError on hard failure."""
        ...


class ChatClientSummaryLlm:
    """Adapter: ``ChatClient.chat_completion`` → ``SummaryLlm``.

    - ``reasoning=False`` (ladder must not consume do-loop reasoning budget)
    - ``UsageHardStopError`` → ``SummaryLlmError`` (never kill presence)
    - empty / whitespace content → ``SummaryLlmError``
    """

    def __init__(self, client: Any, *, temperature: float | None = None) -> None:
        self._client = client
        self._temperature = temperature

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
    ) -> str:
        try:
            result = self._client.chat_completion(
                messages,
                max_tokens=int(max_tokens),
                reasoning=False,
                temperature=self._temperature,
            )
        except UsageHardStopError as exc:
            raise SummaryLlmError(f"usage_hard_stop: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — map all client errors
            raise SummaryLlmError(f"chat_completion_failed: {exc}") from exc

        content = getattr(result, "content", None)
        text = (str(content) if content is not None else "").strip()
        if not text:
            raise SummaryLlmError("empty_content")
        return text


__all__ = [
    "ChatClientSummaryLlm",
    "SummaryLlm",
    "SummaryLlmError",
]
