"""
Tagentacle MCP Auth — JWT credential primitives.

This module is the symmetric center of the TACL (Tagentacle Access Control
Layer) system.  It provides pure-Python JWT (HS256) signing and verification
with **zero external dependencies** beyond the Python standard library.

Roles:
  - PermissionMCPServerNode calls ``sign_credential`` to issue JWTs.
  - MCPServerNode's auth middleware calls ``verify_credential`` to validate.
  - AuthMCPClient carries the JWT and passes it as a Bearer header.

JWT Payload Schema::

    {
        "agent_id": "agent_alpha",
        "tool_grants": {
            "tagentacle_mcp_server": ["publish_to_topic", "subscribe_topic"],
            "tao_wallet_server": ["query_balance", "transfer"]
        },
        "space": "agent_space_1",
        "iat": 1740000000,
        "exp": 1740086400
    }

``space`` is an optional field that identifies the isolated execution
environment (e.g. a Docker container) bound to this agent at runtime.
Other nodes (such as shell-server) read ``CallerIdentity.space`` to
route commands to the correct backend.

The shared secret is loaded from the ``TAGENTACLE_AUTH_SECRET`` environment
variable by both the issuer (PermissionMCPServerNode) and every auth-enabled
MCPServerNode.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Context variable: set by the auth middleware, read by tool handlers
# ---------------------------------------------------------------------------


@dataclass
class CallerIdentity:
    """Identity of the authenticated MCP caller for the current request."""

    agent_id: str
    tool_grants: Dict[str, List[str]] = field(default_factory=dict)
    space: Optional[str] = None


#: Set per-request by the auth middleware; read via ``get_caller_identity()``.
_caller_identity_var: ContextVar[Optional[CallerIdentity]] = ContextVar(
    "tacl_caller_identity",
    default=None,
)


def get_caller_identity() -> Optional[CallerIdentity]:
    """Return the authenticated caller identity for the current request.

    Returns ``None`` if auth is not enabled or no credential was presented.
    """
    return _caller_identity_var.get()


def set_caller_identity(identity: Optional[CallerIdentity]) -> None:
    """Set the caller identity (used by auth middleware)."""
    _caller_identity_var.set(identity)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for authentication / authorization errors."""


class CredentialInvalid(AuthError):
    """The JWT credential is malformed, has a bad signature, or is expired."""


class ToolNotAuthorized(AuthError):
    """The caller's credential does not grant access to the requested tool."""


# ---------------------------------------------------------------------------
# JWT helpers (HS256, pure stdlib)
# ---------------------------------------------------------------------------

_DEFAULT_TTL = 86400  # 24 hours


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _default_secret() -> str:
    """Read the shared secret from the environment."""
    secret = os.environ.get("TAGENTACLE_AUTH_SECRET", "")
    if not secret:
        raise AuthError(
            "TAGENTACLE_AUTH_SECRET environment variable is not set. "
            "All auth-enabled MCP servers and the permission server must "
            "share the same secret."
        )
    return secret


def sign_credential(
    agent_id: str,
    tool_grants: Dict[str, List[str]],
    secret: Optional[str] = None,
    ttl: int = _DEFAULT_TTL,
    space: Optional[str] = None,
) -> str:
    """Issue a signed JWT credential.

    Args:
        agent_id: Unique identifier for the agent.
        tool_grants: Mapping of ``server_id`` to list of allowed tool names.
        secret: HMAC secret (defaults to ``TAGENTACLE_AUTH_SECRET`` env var).
        ttl: Time-to-live in seconds (default 24 h).
        space: Optional isolated execution environment bound to this agent
            (e.g. Docker container name).  Included in the JWT if provided.

    Returns:
        A compact JWT string (``header.payload.signature``).
    """
    if secret is None:
        secret = _default_secret()

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "agent_id": agent_id,
        "tool_grants": tool_grants,
        "iat": now,
        "exp": now + ttl,
    }
    if space is not None:
        payload["space"] = space

    segments = [
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode()),
        _b64url_encode(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = f"{segments[0]}.{segments[1]}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    segments.append(_b64url_encode(sig))

    return ".".join(segments)


def verify_credential(
    token: str,
    secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify and decode a JWT credential.

    Args:
        token: The compact JWT string.
        secret: HMAC secret (defaults to ``TAGENTACLE_AUTH_SECRET`` env var).

    Returns:
        The decoded payload dict.

    Raises:
        CredentialInvalid: On malformed token, bad signature, or expiry.
    """
    if secret is None:
        secret = _default_secret()

    parts = token.split(".")
    if len(parts) != 3:
        raise CredentialInvalid("Malformed JWT: expected 3 dot-separated segments")

    try:
        header_bytes = _b64url_decode(parts[0])
        payload_bytes = _b64url_decode(parts[1])
        sig_bytes = _b64url_decode(parts[2])
    except Exception as exc:
        raise CredentialInvalid(f"Malformed JWT: base64 decode failed: {exc}") from exc

    # Verify header
    try:
        header = json.loads(header_bytes)
    except json.JSONDecodeError as exc:
        raise CredentialInvalid(f"Malformed JWT header: {exc}") from exc
    if header.get("alg") != "HS256":
        raise CredentialInvalid(f"Unsupported algorithm: {header.get('alg')}")

    # Verify signature
    signing_input = f"{parts[0]}.{parts[1]}"
    expected_sig = hmac.new(
        secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(sig_bytes, expected_sig):
        raise CredentialInvalid("Invalid signature")

    # Decode payload
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise CredentialInvalid(f"Malformed JWT payload: {exc}") from exc

    # Check expiry
    exp = payload.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        raise CredentialInvalid("Credential expired")

    # Validate required fields
    if "agent_id" not in payload:
        raise CredentialInvalid("Missing 'agent_id' in credential")
    if "tool_grants" not in payload:
        raise CredentialInvalid("Missing 'tool_grants' in credential")

    return payload


def check_tool_authorized(
    identity: CallerIdentity,
    server_id: str,
    tool_name: str,
) -> None:
    """Raise ``ToolNotAuthorized`` if the caller may not invoke this tool.

    A wildcard ``["*"]`` in the tool list for a given server grants access
    to all tools on that server.
    """
    grants = identity.tool_grants.get(server_id, [])
    if "*" in grants or tool_name in grants:
        return
    raise ToolNotAuthorized(
        f"Agent '{identity.agent_id}' is not authorized to call "
        f"tool '{tool_name}' on server '{server_id}'. "
        f"Granted tools: {grants}"
    )
