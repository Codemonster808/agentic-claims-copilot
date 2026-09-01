"""FlakyLLMProvider — wraps FakeLLMProvider to deliberately simulate a
failure, so the permanent/transient retry-and-DLQ logic in
src/models/agent_loop.py can be exercised without a real flaky network.

    LLM_PROVIDER=fake-flaky FLAKY_FAILURE_MODE=transient-once python3 ...
    LLM_PROVIDER=fake-flaky FLAKY_FAILURE_MODE=permanent python3 ...

Modes (read from FLAKY_FAILURE_MODE):
  transient-once  — the first .complete() call in this process raises
                     TransientLLMError; every call after that succeeds
                     normally. Simulates a one-off timeout that a retry
                     fixes.
  permanent       — every .complete() call raises PermanentLLMError.
                     Simulates a request the provider will never accept
                     (retrying is pointless).
  anything else / unset — behaves exactly like FakeLLMProvider. This is
                     the default, so LLM_PROVIDER=fake-flaky without
                     FLAKY_FAILURE_MODE set is just the normal fake
                     provider — existing tests and `make demo` are
                     unaffected unless FLAKY_FAILURE_MODE is explicitly
                     set.
"""

import os

from .errors import PermanentLLMError, TransientLLMError
from .fake import FakeLLMProvider


class FlakyLLMProvider:
    def __init__(self) -> None:
        self._fake = FakeLLMProvider()
        self._has_failed_once = False

    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        mode = os.environ.get("FLAKY_FAILURE_MODE", "")

        if mode == "permanent":
            raise PermanentLLMError("simulated: provider rejected the request (content policy)")

        if mode == "transient-once" and not self._has_failed_once:
            self._has_failed_once = True
            raise TransientLLMError("simulated: connection timeout")

        return self._fake.complete(prompt, max_tokens=max_tokens)
