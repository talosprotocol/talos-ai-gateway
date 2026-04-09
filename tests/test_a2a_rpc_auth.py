from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.domain.registry import SurfaceItem
from app.middleware.auth_public import get_auth_context


def _mock_key_data(*, scopes: list[str]):
    key_data = Mock()
    key_data.id = "01946765-c7e0-798c-8c65-22d7a64b91f5"
    key_data.team_id = "01946765-c7e0-798c-8c65-22d7a64b91f6"
    key_data.org_id = "01946765-c7e0-798c-8c65-22d7a64b91f7"
    key_data.scopes = scopes
    key_data.revoked = False
    key_data.allowed_model_groups = ["*"]
    key_data.allowed_mcp_servers = ["*"]
    key_data.budget_mode = "off"
    key_data.team_budget_mode = "off"
    key_data.overdraft_usd = "0"
    key_data.team_overdraft_usd = "0"
    key_data.max_tokens_default = None
    key_data.team_max_tokens_default = None
    key_data.budget = {}
    key_data.team_budget = {}
    return key_data


def _rpc_request(method_name: str):
    request = Mock()
    request.method = "POST"
    request.route = SimpleNamespace(path="/rpc")
    request.url = SimpleNamespace(path="/rpc")
    request.headers = {"Authorization": "Bearer test-token"}
    request.state = SimpleNamespace()
    request.client = None
    request.scope = {"raw_path": b"/rpc", "query_string": b""}
    request.body = AsyncMock(
        return_value=(
            f'{{"jsonrpc":"2.0","id":"req-1","method":"{method_name}","params":{{}}}}'.encode("utf-8")
        )
    )
    return request


def _surface(required_scopes: list[str]) -> SurfaceItem:
    return SurfaceItem(
        id="a2a.v1.rpc",
        type="http",
        required_scopes=required_scopes,
        attestation_required=False,
        audit_action="a2a.rpc.invoke",
        data_classification="sensitive",
        audit_meta_allowlist=["method", "id"],
        path_template="/rpc",
    )


@pytest.mark.asyncio
async def test_auth_context_uses_rpc_method_specific_surface_lookup():
    registry = Mock()
    registry.match_request.return_value = _surface(["a2a.send"])

    key_store = Mock()
    key_store.hash_key.return_value = "hash"
    key_store.lookup_by_hash.return_value = _mock_key_data(scopes=["a2a.send"])

    policy_engine = Mock()
    policy_engine.authorize.return_value = Mock(allowed=False, reason="no policy binding")

    request = _rpc_request("SendMessage")

    ctx = await get_auth_context(
        request,
        authorization="Bearer test-token",
        x_talos_signature=None,
        key_store=key_store,
        verifier=AsyncMock(),
        principal_store=Mock(),
        registry=registry,
        audit_logger=Mock(),
        policy_engine=policy_engine,
    )

    assert ctx.key_id == "01946765-c7e0-798c-8c65-22d7a64b91f5"
    registry.match_request.assert_called_once_with("POST", "/rpc", rpc_method="SendMessage")


@pytest.mark.asyncio
async def test_auth_context_denies_rpc_method_when_scope_missing():
    registry = Mock()
    registry.match_request.return_value = _surface(["a2a.send"])

    key_store = Mock()
    key_store.hash_key.return_value = "hash"
    key_store.lookup_by_hash.return_value = _mock_key_data(scopes=["a2a.get"])

    policy_engine = Mock()
    policy_engine.authorize.return_value = Mock(allowed=False, reason="no policy binding")

    request = _rpc_request("SendMessage")

    with pytest.raises(HTTPException) as exc:
        await get_auth_context(
            request,
            authorization="Bearer test-token",
            x_talos_signature=None,
            key_store=key_store,
            verifier=AsyncMock(),
            principal_store=Mock(),
            registry=registry,
            audit_logger=Mock(log_event=Mock()),
            policy_engine=policy_engine,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == "RBAC_DENIED"
    registry.match_request.assert_called_once_with("POST", "/rpc", rpc_method="SendMessage")
