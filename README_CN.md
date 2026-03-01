# Tagentacle MCP 集成

> **The ROS of AI Agents** — MCPServerNode 基类及内置 MCP Server。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

`tagentacle-py-mcp` 为 Tagentacle 消息总线提供 MCP（Model Context Protocol）集成：

- **MCPServerNode** — MCP Server Node 基类。运行 Streamable HTTP，自动发布到 `/mcp/directory`。
- **TagentacleMCPServer** — 内置可执行节点，将所有总线交互能力暴露为 MCP Tool。

## 安装

```bash
pip install tagentacle-py-mcp
```

自动安装 `tagentacle-py-core`、`uvicorn`、`starlette`、`mcp` 作为依赖。

## MCPServerNode（基类）

继承 `MCPServerNode` 构建自有 MCP Server Node：

```python
from tagentacle_py_mcp import MCPServerNode

class WeatherServer(MCPServerNode):
    def __init__(self):
        super().__init__("weather_server", mcp_port=8100)

    def on_configure(self, config):
        super().on_configure(config)

        @self.mcp.tool(description="获取城市天气")
        def get_weather(city: str) -> str:
            return f"{city}: 晴天"

async def main():
    node = WeatherServer()
    await node.bringup()
    await node.spin()

asyncio.run(main())
```

节点激活时：
1. 通过 uvicorn 启动 Streamable HTTP 服务器
2. 向 `/mcp/directory` Topic 发布 `MCPServerDescription`
3. Agent 节点通过原生 MCP SDK HTTP 客户端发现并连接

### 配置

| 来源 | 键 | 默认值 |
|------|-----|--------|
| 构造函数 | `mcp_host` / `mcp_port` | `"0.0.0.0"` / `8000` |
| 环境变量 | `MCP_HOST` / `MCP_PORT` | 覆盖构造函数 |

## TagentacleMCPServer（可执行节点）

内置 MCP Server，将所有总线交互能力暴露为 MCP Tool：

```python
from tagentacle_py_mcp import TagentacleMCPServer

server = TagentacleMCPServer("bus_tools_node", allowed_topics=["/alerts", "/logs"])
await server.bringup()
await server.spin()
```

### 暴露的 MCP Tool

| 工具 | 说明 |
|------|------|
| `publish_to_topic` | 向 Topic 发布 JSON 消息 |
| `subscribe_topic` | 订阅 Topic 并缓冲消息 |
| `unsubscribe_topic` | 取消订阅 |
| `list_nodes` | 列出所有连接节点 |
| `list_topics` | 列出所有活跃 Topic |
| `list_services` | 列出所有已注册 Service |
| `get_node_info` | 获取节点详情 |
| `call_bus_service` | 通过 RPC 调用总线上的任意 Service |
| `ping_daemon` | 检查 Daemon 健康状态 |
| `describe_topic_schema` | 获取 Topic 消息 JSON Schema |

## TACL — Tagentacle 访问控制层

基于 JWT 的 MCP 工具级身份认证。三个共轭模块以 `auth.py` 为对称中心：

```
              auth.py (HS256 JWT 协议)
             /           |            \
  permission.py    server.py 中间件   auth_client.py
  sign_credential  verify_credential  携带 JWT
  (发行端)         (验证端)           (消费端)
```

### 为 MCP Server 启用认证

```python
class SecureServer(MCPServerNode):
    def __init__(self):
        super().__init__("secure_server", mcp_port=8100, auth_required=True)

    def on_configure(self, config):
        super().on_configure(config)

        @self.mcp.tool(description="敏感操作")
        def do_something() -> str:
            from tagentacle_py_mcp.auth import get_caller_identity
            caller = get_caller_identity()
            return f"你好, {caller.agent_id}!"
```

在环境变量中设置 `TAGENTACLE_AUTH_SECRET`（所有 auth-enabled 服务器和权限服务器共享）。

### AuthMCPClient（Agent 侧）

```python
from tagentacle_py_mcp import AuthMCPClient

client = AuthMCPClient(
    token="tok_abc123",
    permission_server_url="http://127.0.0.1:8200/mcp",
)
await client.login()  # → 获取 JWT 凭证

async with client.connect("http://127.0.0.1:8100/mcp") as session:
    tools = await session.list_tools()  # 只能看到被授权的工具
    result = await session.call_tool("do_something", {})
```

### PermissionMCPServerNode（凭证发行端）

预制 MCP Server，使用 SQLite 管理 agent 注册表并签发 JWT：

```bash
# 安装带权限支持的版本
uv pip install tagentacle-py-mcp[permission]

# 独立运行
export TAGENTACLE_AUTH_SECRET="your-secret"
python permission_node.py
```

| MCP 工具 | 权限 | 说明 |
|----------|------|------|
| `authenticate` | 公开 | 用原始 token 换取 JWT 凭证 |
| `register_agent` | 管理员 | 注册新 agent 及工具授权 |
| `update_grants` | 管理员 | 更新 agent 的工具授权 |
| `revoke_agent` | 管理员 | 吊销 agent |
| `get_grants` | 管理员 | 查询 agent 当前授权 |
| `list_agents` | 管理员 | 列出所有已注册 agent |

同时暴露总线 Service：`/tagentacle/permission/register_agent`、`/tagentacle/permission/get_grants`。

## Tagentacle Pkg

这是一个 Tagentacle **executable pkg**（`tagentacle.toml` 中 `type = "executable"`），同时包含 library 组件。

- **Executable**：`TagentacleMCPServer` 节点（入口：`server_node:main`）
- **Executable**：`PermissionMCPServerNode`（入口：`permission_node:main`）
- **Library**：`MCPServerNode` 基类、TACL 认证原语、`AuthMCPClient` — 可被其他 pkg import

依赖：`[dependencies] tagentacle = ["tagentacle_py_core"]`

## 许可证

MIT
