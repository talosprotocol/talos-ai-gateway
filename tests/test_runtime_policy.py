"""Tests for TGA Runtime Policy Enforcement (Phase 9.2)."""
import pytest
from unittest.mock import MagicMock, patch
from app.domain.tga.runtime import (
    TgaRuntime,
    ExecutionPlan,
    ExecutionResult,
)
from app.domain.tga.state_store import (
    TgaStateStore,
    ExecutionStateEnum,
)
from app.domain.mcp.classifier import (
    ToolClassification,
    ToolClass,
    ToolClassificationError
)

class TestTgaRuntimePolicy:
    """Test TgaRuntime integration with GatewayToolClassifier."""

    @pytest.mark.asyncio
    async def test_execute_plan_fail_classification(self):
        """Execution should fail if tool classifier denies it."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        plan = ExecutionPlan(
            trace_id="trace-policy-001",
            plan_id="plan-policy-001",
            action_request={
                "intent": "test", 
                "call": {"name": "server:forbidden_tool", "arguments": {}}
            }
        )
        
        # Mock classifier to raise error
        with patch("app.domain.tga.runtime.get_tool_classifier") as mock_get:
            mock_classifier = MagicMock()
            mock_classifier.classify.side_effect = ToolClassificationError(
                "Unclassified tool detected", 
                "TOOL_UNCLASSIFIED_DENIED"
            )
            mock_get.return_value = mock_classifier
            
            result = await runtime.execute_plan(plan)
            
            assert result.final_state == ExecutionStateEnum.FAILED
            assert "Unclassified tool detected" in result.error
            assert "Policy Violation" in result.error
            
            # Verify log was persisted
            entries = await store.list_log_entries(plan.trace_id)
            assert entries[-1].to_state == ExecutionStateEnum.FAILED
            assert entries[-1].artifact_type == "tool_effect"

    @pytest.mark.asyncio
    async def test_execute_plan_success_with_classification(self):
        """Execution should proceed if tool classifier approves."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        plan = ExecutionPlan(
            trace_id="trace-policy-002",
            plan_id="plan-policy-002",
            action_request={
                "intent": "test", 
                "call": {"name": "server:safe_tool", "arguments": {}}
            }
        )
        
        # Mock classifier to succeed
        with patch("app.domain.tga.runtime.get_tool_classifier") as mock_get:
            mock_classifier = MagicMock()
            mock_classifier.classify.return_value = ToolClassification(
                server_id="server",
                tool_name="safe_tool",
                tool_class=ToolClass.READ,
                is_document_op=False,
                requires_idempotency_key=False
            )
            mock_get.return_value = mock_classifier
            
            result = await runtime.execute_plan(plan)
            
            assert result.final_state == ExecutionStateEnum.COMPLETED
            
            # Verify classify was called with correct args
            mock_classifier.classify.assert_called_with("server", "safe_tool")
