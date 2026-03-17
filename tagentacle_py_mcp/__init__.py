"""
Tagentacle MCP Integration: MCPServerComponent, built-in bus tools server,
and TACL (Tagentacle Access Control Layer) authentication system.

This package provides:
  - MCPServerComponent: Composable MCP Server component (no Node inheritance).
    Manages FastMCP + uvicorn + /mcp/directory publishing. Designed for has-a
    composition with any LifecycleNode.
  - MCPServerNode: DEPRECATED wrapper (LifecycleNode + MCPServerComponent).
    Kept for backward compatibility. Use MCPServerComponent directly.
  - BusMCPServer: Built-in MCP Server exposing bus operations as MCP Tools.
    (Previously named TagentacleMCPServer; alias kept for backward compat.)
  - MCP_DIRECTORY_TOPIC: Standard topic name for MCP server discovery.
  - Auth primitives (``tagentacle_py_mcp.auth``): JWT sign/verify, CallerIdentity,
    get_caller_identity(), check_tool_authorized().
  - AuthMCPClient (``tagentacle_py_mcp.auth_client``): Auth-aware MCP client.
  - PermissionMCPServerNode (``tagentacle_py_mcp.permission``): JWT credential
    issuer and agent registry (requires ``[permission]`` optional dependency).
"""

from tagentacle_py_mcp.server import (
    MCPServerComponent,
    MCPServerNode,
    BusMCPServer,
    TagentacleMCPServer,  # backward compat alias
    TACLAuthMiddleware,
    MCP_DIRECTORY_TOPIC,
)

from tagentacle_py_mcp.auth import (
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

from tagentacle_py_mcp.auth_client import AuthMCPClient

__all__ = [
    # Component (recommended)
    "MCPServerComponent",
    # Server (deprecated wrapper)
    "MCPServerNode",
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
