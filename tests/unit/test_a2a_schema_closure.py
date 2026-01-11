import json
from pathlib import Path
import pytest

SCHEMA_BASE = Path(__file__).resolve().parent.parent.parent.parent / "talos-contracts" / "schemas" / "a2a"

def test_schema_closure_registry():
    index_path = SCHEMA_BASE / "index.json"
    assert index_path.exists(), "index.json registry missing"
    
    with open(index_path) as f:
        data = json.load(f)
        
    registry_schemas = data.get("schemas", [])
    assert len(registry_schemas) > 0
    
    for schema_rel_path in registry_schemas:
        schema_path = SCHEMA_BASE / schema_rel_path
        assert schema_path.exists(), f"Schema {schema_rel_path} listed in index.json but missing on disk"
        
        # Verify valid JSON
        with open(schema_path) as f:
            try:
                json.load(f)
            except json.JSONDecodeError:
                pytest.fail(f"Schema {schema_rel_path} is not valid JSON")

def test_all_schemas_in_registry():
    # Ensure no orphan schemas exist that aren't in index.json (optional but good for closure)
    index_path = SCHEMA_BASE / "index.json"
    with open(index_path) as f:
        data = json.load(f)
    registry_schemas = set(data.get("schemas", []))
    
    actual_schemas = set()
    for p in SCHEMA_BASE.rglob("*.json"):
        if p.name == "index.json":
            continue
        rel = str(p.relative_to(SCHEMA_BASE))
        actual_schemas.add(rel)
        
    missing_from_registry = actual_schemas - registry_schemas
    assert not missing_from_registry, f"Schemas found on disk but missing from index.json: {missing_from_registry}"
