"""
Tagentacle MCP Integration: MCPServerNode base class, built-in bus tools server,
and TACL (Tagentacle Access Control Layer) authentication system.

This package provides:
  - MCPServerNode: Abstract base class for MCP Server Nodes that run a
    Streamable HTTP endpoint and publish to /mcp/directory for discovery.
    Supports optional ``auth_required=True`` for JWT-based access control.
  - TagentacleMCPServer: Built-in MCP Server exposing bus operations as MCP Tools.
  - MCP_DIRECTORY_TOPIC: Standard topic name for MCP server discovery.
  - Auth primitives (``tagentacle_py_mcp.auth``): JWT sign/verify, CallerIdentity,
    get_caller_identity(), check_tool_authorized().
  - AuthMCPClient (``tagentacle_py_mcp.auth_client``): Auth-aware MCP client.
  - PermissionMCPServerNode (``tagentacle_py_mcp.permission``): JWT credential
    issuer and agent registry (requires ``[permission]`` optional dependency).
"""

from tagentacle_py_mcp.server import (
    MCPServerNode,
    TagentacleMCPServer,
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
    # Server
    "MCPServerNode",
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
