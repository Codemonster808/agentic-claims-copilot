import os

from .base import LLMProvider


def get_provider() -> LLMProvider:
    backend = os.environ.get("LLM_PROVIDER", "fake")
    if backend == "fake":
        from .fake import FakeLLMProvider

        return FakeLLMProvider()
    if backend == "minimax":
        from .minimax import MiniMaxLLMProvider

        return MiniMaxLLMProvider()
    if backend == "fake-flaky":
        from .flaky import FlakyLLMProvider

        return FlakyLLMProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER: {backend!r} (expected 'fake', 'minimax', or 'fake-flaky')"
    )
