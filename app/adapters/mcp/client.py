"""MCP Client Adapter."""
import logging
import os
import requests
from typing import Dict, Any, Optional, cast, TYPE_CHECKING
from app.core.config import settings

if TYPE_CHECKING:
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    from mcp.client.session import ClientSession
    from mcp.types import CallToolResult
else:
    try:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
        from mcp.client.session import ClientSession
        from mcp.types import CallToolResult
    except ImportError:
        StdioServerParameters = None
        stdio_client = None
        sse_client = None
        ClientSession = None

logger = logging.getLogger(__name__)

class McpClient:
    async def call_tool(
        self, 
        server_config: Dict[str, Any], 
        tool_name: str, 
        arguments: Dict[str, Any],
        idempotency_key: Optional[str] = None,
        principal_id: Optional[str] = None,
        capability_read_only: bool = False
    ) -> Dict[str, Any]:
        """
        Call an MCP tool, routing through the connector if configured.
        """
        if settings.TALOS_CONNECTOR_URL:
             return await self._call_connector(
                 server_config, tool_name, arguments, idempotency_key, principal_id, capability_read_only
             )
        
        # Fallback to direct connection if no connector configured
        transport = server_config.get("transport", "stdio")
        try:
            if transport == "stdio":
                return await self._call_stdio(server_config, tool_name, arguments)
            elif transport == "sse":
                return await self._call_sse(server_config, tool_name, arguments)
            else:
                raise ValueError(f"Unsupported transport: {transport}")
        except Exception as e:
            logger.error(f"Direct MCP Tool Call Failed: {e}")
            raise

    async def _call_connector(
        self,
        server: Dict[str, Any],
        tool: str,
        args: Dict[str, Any],
        idempotency_key: Optional[str],
        principal_id: Optional[str],
        capability_read_only: bool
    ) -> Dict[str, Any]:
        """Route tool call through the talos-mcp-connector service."""
        server_id = server.get("id")
        if not server_id:
             # If server config is passed without ID, we might need to derive it 
             # or the connector might not know about it if it's dynamic.
             # In production, servers are registered in the connector config.
             server_id = server.get("name") # Fallback
        
        url = f"{settings.TALOS_CONNECTOR_URL}/servers/{server_id}/tools/{tool}/call"
        
        payload = {
            "args": args,
            "idempotency_key": idempotency_key,
            "capability_read_only": capability_read_only
        }
        
        headers = {}
        if principal_id:
            headers["X-Talos-Principal"] = principal_id

        try:
            # Use requests for simplicity since it's in requirements.txt
            # In a fully async app, httpx would be better, but we follow existing patterns.
            import asyncio
            response = await asyncio.to_thread(
                requests.post, url, json=payload, headers=headers, timeout=30
            )
            
            if response.status_code == 409:
                 from app.errors import raise_talos_error
                 raise_talos_error("IDEMPOTENCY_CONFLICT", 409, response.json().get("detail", "Conflict"))
            
            response.raise_for_status()
            data = response.json()
            return cast(Dict[str, Any], data.get("result", {}))
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"Connector call failed ({e.response.status_code}): {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to call MCP Connector: {e}")
            raise

    async def _call_stdio(self, server: Dict[str, Any], tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if ClientSession is None or stdio_client is None or StdioServerParameters is None: raise ImportError("MCP SDK not installed")
        command = server.get("command")
        if not command: raise ValueError("Command required")
        
        params = StdioServerParameters(
            command=command,
            args=server.get("args", []),
            env={**os.environ, **server.get("env", {})}
        )
        
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                return self._format_result(result)

    async def _call_sse(self, server: Dict[str, Any], tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if ClientSession is None or sse_client is None: raise ImportError("MCP SDK not installed")
        endpoint = server.get("endpoint")
        if not endpoint: raise ValueError("Endpoint required")
             
        async with sse_client(endpoint) as (read, write):
             async with ClientSession(read, write) as session:
                 await session.initialize()
                 result = await session.call_tool(tool, args)
                 return self._format_result(result)

    def _format_result(self, result: Any) -> Dict[str, Any]:
        content = []
        for item in result.content:
            if hasattr(item, 'text'):
                content.append({"type": "text", "text": item.text})
            elif hasattr(item, 'data'):
                 content.append({"type": "image", "data": item.data, "mimeType": item.mimeType})
        return {"content": content, "is_error": getattr(result, 'isError', False)}
