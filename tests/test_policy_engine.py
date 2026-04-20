"""Tests for RBAC PolicyEngine (Phase 7.1).

Tests scope matching vectors from contracts.
"""
import json
import pytest
from pathlib import Path

from app.domain.rbac.models import (
    Role,
    Binding,
    BindingEntry,
    BindingDocument,
    Scope,
    ScopeType,
    AuthzReasonCode,
)
from app.domain.rbac.policy_engine import PolicyEngine


VECTORS_PATH = Path(__file__).parent.parent.parent.parent / \
    "talos-contracts/test_vectors/rbac/scope_match_vectors.json"


class TestScopeMatching:
    """Test scope matching per LOCKED SPEC vectors."""

    @pytest.fixture
    def engine(self):
        return PolicyEngine()

    def test_global_matches_any_scope(self, engine):
        """Global binding matches any scope."""
        result = engine._match_scope(
            binding_scope=Scope(scope_type=ScopeType.GLOBAL, attributes={}),
            required=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"})
        )
        assert result.matched is True
        assert result.specificity == 0

    def test_exact_repo_match(self, engine):
        """Exact repo match yields +2 specificity."""
        result = engine._match_scope(
            binding_scope=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"}),
            required=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"})
        )
        assert result.matched is True
        assert result.specificity == 2

    def test_wildcard_match(self, engine):
        """Wildcard yields +1 specificity."""
        result = engine._match_scope(
            binding_scope=Scope(scope_type=ScopeType.REPO, attributes={"repo": "*"}),
            required=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/contracts"})
        )
        assert result.matched is True
        assert result.specificity == 1

    def test_scope_type_mismatch(self, engine):
        """Different scope types don't match."""
        result = engine._match_scope(
            binding_scope=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"}),
            required=Scope(scope_type=ScopeType.SECRET, attributes={"secret_id": "api-key"})
        )
        assert result.matched is False
        assert result.reason == "scope_type_mismatch"

    def test_missing_attribute(self, engine):
        """Missing required attribute fails."""
        result = engine._match_scope(
            binding_scope=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"}),
            required=Scope(scope_type=ScopeType.REPO, attributes={})
        )
        assert result.matched is False
        assert result.reason == "missing_attribute"

    def test_attribute_mismatch(self, engine):
        """Mismatched attribute value fails."""
        result = engine._match_scope(
            binding_scope=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"}),
            required=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/contracts"})
        )
        assert result.matched is False
        assert result.reason == "attribute_mismatch"


class TestPolicyEngineResolve:
    """Test PolicyEngine.resolve per LOCKED SPEC."""

    @pytest.fixture
    def setup_engine(self):
        """Create engine with test data."""
        roles = {
            "role_admin": Role(
                role_id="role_admin",
                name="Admin Role",
                permissions=["secrets.read", "secrets.write"]
            ),
            "role_viewer": Role(
                role_id="role_viewer",
                name="Viewer Role",
                permissions=["secrets.read"]
            ),
        }
        
        binding_doc = BindingDocument(
            principal_id="user_123",
            bindings=[
                BindingEntry(
                    binding_id="bind_global",
                    role_id="role_viewer",
                    scope=Scope(scope_type=ScopeType.GLOBAL, attributes={})
                ),
                BindingEntry(
                    binding_id="bind_repo",
                    role_id="role_admin",
                    scope=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"})
                ),
            ]
        )
        
        return PolicyEngine(
            roles=roles,
            binding_docs={"user_123": binding_doc}
        )

    @pytest.mark.asyncio
    async def test_allow_with_global_binding(self, setup_engine):
        """Global binding allows any scope."""
        decision = await setup_engine.resolve(
            principal_id="user_123",
            permission="secrets.read",
            request_scope=Scope(scope_type=ScopeType.SECRET, attributes={"secret_id": "any"})
        )
        assert decision.allowed is True
        assert decision.reason_code == AuthzReasonCode.ALLOWED

    @pytest.mark.asyncio
    async def test_higher_specificity_wins(self, setup_engine):
        """More specific binding wins over global."""
        decision = await setup_engine.resolve(
            principal_id="user_123",
            permission="secrets.read",
            request_scope=Scope(scope_type=ScopeType.REPO, attributes={"repo": "talosprotocol/talos"})
        )
        assert decision.allowed is True
        assert decision.effective_binding_id == "bind_repo"  # More specific

    @pytest.mark.asyncio
    async def test_deny_no_bindings(self, setup_engine):
        """No bindings returns BINDING_NOT_FOUND."""
        decision = await setup_engine.resolve(
            principal_id="unknown_user",
            permission="secrets.read",
            request_scope=Scope(scope_type=ScopeType.GLOBAL, attributes={})
        )
        assert decision.allowed is False
        assert decision.reason_code == AuthzReasonCode.BINDING_NOT_FOUND

    @pytest.mark.asyncio
    async def test_deny_no_permission(self, setup_engine):
        """No matching permission returns PERMISSION_DENIED."""
        decision = await setup_engine.resolve(
            principal_id="user_123",
            permission="secrets.delete",
            request_scope=Scope(scope_type=ScopeType.GLOBAL, attributes={})
        )
        assert decision.allowed is False
        assert decision.reason_code == AuthzReasonCode.DENIED

    @pytest.mark.asyncio
    async def test_audit_dict_format(self, setup_engine):
        """AuthzDecision.to_audit_dict() produces correct format."""
        decision = await setup_engine.resolve(
            principal_id="user_123",
            permission="secrets.read",
            request_scope=Scope(scope_type=ScopeType.GLOBAL, attributes={})
        )
        audit = decision.to_audit_dict()
        
        assert audit["authz_decision"] == "ALLOW"
        assert audit["authz_reason_code"] == "RBAC_PERMISSION_ALLOWED"
        assert audit["permission"] == "secrets.read"
        assert audit["scope_type"] == "global"
        assert "matched_role_ids" in audit
