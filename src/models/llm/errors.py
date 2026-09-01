"""Exceptions an LLMProvider.complete() call can raise, split by whether
retrying is worth it.

TransientLLMError: a network timeout, connection reset, or provider-side
throttling — the same request would likely succeed on a later attempt.
Worth retrying with backoff.

PermanentLLMError: a request the provider will never accept as-is (bad
request shape, content-policy rejection, auth failure). Retrying the
identical request wastes budget for zero chance of success — send it to
the DLQ immediately instead.
"""


class TransientLLMError(Exception):
    pass


class PermanentLLMError(Exception):
    pass
