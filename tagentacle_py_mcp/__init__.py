"""
Tagentacle MCP Integration: MCPServerComponent, built-in bus tools server,
and TACL re-exports for backward compatibility.

This package provides:
  - MCPServerComponent: Composable MCP Server component (no Node inheritance).
    Manages FastMCP + uvicorn + /mcp/directory publishing. Designed for has-a
    composition with any LifecycleNode.
  - BusMCPServer: Built-in MCP Server exposing bus operations as MCP Tools.
    (Previously named TagentacleMCPServer; alias kept for backward compat.)
  - MCP_DIRECTORY_TOPIC: Standard topic name for MCP server discovery.

TACL auth primitives have moved to ``tagentacle-py-tacl`` (python-sdk-tacl).
For backward compatibility, they are re-exported here.
"""

from tagentacle_py_mcp.server import (
    MCPServerComponent,
    BusMCPServer,
    TagentacleMCPServer,  # backward compat alias
    TACLAuthMiddleware,
    MCP_DIRECTORY_TOPIC,
)
from tagentacle_py_mcp.mailbox import BusMailboxComponent

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
    # Component (recommended)
    "MCPServerComponent",
    "BusMailboxComponent",
    # Built-in servers
    "BusMCPServer",
    "TagentacleMCPServer",  # backward compat alias
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
