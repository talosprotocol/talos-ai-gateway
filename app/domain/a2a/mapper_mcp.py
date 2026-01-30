"""A2A <-> MCP Mapper."""
from typing import Dict, Any, Optional
import datetime

from app.api.a2a.jsonrpc import JsonRpcException
from app.domain.mcp import registry
from app.adapters.mcp.client import McpClient
from app.middleware.auth_public import AuthContext

class McpMapper:
    def __init__(self, mcp_client: McpClient, audit_store, capability_validator: Optional[Any] = None):
        self.mcp_client = mcp_client
        self.audit_store = audit_store
        self.capability_validator = capability_validator

    async def execute_tool(
        self, 
        tool_call: Dict[str, Any], 
        auth_context: AuthContext, 
        request_id: str,
        capability: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute an MCP tool call deterministically.
        
        Security:
        1. Access Token must have 'mcp.invoke' scope (in addition to a2a.invoke checked by dispatcher).
        2. 'server_id' must be in token's allowed_mcp_servers (or *).
        3. 'server_id' + 'tool_name' must be allowed by Team Policy.
        """
        
        # 1. Scope Check
        if "mcp.invoke" not in auth_context.scopes:
             raise JsonRpcException(-32000, "Access Denied", data={"talos_code": "RBAC_DENIED", "details": "Missing scope: mcp.invoke"})

        server_id = tool_call.get("server_id")
        tool_name = tool_call.get("tool_name")
        arguments = tool_call.get("arguments", {})

        if not server_id or not tool_name:
             raise JsonRpcException(-32602, "Invalid Params", data={"details": "Missing server_id or tool_name"})
             
        # --- TGA Capability Enforcement ---
        if capability:
            if not self.capability_validator:
                raise JsonRpcException(-32000, "Capability validation unavailable", data={"talos_code": "CONFIG_ERROR"})
            
            # 1. Decode & Verify Signature
            try:
                cap = self.capability_validator.decode_and_verify(capability)
            except Exception as e:
                # Map to JGA_DENIED or similar
                raise JsonRpcException(-32000, "Capability Invalid", data={"talos_code": "TGA_CAPABILITY_INVALID", "details": str(e)})

            # 2. Verify Call-Capability Digest Binding
            expected_digest = tool_call.get("capability_digest")
            if expected_digest:
                actual_digest = self.capability_validator.calculate_capability_digest(capability)
                if actual_digest != expected_digest:
                     raise JsonRpcException(-32000, "Capability Mismatch", data={"talos_code": "TGA_DIGEST_MISMATCH", "details": "ToolCall.capability_digest does not match provided token"})

            # 3. Enforce Constraints
            try:
                self.capability_validator.validate_tool_call(cap, server_id, tool_name, arguments)
            except Exception as e:
                raise JsonRpcException(-32000, "Constraint Violation", data={"talos_code": "TGA_CONSTRAINT_VIOLATION", "details": str(e)})

            # 4. Audit Trace IDs
            if self.audit_store:
                # We'll attach trace_id and plan_id to the audit event below
                pass

        # 4. Key Access Check
        if not auth_context.can_access_mcp_server(server_id):
             raise JsonRpcException(-32000, "Access Denied", data={"talos_code": "MCP_DENIED_SERVER", "details": "Key cannot access this server"})

        # 5. Policy Check
        if not registry.is_tool_allowed(auth_context.team_id, server_id, tool_name):
             raise JsonRpcException(-32000, "Access Denied", data={"talos_code": "MCP_DENIED_TOOL", "details": "Prohibited by Team Policy"})

        # 4. Get Server Config
        server_config = registry.get_server(server_id)
        if not server_config:
             raise JsonRpcException(-32000, "Server Not Found", data={"talos_code": "MCP_SERVER_NOT_FOUND"})

        # 5. Audit Event (Before Execution) - using same request_id
        if self.audit_store:
            audit_event = {
                "type": "mcp.tool.call",
                "request_id": request_id, 
                "team_id": auth_context.team_id,
                "key_id": auth_context.key_id,
                "surface": "a2a",
                "server_id": server_id,
                "tool_name": tool_name
            }
            # Attach TGA trace info if available from capability
            if capability:
                # We can re-decode or pass cap down. Let's re-decode for isolation or pass cap.
                # Actually cap is local to execute_tool if it was decoded.
                # Let's assume we want to log the capability digest too.
                audit_event["tga"] = {
                    "capability_digest": self.capability_validator.calculate_capability_digest(capability) if self.capability_validator else None
                }
                # If we decoded it successfully above, cap variable is available in the scope.
                # But to be safe, we'll try to re-decode or pass it.
                # Since we already verified it above, we can just use the 'cap' variable if we move the audit call down.
            
            await self.audit_store.log_event(audit_event)

        # 6. Execute via Client
        try:
            result = await self.mcp_client.call_tool(
                server_config=server_config,
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=tool_call.get("idempotency_key"),
                principal_id=auth_context.key_id,
                capability_read_only=False
            )
        except Exception as e:
            # Map transport/execution errors to Domain Error
            raise JsonRpcException(-32000, "Tool Execution Failed", data={"talos_code": "MCP_TRANSPORT_ERROR", "details": str(e)})

        # 7. Map Result to Task
        # A2A Task format requires: id, status, created_at, output (list of blobs/msgs)
        # We simplify output mapping for now.
        
        output_content = result.get("content", [])
        is_error = result.get("is_error", False)
        
        # Structure as a Task resource
        task = {
            "id": request_id, # Re-use request ID as task ID for A1 synchronous model
            "status": "failed" if is_error else "completed",
            "created_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "completed_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "output": [
                {
                    "role": "model",
                    "content": [self._map_content_block(c) for c in output_content]
                }
            ]
        }
        
        return task

    def _map_content_block(self, block: Dict[str, Any]) -> Dict[str, Any]:
        """Map MCP content block to A2A message content block."""
        # MCP: type=text, text=...
        # A2A: text=... (for text blocks)
        if block.get("type") == "text":
            return {"text": block.get("text", "")}
        elif block.get("type") == "image":
            # A2A image block format? For now treating as opaque or text representation
            return {"text": f"[Image: {block.get('mimeType')}]"}
        elif block.get("type") == "resource":
             return {"text": f"[Resource: {block.get('uri')}]"}
        return {"text": "[Unknown Content]"}
