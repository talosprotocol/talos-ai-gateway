"""Gateway Tool Classifier for Phase 9.2.2 enforcement.

This module implements secondary enforcement (belt-and-suspenders) at Gateway level:
- Re-derives tool_class from registry (never trusts agent-declared)
- Validates capability read_only vs tool_class
- Emits audit events with tool classification info
"""
import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolClass(str, Enum):
    READ = "read"
    WRITE = "write"


class ToolClassificationError(Exception):
    """Error during tool classification."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass
class ToolClassification:
    """Classification result for a tool."""
    server_id: str
    tool_name: str
    tool_class: ToolClass
    is_document_op: bool
    requires_idempotency_key: bool


class GatewayToolClassifier:
    """
    Gateway-side tool classification per Phase 9.2 LOCKED spec.
    
    Security invariants:
    - Tool_class MUST be re-derived from registry (never trust agent)
    - Unknown tools denied in production
    - Validates capability read_only against tool_class
    - Must use same registry version as connector (no split-brain)
    """
    
    def __init__(
        self, 
        registry_dir: Optional[str] = None,
        env: str = "dev"
    ):
        """
        Initialize classifier.
        
        Args:
            registry_dir: Directory containing tool registry JSON files
            env: Environment - "dev" or "prod"
        """
        self.env = env
        self.registry: Dict[tuple, ToolClassification] = {}
        
        if registry_dir and Path(registry_dir).exists():
            self._load_registries(registry_dir)
        else:
            logger.warning("No tool registries loaded - running in permissive mode")
    
    def _load_registries(self, registry_dir: str) -> None:
        """Load all tool registries from directory."""
        registry_path = Path(registry_dir)
        
        for registry_file in registry_path.glob("*.json"):
            try:
                self._load_registry(str(registry_file))
            except Exception as e:
                logger.error(f"Failed to load registry {registry_file}: {e}")
    
    def _load_registry(self, path: str) -> None:
        """Load a single tool registry file."""
        with open(path, 'r') as f:
            data = json.load(f)
        
        # Validate schema_id
        if data.get("schema_id") != "talos.mcp.tool_registry":
            logger.warning(f"Skipping non-registry file: {path}")
            return
        
        server_id = data.get("server_id")
        for tool_def in data.get("tools", []):
            tool_name = tool_def["tool_name"]
            
            classification = ToolClassification(
                server_id=server_id,
                tool_name=tool_name,
                tool_class=ToolClass(tool_def["tool_class"]),
                is_document_op=tool_def.get("is_document_op", False),
                requires_idempotency_key=tool_def.get("requires_idempotency_key", False),
            )
            
            self.registry[(server_id, tool_name)] = classification
            logger.debug(f"Registered {server_id}:{tool_name} as {classification.tool_class}")
    
    def classify(
        self, 
        server_id: str, 
        tool_name: str
    ) -> Optional[ToolClassification]:
        """
        Classify a tool by re-deriving from registry.
        
        Args:
            server_id: MCP server identifier
            tool_name: Tool name
        
        Returns:
            ToolClassification if found, None if unclassified (dev mode)
        
        Raises:
            ToolClassificationError: If unclassified in production
        """
        classification = self.registry.get((server_id, tool_name))
        
        if classification is None:
            if self.env == "prod":
                raise ToolClassificationError(
                    f"Tool {server_id}:{tool_name} not in registry",
                    "TOOL_UNCLASSIFIED_DENIED"
                )
            logger.warning(f"UNCLASSIFIED tool: {server_id}:{tool_name} (dev mode)")
        
        return classification
    
    def validate_capability(
        self,
        classification: ToolClassification,
        capability_read_only: bool
    ) -> None:
        """
        Validate tool class against capability read_only flag.
        
        Raises:
            ToolClassificationError: If write tool called with read-only capability
        """
        if capability_read_only and classification.tool_class == ToolClass.WRITE:
            raise ToolClassificationError(
                f"Write tool '{classification.tool_name}' called with read-only capability",
                "TOOL_CLASS_MISMATCH"
            )
    
    def validate_declaration(
        self,
        classification: ToolClassification,
        declared_tool_class: Optional[str]
    ) -> None:
        """
        Validate agent-declared tool_class against registry.
        
        Note: This is a secondary check - gateway re-derives classification
        regardless, but if agent declares and it mismatches, we deny.
        
        Raises:
            ToolClassificationError: If declaration mismatches registry
        """
        if declared_tool_class and declared_tool_class != classification.tool_class.value:
            raise ToolClassificationError(
                f"Declared '{declared_tool_class}' != registry '{classification.tool_class.value}'",
                "TOOL_CLASS_DECLARATION_MISMATCH"
            )
    
    def build_audit_context(
        self,
        classification: Optional[ToolClassification],
        document_hashes: Optional[List[Dict[str, Any]]] = None,
        batch_total_bytes: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Build audit context for tool classification.
        
        Returns:
            Dict with classification info for audit events
        """
        context: Dict[str, Any] = {}
        
        if classification:
            context["tool_class"] = classification.tool_class.value
            context["is_document_op"] = classification.is_document_op
        else:
            context["tool_class"] = "unclassified"
            context["is_document_op"] = False
        
        if document_hashes:
            context["document_hashes"] = document_hashes
        
        if batch_total_bytes is not None:
            context["batch_total_bytes"] = batch_total_bytes
        
        return context


# Singleton for dependency injection
_classifier_instance: Optional[GatewayToolClassifier] = None


def get_tool_classifier() -> GatewayToolClassifier:
    """Get or create the tool classifier singleton."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = GatewayToolClassifier()
    return _classifier_instance


def init_tool_classifier(registry_dir: Optional[str], env: str = "dev") -> GatewayToolClassifier:
    """Initialize the tool classifier with specific configuration."""
    global _classifier_instance
    _classifier_instance = GatewayToolClassifier(registry_dir=registry_dir, env=env)
    return _classifier_instance
