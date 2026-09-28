"""Failures for an optional external Laya runtime."""


class LayaAdapterError(RuntimeError):
    """Base class; an adapter failure is never an execution authorization."""


class LayaNotConfiguredError(LayaAdapterError):
    """No verified runtime endpoint has been configured."""


class LayaRuntimeError(LayaAdapterError):
    """The configured runtime failed, timed out, or violated the adapter contract."""


class LayaAuthError(LayaRuntimeError):
    """Authentication to the Laya runtime failed (HTTP 401/403)."""


class LayaContractError(LayaRuntimeError):
    """The remote response does not match Laya Pro's normalized schema."""
