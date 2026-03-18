# Tagentacle MCP Integration

> **The ROS of AI Agents** — MCPServerComponent and built-in MCP Server for the Tagentacle bus.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

`tagentacle-py-mcp` provides MCP (Model Context Protocol) integration for the Tagentacle message bus:

- **MCPServerComponent** — Composable MCP Server component. Manages FastMCP + Streamable HTTP + `/mcp/directory` publishing. Designed for has-a composition with any `LifecycleNode`.
- **BusMCPServer** — Built-in executable node exposing all bus interactions as MCP Tools (previously `TagentacleMCPServer`; alias kept).

## Install

```bash
pip install tagentacle-py-mcp
```

This automatically installs `tagentacle-py-core`, `tagentacle-py-tacl`, `uvicorn`, `starlette`, and `mcp` as dependencies.

## MCPServerComponent (Composition Pattern)

Build your own MCP Server Node by composing `MCPServerComponent` with any `LifecycleNode`:

```python
from tagentacle_py_core import LifecycleNode
from tagentacle_py_mcp import MCPServerComponent

class WeatherServer(LifecycleNode):
    def __init__(self):
        super().__init__("weather_server")
        self.mcp_server = MCPServerComponent(
            "weather_server", mcp_port=8100,
            description="Weather tools",
        )

    def on_configure(self, config):
        @self.mcp_server.mcp.tool(description="Get weather for a city")
        def get_weather(city: str) -> str:
            return f"Sunny in {city}"

        self.mcp_server.configure(config)

    async def on_activate(self):
        await self.mcp_server.start(publish_fn=self.publish)

    async def on_deactivate(self):
        await self.mcp_server.stop(publish_fn=self.publish)

    async def on_shutdown(self):
        await self.mcp_server.shutdown()

async def main():
    node = WeatherServer()
    await node.bringup()
    await node.spin()

asyncio.run(main())
```

On activation the component:
1. Starts a Streamable HTTP server via uvicorn
2. Publishes an `MCPServerDescription` to the `/mcp/directory` Topic
3. Agent Nodes discover and connect via native MCP SDK HTTP client

### Configuration

| Source | Key | Default |
|--------|-----|---------|
| Constructor | `mcp_host` / `mcp_port` | `"127.0.0.1"` / `8000` |
| Bringup config | `mcp_host` / `mcp_port` | overrides constructor |
| Entry-point scripts | `MCP_HOST` / `MCP_PORT` env vars | used in `server_node.py` CLI entry point |

## BusMCPServer (Executable Node)

Built-in MCP Server exposing all bus interactions as MCP Tools:

```python
from tagentacle_py_mcp import BusMCPServer

server = BusMCPServer("bus_tools_node", allowed_topics=["/alerts", "/logs"])
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

JWT-based MCP tool-level authentication. TACL core has moved to [`tagentacle-py-tacl`](https://github.com/Tagentacle/python-sdk-tacl); auth primitives are re-exported here for backward compatibility.

### Enable Auth on Your MCP Server

```python
class SecureServer(LifecycleNode):
    def __init__(self):
        super().__init__("secure_server")
        self.mcp_server = MCPServerComponent(
            "secure_server", mcp_port=8100,
            description="Secure tools",
            auth_required=True,
        )

    def on_configure(self, config):
        @self.mcp_server.mcp.tool(description="Sensitive operation")
        def do_something() -> str:
            from tagentacle_py_mcp.auth import get_caller_identity
            caller = get_caller_identity()
            return f"Hello, {caller.agent_id}!"

        self.mcp_server.configure(config)

    async def on_activate(self):
        await self.mcp_server.start(publish_fn=self.publish)

    async def on_deactivate(self):
        await self.mcp_server.stop(publish_fn=self.publish)

    async def on_shutdown(self):
        await self.mcp_server.shutdown()
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

### TACLAuthority (Credential Issuer)

Pre-built node (in `tagentacle-py-tacl[authority]`) that manages agent registry in SQLite and issues JWTs:

```bash
# Install with authority support
uv pip install tagentacle-py-tacl[authority]

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

- **Executable**: `BusMCPServer` node (entry point: `server_node:main`)
- **Library**: `MCPServerComponent`, TACL auth re-exports, `AuthMCPClient` — importable by other pkgs

Dependencies: `[dependencies] tagentacle = ["tagentacle_py_core", "tagentacle_py_tacl"]`

## License

MIT
