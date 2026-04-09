from pathlib import Path

import pytest

from app import dependencies


def test_find_upwards_discovers_contract_inventory(tmp_path: Path):
    repo_root = tmp_path / "repo"
    service_root = repo_root / "services" / "ai-gateway"
    contracts_inventory = repo_root / "contracts" / "inventory" / "gateway_surface.json"
    contracts_inventory.parent.mkdir(parents=True)
    contracts_inventory.write_text('{"version":"1.0","items":[]}')
    (service_root / "app").mkdir(parents=True)

    discovered = dependencies._find_upwards(service_root, "contracts/inventory/gateway_surface.json")

    assert discovered == contracts_inventory


def test_find_upwards_returns_none_when_contract_inventory_absent(tmp_path: Path):
    service_root = tmp_path / "repo" / "services" / "ai-gateway"
    service_root.mkdir(parents=True)

    discovered = dependencies._find_upwards(service_root, "contracts/inventory/gateway_surface.json")

    assert discovered is None


def test_rbac_surface_registry_path_prefers_contract_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo_root = tmp_path / "repo"
    service_root = repo_root / "services" / "ai-gateway"
    contracts_inventory = repo_root / "contracts" / "inventory" / "gateway_surface.json"
    contracts_inventory.parent.mkdir(parents=True)
    contracts_inventory.write_text('{"version":"1.0","items":[]}')
    service_root.mkdir(parents=True)

    monkeypatch.delenv("RBAC_SURFACE_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(dependencies, "_service_root", lambda: service_root)

    resolved = dependencies._resolve_rbac_surface_registry_path()

    assert resolved == str(contracts_inventory)


def test_surface_inventory_path_raises_when_contract_inventory_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    service_root = tmp_path / "repo" / "services" / "ai-gateway"
    service_root.mkdir(parents=True)

    monkeypatch.delenv("SURFACE_INVENTORY_PATH", raising=False)
    monkeypatch.delenv("RBAC_SURFACE_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(dependencies, "_service_root", lambda: service_root)

    with pytest.raises(RuntimeError, match="contracts/inventory/gateway_surface.json"):
        dependencies._resolve_auth_surface_inventory_path()

    with pytest.raises(RuntimeError, match="contracts/inventory/gateway_surface.json"):
        dependencies._resolve_rbac_surface_registry_path()
