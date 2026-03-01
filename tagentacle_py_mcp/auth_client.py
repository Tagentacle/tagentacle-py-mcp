"""
Tagentacle MCP Auth Client — Auth-aware MCP client for authenticated servers.

This module provides ``AuthMCPClient``, the consumer side of the TACL
(Tagentacle Access Control Layer) system.  It:

  1. Connects to a **PermissionMCPServerNode** and calls its ``authenticate``
     tool to exchange a raw token for a signed JWT credential.
  2. Uses the JWT as a ``Bearer`` header when connecting to any auth-enabled
     ``MCPServerNode``.

Usage::

    client = AuthMCPClient(
        token="tok_abc123",
        permission_server_url="http://127.0.0.1:8200/mcp",
    )
    await client.login()

    # Connect to an auth-enabled MCP server
    session = await client.connect("http://127.0.0.1:8100/mcp")
    tools = await session.list_tools()
    result = await session.call_tool("get_weather", {"city": "Tokyo"})
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("tagentacle.mcp.auth_client")


class AuthMCPClient:
    """MCP client that authenticates via a PermissionMCPServerNode.

    Workflow:
      1. ``login()`` — connect to the permission server (no auth required
         on it), call the ``authenticate`` tool with the raw token, receive
         a signed JWT credential.
      2. ``connect(url)`` — open an MCP session to any auth-enabled server,
         passing the JWT as ``Authorization: Bearer <jwt>``.

    Args:
        token: The raw secret token assigned to this agent.
        permission_server_url: Full MCP URL of the PermissionMCPServerNode
            (e.g. ``http://127.0.0.1:8200/mcp``).
    """

    def __init__(
        self,
        token: str,
        permission_server_url: str,
    ):
        self._token = token
        self._permission_url = permission_server_url
        self._credential: Optional[str] = None  # JWT after login

    @property
    def credential(self) -> Optional[str]:
        """The JWT credential, or ``None`` if not yet logged in."""
        return self._credential

    @property
    def is_authenticated(self) -> bool:
        return self._credential is not None

    async def login(self) -> str:
        """Authenticate with the permission server and obtain a JWT.

        Calls the ``authenticate`` tool on the permission server with the
        raw token.  Stores the resulting JWT for future ``connect()`` calls.

        Returns:
            The JWT credential string.

        Raises:
            RuntimeError: If authentication fails.
        """
        logger.info("Logging in to permission server at %s", self._permission_url)

        async with streamablehttp_client(self._permission_url) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                result = await session.call_tool(
                    "authenticate", {"token": self._token}
                )

                # The tool returns a list of TextContent; first item is the JWT
                if not result.content:
                    raise RuntimeError(
                        "Permission server returned empty response"
                    )

                text = result.content[0].text
                if text.startswith("Error:"):
                    raise RuntimeError(
                        f"Authentication failed: {text}"
                    )

                self._credential = text
                logger.info("Login successful — JWT credential obtained.")
                return self._credential

    @asynccontextmanager
    async def connect(self, server_url: str):
        """Open an authenticated MCP session to *server_url*.

        The JWT credential is sent as ``Authorization: Bearer <jwt>`` header.
        Must call ``login()`` first.

        Yields:
            A ``ClientSession`` connected to the target server.

        Raises:
            RuntimeError: If ``login()`` has not been called yet.
        """
        if not self._credential:
            raise RuntimeError(
                "Not authenticated — call login() before connect()."
            )

        headers = {"Authorization": f"Bearer {self._credential}"}

        async with streamablehttp_client(
            server_url, headers=headers
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    async def list_tools(self, server_url: str) -> List[Dict[str, Any]]:
        """Convenience: connect, list tools, disconnect.

        Returns:
            A list of tool descriptions (name, description, inputSchema).
        """
        async with self.connect(server_url) as session:
            result = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
                for t in result.tools
            ]

    async def call_tool(
        self,
        server_url: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Convenience: connect, call one tool, disconnect.

        For repeated calls to the same server, prefer ``connect()`` context
        manager to keep the session open.
        """
        async with self.connect(server_url) as session:
            return await session.call_tool(tool_name, arguments or {})
