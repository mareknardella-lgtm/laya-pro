"""Failures for the optional, isolated System 2 generation runtime."""


class System2AdapterError(RuntimeError):
    """A generation adapter failure; never triggers tool execution or System 1 fallback."""


class System2NotConfiguredError(System2AdapterError):
    """No verified loopback generation endpoint is configured."""


class System2RuntimeError(System2AdapterError):
    """The configured generation endpoint failed or timed out."""


class System2AuthError(System2RuntimeError):
    """Authentication to the System 2 provider failed (HTTP 401/403)."""


class System2RateLimitError(System2RuntimeError):
    """The System 2 provider returned rate limit or quota exceeded (HTTP 429)."""


class System2ContractError(System2RuntimeError):
    """The generation endpoint violated the plain-text response contract."""
