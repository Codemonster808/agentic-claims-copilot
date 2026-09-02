from typing import Protocol


class LLMClient(Protocol):
    """Structural type for anything the agent loop can call as an LLM.

    Same Protocol-over-concrete-class pattern as VectorStore in
    src/utils/vectors.py: callers annotate against this, and the fake,
    flaky, and MiniMax implementations satisfy it without inheriting
    from anything.
    """

    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        """Return a text completion for the given prompt."""
        ...


# `LLMProvider` is the name the factory in __init__.py has always used for
# this same contract; kept as an alias so both call sites read naturally.
LLMProvider = LLMClient
