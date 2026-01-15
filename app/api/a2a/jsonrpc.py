import json
from pathlib import Path
from typing import Any, Dict, Optional, Union
from pydantic import BaseModel
import jsonschema

# Path to schemas in the monorepo
# Use resolve() to handle running from different CWDs
SCHEMA_BASE = Path(__file__).resolve().parent.parent.parent.parent.parent / "talos-contracts" / "schemas" / "a2a"

class JsonRpcError(BaseModel):
    code: int
    message: str
    data: Optional[Dict[str, Any]] = None

class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Union[str, int, None]
    result: Optional[Any] = None
    error: Optional[JsonRpcError] = None

class JsonRpcRequest(BaseModel):
    jsonrpc: str
    method: str
    params: Optional[Any] = None
    id: Union[str, int]

class SchemaValidator:
    def __init__(self):
        self.schemas: Dict[str, dict] = {}
        self.store: Dict[str, dict] = {} # Map URI -> Schema
        self._load_schemas()
        
        # Critical Safety: Ensure schemas loaded
        if "request" not in self.schemas:
            # Fallback path logic check for debugging (or hard error)
            raise RuntimeError(f"Critical: A2A Schemas not loaded from {SCHEMA_BASE}. Cannot start A2A service.")

    def _load_schemas(self):
        if not SCHEMA_BASE.exists():
            print(f"WARNING: Schema directory {SCHEMA_BASE} not found.")
            return

        # Walk through all json files in SCHEMA_BASE
        for p in SCHEMA_BASE.rglob("*.json"):
            try:
                with open(p) as f:
                    schema = json.load(f)
                    # Add to store by (remote) ID if present
                    if "$id" in schema:
                        self.store[schema["$id"]] = schema
                        # print(f"DEBUG Loaded: {schema['$id']}")
                    
                    # Also map by filename keys for internal convenience
                    relative_name = p.relative_to(SCHEMA_BASE).as_posix()
                    # Mapping logic for known keys:
                    if p.name == "jsonrpc_request.schema.json":
                        self.schemas["request"] = schema
                    elif p.name == "jsonrpc_response.schema.json":
                        self.schemas["response"] = schema
                    elif "methods" in relative_name:
                        # e.g. methods/tasks_send.request.schema.json -> tasks.send.request
                        method_key = p.name.replace(".schema.json", "").replace("_", ".")
                        self.schemas[method_key] = schema
            except Exception as e:
                print(f"Error loading schema {p}: {e}")

    def validate_request_envelope(self, payload: Dict[str, Any]):
        """Validate the outer JSON-RPC envelope."""
        schema = self.schemas.get("request")
        if schema:
            try:
                self._validate(payload, schema)
            except jsonschema.ValidationError as e:
                raise JsonRpcException(-32600, "Invalid Request", data={"details": e.message})

    def validate_method_request(self, method: str, full_payload: Dict[str, Any]):
        """Validate the full request against the method-specific schema."""
        key = f"{method}.request"
        schema = self.schemas.get(key)
        if not schema:
             return

        try:
            self._validate(full_payload, schema)
        except jsonschema.ValidationError as e:
            raise JsonRpcException(-32602, "Invalid params", data={"details": e.message})

    def validate_method_response(self, method: str, full_payload: Dict[str, Any]):
        """Validate the full response against the method-specific schema."""
        key = f"{method}.response"
        schema = self.schemas.get(key)
        if not schema:
             return

        try:
            self._validate(full_payload, schema)
        except jsonschema.ValidationError as e:
            # For response validation, we log as Error but usually don't crash the client if it's production
            # But here we want strict adherence
            raise JsonRpcException(-32603, "Internal response validation error", data={"details": e.message})

    def _validate(self, instance, schema):
        import referencing
        from jsonschema import Draft7Validator
        
        def retrieve(uri):
            # Try exact match
            if uri in self.store:
                return referencing.Resource.from_contents(self.store[uri])
            
            # Fallback: Try matching filename
            filename = uri.split("/")[-1]
            if filename in self.schemas:
                return referencing.Resource.from_contents(self.schemas[filename])
            
            for s in self.store.values():
                if s.get("$id", "").endswith("/" + filename):
                    return referencing.Resource.from_contents(s)

            raise referencing.exceptions.NoSuchResource(uri)

        registry = referencing.Registry(retrieve=retrieve)
        validator = Draft7Validator(schema, registry=registry)
        validator.validate(instance)


class JsonRpcException(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data

validator = SchemaValidator()
