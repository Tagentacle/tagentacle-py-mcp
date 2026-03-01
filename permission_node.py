"""Thin entry point for the Tagentacle Permission MCP Server node.

Launches PermissionMCPServerNode as a LifecycleNode with Streamable HTTP.
This is the TACL credential issuer — agents connect here to authenticate
and receive JWT credentials for accessing other auth-enabled MCP servers.

Requires: ``pip install tagentacle-py-mcp[permission]``
"""

import asyncio
import logging
import os
import sys


async def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("tagentacle.mcp.permission")

    # Import here so the module-level import error is clear if aiosqlite
    # is missing (it's an optional dependency).
    try:
        from tagentacle_py_mcp.permission import PermissionMCPServerNode
    except ImportError as exc:
        logger.error(
            "Failed to import PermissionMCPServerNode: %s\n"
            "Install with: uv pip install tagentacle-py-mcp[permission]",
            exc,
        )
        sys.exit(1)

    mcp_port = int(os.environ.get("MCP_PORT", "8200"))
    db_path = os.environ.get("TAGENTACLE_PERMISSION_DB", "permission.db")

    server = PermissionMCPServerNode(mcp_port=mcp_port, db_path=db_path)
    await server.connect()
    spin_task = asyncio.create_task(server.spin())

    await server.configure()
    await server.activate()

    await spin_task


if __name__ == "__main__":
    asyncio.run(main())
