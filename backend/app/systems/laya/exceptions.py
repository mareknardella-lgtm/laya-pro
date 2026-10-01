"""Errors raised when the laya-coreml runtime is unavailable or breaks contract."""

from __future__ import annotations


class LayaError(RuntimeError):
    """Base class for every System 2 failure."""


class LayaUnavailable(LayaError):
    """The reasoning runtime is not configured or not reachable."""


class LayaContractError(LayaError):
    """The runtime replied with something that is not a valid reasoning plan."""


class LayaRefusal(LayaError):
    """The runtime explicitly declined to reason about the request."""

    def __init__(self, message: str, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason