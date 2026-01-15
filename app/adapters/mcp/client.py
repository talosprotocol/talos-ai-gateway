"""MCP Client Adapter."""
import logging
import os
from typing import Dict, Any

try:
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    from mcp.client.session import ClientSession
    from mcp.types import CallToolResult, TextContent, ImageContent, EmbeddedResource
except ImportError:
    # Fallback/Mock if SDK issues
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
        arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        if not ClientSession:
             raise ImportError("MCP SDK not installed or imports failed")

        transport = server_config.get("transport", "stdio")
        
        try:
            if transport == "stdio":
                return await self._call_stdio(server_config, tool_name, arguments)
            elif transport == "sse":
                return await self._call_sse(server_config, tool_name, arguments)
            else:
                raise ValueError(f"Unsupported transport: {transport}")
        except Exception as e:
            logger.error(f"MCP Tool Call Failed: {e}")
            raise

    async def _call_stdio(self, server: Dict[str, Any], tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        command = server.get("command")
        if not command:
             raise ValueError("Command required for stdio transport")
             
        server_args = server.get("args", [])
        env = server.get("env", {})
        
        full_env = os.environ.copy()
        full_env.update(env)
        
        params = StdioServerParameters(
            command=command,
            args=server_args,
            env=full_env
        )
        
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                return self._format_result(result)

    async def _call_sse(self, server: Dict[str, Any], tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = server.get("endpoint")
        if not endpoint:
             raise ValueError("Endpoint required for SSE transport")
             
        async with sse_client(endpoint) as (read, write):
             async with ClientSession(read, write) as session:
                 await session.initialize()
                 result = await session.call_tool(tool, args)
                 return self._format_result(result)

    def _format_result(self, result: Any) -> Dict[str, Any]:
        # result is CallToolResult
        content = []
        
        for item in result.content:
            if item.type == "text":
                content.append({"type": "text", "text": item.text})
            elif item.type == "image":
                 content.append({"type": "image", "data": item.data, "mimeType": item.mimeType})
            elif item.type == "resource":
                 content.append({"type": "resource", "uri": item.resource.uri, "text": item.resource.text if hasattr(item.resource, 'text') else None})
        
        return {
            "content": content,
            "is_error": result.isError
        }
