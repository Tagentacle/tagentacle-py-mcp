# Tagentacle MCP Integration

> **The ROS of AI Agents** — MCPServerNode base class and built-in MCP Server for the Tagentacle bus.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

`tagentacle-py-mcp` provides MCP (Model Context Protocol) integration for the Tagentacle message bus:

- **MCPServerNode** — Base class for building MCP Server Nodes. Runs Streamable HTTP, publishes to `/mcp/directory`.
- **TagentacleMCPServer** — Built-in executable node exposing all bus interactions as MCP Tools.

## Install

```bash
pip install tagentacle-py-mcp
```

This automatically installs `tagentacle-py-core`, `uvicorn`, `starlette`, and `mcp` as dependencies.

## MCPServerNode (Base Class)

Build your own MCP Server Node by subclassing `MCPServerNode`:

```python
from tagentacle_py_mcp import MCPServerNode

class WeatherServer(MCPServerNode):
    def __init__(self):
        super().__init__("weather_server", mcp_port=8100)

    def on_configure(self, config):
        super().on_configure(config)

        @self.mcp.tool(description="Get weather for a city")
        def get_weather(city: str) -> str:
            return f"Sunny in {city}"

async def main():
    node = WeatherServer()
    await node.bringup()
    await node.spin()

asyncio.run(main())
```

On activation the node:
1. Starts a Streamable HTTP server via uvicorn
2. Publishes an `MCPServerDescription` to the `/mcp/directory` Topic
3. Agent Nodes discover and connect via native MCP SDK HTTP client

### Configuration

| Source | Key | Default |
|--------|-----|---------|
| Constructor | `mcp_host` / `mcp_port` | `"127.0.0.1"` / `8000` |
| Bringup config | `mcp_host` / `mcp_port` | overrides constructor |
| Entry-point scripts | `MCP_HOST` / `MCP_PORT` env vars | used in `server_node.py` / `permission_node.py` CLI entry points |

## TagentacleMCPServer (Executable Node)

Built-in MCP Server exposing all bus interactions as MCP Tools:

```python
from tagentacle_py_mcp import TagentacleMCPServer

server = TagentacleMCPServer("bus_tools_node", allowed_topics=["/alerts", "/logs"])
await server.bringup()
await server.spin()
```

### Exposed MCP Tools

| Tool | Description |
|------|-------------|
| `publish_to_topic` | Publish JSON message to a Topic |
| `subscribe_topic` | Subscribe to a Topic and buffer messages |
| `unsubscribe_topic` | Unsubscribe from a Topic |
| `list_nodes` | List all connected nodes |
| `list_topics` | List all active Topics |
| `list_services` | List all registered Services |
| `get_node_info` | Get node details |
| `call_bus_service` | Call any Service via RPC |
| `ping_daemon` | Check Daemon health |
| `describe_topic_schema` | Get Topic message JSON Schema |

## TACL — Tagentacle Access Control Layer

JWT-based MCP tool-level authentication. Three conjugate modules share `auth.py` as the symmetric center:

```
              auth.py (HS256 JWT protocol)
             /           |            \
  permission.py    server.py middleware  auth_client.py
  sign_credential  verify_credential    carry JWT
  (issuer)         (verifier)           (consumer)
```

### Enable Auth on Your MCP Server

```python
class SecureServer(MCPServerNode):
    def __init__(self):
        super().__init__("secure_server", mcp_port=8100, auth_required=True)

    def on_configure(self, config):
        super().on_configure(config)

        @self.mcp.tool(description="Sensitive operation")
        def do_something() -> str:
            from tagentacle_py_mcp.auth import get_caller_identity
            caller = get_caller_identity()
            return f"Hello, {caller.agent_id}!"
```

Set `TAGENTACLE_AUTH_SECRET` in the environment (shared by all auth-enabled servers and the permission server).

### AuthMCPClient (Agent Side)

```python
from tagentacle_py_mcp import AuthMCPClient

client = AuthMCPClient(
    token="tok_abc123",
    permission_server_url="http://127.0.0.1:8200/mcp",
)
await client.login()  # → JWT credential

async with client.connect("http://127.0.0.1:8100/mcp") as session:
    tools = await session.list_tools()  # only granted tools visible
    result = await session.call_tool("do_something", {})
```

### PermissionMCPServerNode (Credential Issuer)

Pre-built MCP Server that manages agent registry in SQLite and issues JWTs:

```bash
# Install with permission support
uv pip install tagentacle-py-mcp[permission]

# Run standalone
export TAGENTACLE_AUTH_SECRET="your-secret"
python permission_node.py
```

| MCP Tool | Access | Description |
|----------|--------|-------------|
| `authenticate` | Public | Exchange raw token for JWT credential |
| `register_agent` | Admin | Register new agent with tool grants |
| `update_grants` | Admin | Update tool grants for an agent |
| `revoke_agent` | Admin | Revoke an agent |
| `get_grants` | Admin | Query agent's current grants |
| `list_agents` | Admin | List all registered agents |

Also exposes bus Services: `/tagentacle/permission/register_agent`, `/tagentacle/permission/get_grants`.

## Tagentacle Pkg

This is a Tagentacle **executable pkg** (`type = "executable"` in `tagentacle.toml`) with a library component.

- **Executable**: `TagentacleMCPServer` node (entry point: `server_node:main`)
- **Executable**: `PermissionMCPServerNode` (entry point: `permission_node:main`)
- **Library**: `MCPServerNode` base class, TACL auth primitives, `AuthMCPClient` — importable by other pkgs

Dependencies: `[dependencies] tagentacle = ["tagentacle_py_core"]`

## License

MIT
