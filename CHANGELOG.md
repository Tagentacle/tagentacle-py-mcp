# Changelog — tagentacle-py-mcp

All notable changes to **tagentacle-py-mcp** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-03-01

### Added
- **TACL (Tagentacle Access Control Layer)** — MCP-level JWT authentication system:
  - **`auth.py`**: Pure-stdlib HS256 JWT sign/verify primitives. `CallerIdentity` contextvar for tool handlers to read authenticated caller info. `check_tool_authorized()` for declarative tool-level ACL.
  - **`auth_client.py`**: `AuthMCPClient` — login to permission server with raw token, obtain JWT, connect to auth-enabled MCP servers with Bearer header.
  - **`permission.py`**: `PermissionMCPServerNode` — pre-built MCP Server Node that issues JWT credentials. SQLite-backed agent registry with tool grants. Exposes `authenticate` (public), plus admin tools (`register_agent`, `update_grants`, `revoke_agent`, `get_grants`, `list_agents`). Also registers bus Services at `/tagentacle/permission/*`.
  - **`permission_node.py`**: Entry point script for running PermissionMCPServerNode standalone.
- **`MCPServerNode.auth_required`** constructor parameter (default `False`):
  - When `True`, wraps Starlette app with `TACLAuthMiddleware` — validates JWT Bearer tokens, sets `CallerIdentity` contextvar per-request.
  - `/mcp/directory` messages now include `auth_required` field.
- **Optional dependency group** `[permission]` in `pyproject.toml` for SQLite-based permission server.
- **`tagentacle.toml`** new entry point: `permission_node = "permission_node:main"`.

### Changed
- `__init__.py` now exports all auth symbols: `CallerIdentity`, `get_caller_identity`, `sign_credential`, `verify_credential`, `check_tool_authorized`, `AuthError`, `CredentialInvalid`, `ToolNotAuthorized`, `AuthMCPClient`, `TACLAuthMiddleware`.

## [0.2.0] - 2026-03-15

### Added
- **MCPServerNode** base class (`LifecycleNode` subclass):
  - Embeds `FastMCP` instance (`self.mcp`) — register tools/resources/prompts in `on_configure()`.
  - Runs Streamable HTTP server via `uvicorn` on `on_activate()`.
  - Publishes `MCPServerDescription` to `/mcp/directory` Topic on activation; publishes `"unavailable"` on deactivation.
  - Configurable via `mcp_host` / `mcp_port` constructor args or `MCP_HOST` / `MCP_PORT` env vars.
- **MCP_DIRECTORY_TOPIC** constant (`/mcp/directory`).

### Changed
- **TagentacleMCPServer** now inherits `MCPServerNode` (was standalone).
  - Same 10 bus tools, now registered in `on_configure()`.
  - Runs its own Streamable HTTP endpoint instead of relying on bus-as-transport.
- **Dependencies**: Added `uvicorn>=0.20`, `starlette` to `pyproject.toml`.

### Removed
- **BREAKING**: Deleted `transport.py` — all bus-as-transport adapters removed:
  - `tagentacle_client_transport` / `TagentacleClientTransport`
  - `tagentacle_server_transport` / `TagentacleServerTransport`
  - `MCP_TRAFFIC_TOPIC` (`/mcp/traffic`) dual-track mirroring.
- MCP Servers now run their own HTTP endpoints; Agents connect directly.

### Migration
- **Server authors**: Subclass `MCPServerNode`, register tools in `on_configure()`, start with `bringup() + spin()`.
- **Client authors**: Use `mcp.client.streamable_http.streamable_http_client(url)` directly. Discover servers via `/mcp/directory` subscription or `MCP_SERVER_URL` env var.

## [0.1.0] - 2026-02-26

### Added
- **MCP Transport Adapters** (library):
  - `tagentacle_client_transport(node, server_node_id)` — async context manager bridging MCP ClientSession over the bus.
  - `tagentacle_server_transport(node, server_node_id)` — async context manager exposing MCP Server as a bus service.
  - Automatic traffic mirroring to `/mcp/traffic` topic (dual-track observability).
  - Backward-compatible aliases: `TagentacleClientTransport`, `TagentacleServerTransport`.
- **TagentacleMCPServer** (executable node):
  - Built-in MCP Server exposing all bus interactions as MCP Tools.
  - Tools: `publish_to_topic`, `subscribe_topic`, `unsubscribe_topic`, `list_nodes`, `list_topics`, `list_services`, `get_node_info`, `call_bus_service`, `ping_daemon`, `describe_topic_schema`.
  - Topic allow-list support.
  - Standalone `main()` entrypoint.
- **Tagentacle pkg manifest**: `tagentacle.toml` with `type = "executable"`, entry point `tagentacle_py_mcp.server:main`.

### Changed
- **Renamed**: `MCPPublishBridge` → `TagentacleMCPServer` (breaking rename from old python-sdk).
- **Import path**: `from tagentacle_py.mcp import ...` → `from tagentacle_py_mcp import ...`.
- **Core dependency**: Depends on `tagentacle-py-core` (extracted from monolithic python-sdk).

> Extracted from the monolithic `tagentacle-py` (python-sdk) repo as part of the
> 1-repo-1-pkg architecture migration.
