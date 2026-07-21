"""Outbound simulated-Agent WebSocket transport."""

from .authenticator import FixtureDeviceAuthenticator
from .connection_registry import AgentConnection, ConnectionRegistry

__all__ = ["AgentConnection", "ConnectionRegistry", "FixtureDeviceAuthenticator"]
