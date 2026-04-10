"""
Tagentacle MCP Integration: MCPServerComponent, built-in bus tools server,
and TACL re-exports for backward compatibility.

This package provides:
  - MCPServerComponent: Composable MCP Server component (no Node inheritance).
    Manages FastMCP + uvicorn + /mcp/directory publishing. Designed for has-a
    composition with any LifecycleNode.
  - BusMCPNode: Built-in MCP Server exposing bus operations as MCP Tools.
    (Previously named BusMCPServer / TagentacleMCPServer; aliases kept.)
  - InboxMCP: Composable mailbox with dual Python/MCP access.
    (Previously named BusMailboxComponent; alias kept.)
  - MCP_DIRECTORY_TOPIC: Standard topic name for MCP server discovery.

TACL auth primitives have moved to ``tagentacle-py-tacl``.
For backward compatibility, they are re-exported here.
"""

from tagentacle_py_mcp.server import (
    MCPServerComponent,
    BusMCPNode,
    BusMCPServer,  # backward compat alias (Q27)
    TagentacleMCPServer,  # backward compat alias
    TACLAuthMiddleware,
    MCP_DIRECTORY_TOPIC,
)
from tagentacle_py_mcp.mailbox import (
    InboxMCP,
    BusMailboxComponent,
)  # BusMailboxComponent = compat alias

# Re-export TACL from the new package for backward compatibility
from tagentacle_py_tacl.auth import (
    CallerIdentity,
    get_caller_identity,
    set_caller_identity,
    sign_credential,
    verify_credential,
    check_tool_authorized,
    AuthError,
    CredentialInvalid,
    ToolNotAuthorized,
)

from tagentacle_py_tacl.client import AuthMCPClient

__all__ = [
    # Component (recommended — Q27 names)
    "MCPServerComponent",
    "InboxMCP",
    "BusMCPNode",
    # Backward compat aliases
    "BusMailboxComponent",
    "BusMCPServer",
    "TagentacleMCPServer",
    "TACLAuthMiddleware",
    "MCP_DIRECTORY_TOPIC",
    # Auth primitives
    "CallerIdentity",
    "get_caller_identity",
    "set_caller_identity",
    "sign_credential",
    "verify_credential",
    "check_tool_authorized",
    "AuthError",
    "CredentialInvalid",
    "ToolNotAuthorized",
    # Auth client
    "AuthMCPClient",
]
