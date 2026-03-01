"""
Tagentacle Permission MCP Server — JWT credential issuer and agent registry.

This module provides ``PermissionMCPServerNode``, the issuer side of the TACL
(Tagentacle Access Control Layer) system.  It is a pre-built MCP Server Node
(like ``TagentacleMCPServer``) that lives inside ``python-sdk-mcp``.

Responsibilities:
  - Store agent records and their tool grants in an SQLite database.
  - Expose an ``authenticate`` MCP tool that exchanges a raw token for a
    signed JWT credential containing the agent's tool grants.
  - Expose admin tools for registering / updating / revoking agents.
  - Expose bus Services (``/tagentacle/permission/*``) so other Nodes
    (e.g. a reproduction server) can programmatically manage agents.

The SQLite database path is configured via the ``permission_db`` key in
bringup config, the ``TAGENTACLE_PERMISSION_DB`` env var, or defaults to
``permission.db`` in the working directory.

Requires the ``[permission]`` optional dependency::

    pip install tagentacle-py-mcp[permission]
    # or: uv pip install tagentacle-py-mcp[permission]

Architecture::

    Agent ──token──▶ PermissionMCPServerNode.authenticate()
                              │
                              ▼
                     SQLite: verify token_hash → load tool_grants
                              │
                              ▼
                     auth.sign_credential(agent_id, tool_grants)
                              │
                              ▼
                     ◀── JWT credential ──
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field

from tagentacle_py_mcp.auth import sign_credential
from tagentacle_py_mcp.server import MCPServerNode

logger = logging.getLogger("tagentacle.mcp.permission")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS agents (
    agent_id       TEXT PRIMARY KEY,
    token_hash     TEXT NOT NULL UNIQUE,
    parent_agent_id TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_grants (
    agent_id   TEXT NOT NULL,
    server_id  TEXT NOT NULL,
    tool_name  TEXT NOT NULL,
    PRIMARY KEY (agent_id, server_id, tool_name),
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
);
"""


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of a raw token (never store tokens in plain text)."""
    return hashlib.sha256(token.encode()).hexdigest()


class _PermissionDB:
    """Thin synchronous wrapper around sqlite3 for permission data.

    All methods are blocking but extremely fast (in-process SQLite on
    local disk).  They are called from async code via
    ``asyncio.to_thread`` in the MCP tool handlers.
    """

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        logger.info("Permission DB opened at %s", db_path)

    def close(self):
        self._conn.close()

    # -- agent CRUD --

    def register_agent(
        self,
        agent_id: str,
        token: str,
        tool_grants: Dict[str, List[str]],
        parent_agent_id: Optional[str] = None,
    ) -> None:
        token_hash = _hash_token(token)
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO agents (agent_id, token_hash, parent_agent_id) "
            "VALUES (?, ?, ?)",
            (agent_id, token_hash, parent_agent_id),
        )
        for server_id, tools in tool_grants.items():
            for tool in tools:
                cur.execute(
                    "INSERT INTO tool_grants (agent_id, server_id, tool_name) "
                    "VALUES (?, ?, ?)",
                    (agent_id, server_id, tool),
                )
        self._conn.commit()

    def authenticate(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify a raw token and return agent info + grants, or None."""
        token_hash = _hash_token(token)
        row = self._conn.execute(
            "SELECT agent_id FROM agents WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        agent_id = row[0]
        return {
            "agent_id": agent_id,
            "tool_grants": self._load_grants(agent_id),
        }

    def _load_grants(self, agent_id: str) -> Dict[str, List[str]]:
        rows = self._conn.execute(
            "SELECT server_id, tool_name FROM tool_grants WHERE agent_id = ?",
            (agent_id,),
        ).fetchall()
        grants: Dict[str, List[str]] = {}
        for server_id, tool_name in rows:
            grants.setdefault(server_id, []).append(tool_name)
        return grants

    def update_grants(
        self, agent_id: str, tool_grants: Dict[str, List[str]]
    ) -> bool:
        """Replace all tool grants for an agent.  Returns False if agent
        does not exist."""
        row = self._conn.execute(
            "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return False
        cur = self._conn.cursor()
        cur.execute("DELETE FROM tool_grants WHERE agent_id = ?", (agent_id,))
        for server_id, tools in tool_grants.items():
            for tool in tools:
                cur.execute(
                    "INSERT INTO tool_grants (agent_id, server_id, tool_name) "
                    "VALUES (?, ?, ?)",
                    (agent_id, server_id, tool),
                )
        self._conn.commit()
        return True

    def revoke_agent(self, agent_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def get_grants(self, agent_id: str) -> Optional[Dict[str, List[str]]]:
        row = self._conn.execute(
            "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return self._load_grants(agent_id)

    def list_agents(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT agent_id, parent_agent_id, created_at FROM agents"
        ).fetchall()
        return [
            {
                "agent_id": r[0],
                "parent_agent_id": r[1],
                "created_at": r[2],
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# PermissionMCPServerNode
# ---------------------------------------------------------------------------

class PermissionMCPServerNode(MCPServerNode):
    """MCP Server Node that issues JWT credentials and manages agent ACLs.

    This is the **issuer** in the TACL system.  It does NOT require auth
    itself (``auth_required=False``) — it is the entry point agents use
    to authenticate.

    MCP Tools:
      - ``authenticate(token)`` — exchange a raw token for a JWT credential.
      - ``register_agent(agent_id, token, tool_grants, parent_agent_id?)``
        — admin: register a new agent.
      - ``update_grants(agent_id, tool_grants)`` — admin: update grants.
      - ``revoke_agent(agent_id)`` — admin: revoke an agent.
      - ``get_grants(agent_id)`` — admin: query current grants.
      - ``list_agents()`` — admin: list all registered agents.

    Admin tools require the caller to present a token that resolves to an
    agent whose grants include ``("permission_server", "admin")``.

    Bus Services (for programmatic access by other Nodes):
      - ``/tagentacle/permission/register_agent``
      - ``/tagentacle/permission/get_grants``

    Constructor Args:
        node_id: Node ID (default ``"permission_server"``).
        mcp_port: HTTP port (default ``8200``).
        db_path: SQLite database file path.  Defaults to
            ``TAGENTACLE_PERMISSION_DB`` env var or ``"permission.db"``.
    """

    def __init__(
        self,
        node_id: str = "permission_server",
        *,
        mcp_port: int = 8200,
        db_path: Optional[str] = None,
    ):
        super().__init__(
            node_id,
            mcp_name="tagentacle-permission-server",
            mcp_port=mcp_port,
            description=(
                "TACL Permission Server — authenticates agents and "
                "issues JWT credentials with tool-level access control."
            ),
            auth_required=False,  # This server is the auth entry point
        )
        self._db_path = (
            db_path
            or os.environ.get("TAGENTACLE_PERMISSION_DB", "permission.db")
        )
        self._db: Optional[_PermissionDB] = None

    def on_configure(self, config: Dict[str, Any]):
        """Initialize DB and register MCP tools + bus services."""
        # Allow bringup config to override db_path
        if "permission_db" in config:
            self._db_path = config["permission_db"]

        self._db = _PermissionDB(self._db_path)
        self._register_tools()

        # Register bus services for programmatic access
        @self.service("/tagentacle/permission/register_agent")
        async def _bus_register(msg: dict):
            p = msg.get("payload", {})
            return await self._do_register_agent(
                p.get("agent_id", ""),
                p.get("token", ""),
                p.get("tool_grants", {}),
                p.get("parent_agent_id"),
            )

        @self.service("/tagentacle/permission/get_grants")
        async def _bus_get_grants(msg: dict):
            p = msg.get("payload", {})
            agent_id = p.get("agent_id", "")
            grants = await asyncio.to_thread(self._db.get_grants, agent_id)
            if grants is None:
                return {"error": f"Agent '{agent_id}' not found"}
            return {"agent_id": agent_id, "tool_grants": grants}

        super().on_configure(config)

    async def on_shutdown(self):
        """Close the database."""
        await super().on_shutdown()
        if self._db:
            self._db.close()

    # -- internal helpers --

    def _is_admin(self, token: str) -> bool:
        """Check if a token resolves to an admin agent."""
        info = self._db.authenticate(token)
        if info is None:
            return False
        grants = info.get("tool_grants", {})
        admin_tools = grants.get("permission_server", [])
        return "admin" in admin_tools or "*" in admin_tools

    async def _do_register_agent(
        self,
        agent_id: str,
        token: str,
        tool_grants: Dict[str, List[str]],
        parent_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            await asyncio.to_thread(
                self._db.register_agent,
                agent_id, token, tool_grants, parent_agent_id,
            )
            return {"status": "ok", "agent_id": agent_id}
        except sqlite3.IntegrityError as exc:
            return {"error": f"Registration failed: {exc}"}

    # -- MCP tool registration --

    def _register_tools(self) -> None:

        # --- Public tool: authenticate ---

        @self.mcp.tool(
            description=(
                "Exchange a raw agent token for a signed JWT credential. "
                "The JWT contains the agent's tool_grants and can be used "
                "as a Bearer header to connect to auth-enabled MCP servers."
            ),
        )
        async def authenticate(
            token: Annotated[str, Field(description="The agent's secret token")],
        ) -> str:
            info = await asyncio.to_thread(self._db.authenticate, token)
            if info is None:
                return "Error: Invalid token"

            jwt = sign_credential(
                agent_id=info["agent_id"],
                tool_grants=info["tool_grants"],
            )
            return jwt

        # --- Admin tools ---

        @self.mcp.tool(
            description=(
                "Register a new agent with tool grants. "
                "Requires admin privileges (pass your admin token)."
            ),
        )
        async def register_agent(
            admin_token: Annotated[str, Field(description="Admin agent's token for authorization")],
            agent_id: Annotated[str, Field(description="Unique ID for the new agent")],
            token: Annotated[str, Field(description="Secret token for the new agent")],
            tool_grants: Annotated[str, Field(description="JSON object: {server_id: [tool_name, ...], ...}")],
            parent_agent_id: Annotated[Optional[str], Field(description="Parent agent ID (optional)")] = None,
        ) -> str:
            is_admin = await asyncio.to_thread(self._is_admin, admin_token)
            if not is_admin:
                return "Error: Unauthorized — admin privileges required"
            try:
                grants = json.loads(tool_grants)
            except json.JSONDecodeError as exc:
                return f"Error: Invalid tool_grants JSON: {exc}"
            result = await self._do_register_agent(
                agent_id, token, grants, parent_agent_id
            )
            if "error" in result:
                return f"Error: {result['error']}"
            return f"Agent '{agent_id}' registered successfully."

        @self.mcp.tool(
            description="Update tool grants for an existing agent. Requires admin.",
        )
        async def update_grants(
            admin_token: Annotated[str, Field(description="Admin agent's token")],
            agent_id: Annotated[str, Field(description="Target agent ID")],
            tool_grants: Annotated[str, Field(description="JSON object: {server_id: [tool_name, ...], ...}")],
        ) -> str:
            is_admin = await asyncio.to_thread(self._is_admin, admin_token)
            if not is_admin:
                return "Error: Unauthorized — admin privileges required"
            try:
                grants = json.loads(tool_grants)
            except json.JSONDecodeError as exc:
                return f"Error: Invalid tool_grants JSON: {exc}"
            ok = await asyncio.to_thread(
                self._db.update_grants, agent_id, grants
            )
            if not ok:
                return f"Error: Agent '{agent_id}' not found"
            return f"Grants updated for '{agent_id}'."

        @self.mcp.tool(
            description="Revoke an agent (delete from DB). Requires admin.",
        )
        async def revoke_agent(
            admin_token: Annotated[str, Field(description="Admin agent's token")],
            agent_id: Annotated[str, Field(description="Agent ID to revoke")],
        ) -> str:
            is_admin = await asyncio.to_thread(self._is_admin, admin_token)
            if not is_admin:
                return "Error: Unauthorized — admin privileges required"
            ok = await asyncio.to_thread(self._db.revoke_agent, agent_id)
            if not ok:
                return f"Error: Agent '{agent_id}' not found"
            return f"Agent '{agent_id}' revoked."

        @self.mcp.tool(
            description="Query the tool grants for an agent. Requires admin.",
        )
        async def get_grants(
            admin_token: Annotated[str, Field(description="Admin agent's token")],
            agent_id: Annotated[str, Field(description="Agent ID to query")],
        ) -> str:
            is_admin = await asyncio.to_thread(self._is_admin, admin_token)
            if not is_admin:
                return "Error: Unauthorized — admin privileges required"
            grants = await asyncio.to_thread(self._db.get_grants, agent_id)
            if grants is None:
                return f"Error: Agent '{agent_id}' not found"
            return json.dumps(
                {"agent_id": agent_id, "tool_grants": grants},
                ensure_ascii=False, indent=2,
            )

        @self.mcp.tool(
            description="List all registered agents. Requires admin.",
        )
        async def list_agents(
            admin_token: Annotated[str, Field(description="Admin agent's token")],
        ) -> str:
            is_admin = await asyncio.to_thread(self._is_admin, admin_token)
            if not is_admin:
                return "Error: Unauthorized — admin privileges required"
            agents = await asyncio.to_thread(self._db.list_agents)
            return json.dumps(agents, ensure_ascii=False, indent=2)
