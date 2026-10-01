"""Errors raised when the Nemotron runtime is unavailable or breaks contract."""

from __future__ import annotations


class NemotronError(RuntimeError):
    """Base class for every System 1 failure."""


class NemotronUnavailable(NemotronError):
    """The runtime is not configured or not reachable."""


class NemotronContractError(NemotronError):
    """The runtime replied with something that is not a valid generation."""