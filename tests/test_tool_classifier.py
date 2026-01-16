"""Tests for Gateway tool classifier (Phase 9.2.2)."""
import pytest
from app.domain.mcp.classifier import (
    GatewayToolClassifier,
    ToolClassificationError,
    ToolClass,
    ToolClassification,
)


class TestGatewayToolClassifier:
    """Test GatewayToolClassifier behavior."""

    def test_classify_unclassified_dev_mode(self):
        """Dev mode should return None for unclassified tools."""
        classifier = GatewayToolClassifier(env="dev")
        result = classifier.classify("unknown-server", "unknown-tool")
        assert result is None

    def test_classify_unclassified_prod_mode(self):
        """Prod mode should raise for unclassified tools."""
        classifier = GatewayToolClassifier(env="prod")
        with pytest.raises(ToolClassificationError) as exc:
            classifier.classify("unknown-server", "unknown-tool")
        assert exc.value.code == "TOOL_UNCLASSIFIED_DENIED"

    def test_validate_capability_read_ok(self):
        """Read tool with read-only capability should pass."""
        classification = ToolClassification(
            server_id="mcp-github",
            tool_name="list-issues",
            tool_class=ToolClass.READ,
            is_document_op=False,
            requires_idempotency_key=False,
        )
        classifier = GatewayToolClassifier(env="dev")
        classifier.validate_capability(classification, capability_read_only=True)
        # No exception = pass

    def test_validate_capability_write_with_readonly_fails(self):
        """Write tool with read-only capability should fail."""
        classification = ToolClassification(
            server_id="mcp-github",
            tool_name="create-pr",
            tool_class=ToolClass.WRITE,
            is_document_op=True,
            requires_idempotency_key=True,
        )
        classifier = GatewayToolClassifier(env="dev")
        with pytest.raises(ToolClassificationError) as exc:
            classifier.validate_capability(classification, capability_read_only=True)
        assert exc.value.code == "TOOL_CLASS_MISMATCH"

    def test_validate_declaration_mismatch(self):
        """Agent-declared tool_class mismatch should fail."""
        classification = ToolClassification(
            server_id="mcp-github",
            tool_name="create-pr",
            tool_class=ToolClass.WRITE,
            is_document_op=True,
            requires_idempotency_key=True,
        )
        classifier = GatewayToolClassifier(env="dev")
        with pytest.raises(ToolClassificationError) as exc:
            classifier.validate_declaration(classification, declared_tool_class="read")
        assert exc.value.code == "TOOL_CLASS_DECLARATION_MISMATCH"

    def test_build_audit_context_with_classification(self):
        """Audit context should include classification info."""
        classification = ToolClassification(
            server_id="mcp-github",
            tool_name="create-pr",
            tool_class=ToolClass.WRITE,
            is_document_op=True,
            requires_idempotency_key=True,
        )
        classifier = GatewayToolClassifier(env="dev")
        context = classifier.build_audit_context(
            classification,
            document_hashes=[{"pointer": "/content", "hash": "abc123", "size_bytes": 100}],
            batch_total_bytes=100
        )
        
        assert context["tool_class"] == "write"
        assert context["is_document_op"] is True
        assert len(context["document_hashes"]) == 1
        assert context["batch_total_bytes"] == 100

    def test_build_audit_context_unclassified(self):
        """Audit context for unclassified should show unclassified."""
        classifier = GatewayToolClassifier(env="dev")
        context = classifier.build_audit_context(None)
        
        assert context["tool_class"] == "unclassified"
        assert context["is_document_op"] is False
