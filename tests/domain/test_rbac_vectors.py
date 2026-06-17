import json
import pytest
import os
from app.domain.rbac.policy_engine import PolicyEngine
from app.domain.rbac.models import Scope

from pathlib import Path

# Path to vectors
ROOT_DIR = Path(__file__).resolve().parents[4]
VECTOR_PATH = ROOT_DIR / "contracts" / "test_vectors" / "rbac" / "scope_match_vectors.json"

@pytest.fixture
def policy_engine():
    return PolicyEngine()

def load_vectors():
    if not VECTOR_PATH.exists():
        print(f"Vectors not found at {VECTOR_PATH}")
        return []
        
    with open(VECTOR_PATH, "r") as f:
        data = json.load(f)
    return data.get("vectors", [])

@pytest.mark.asyncio
async def test_scope_matching_vectors(policy_engine):
    vectors = load_vectors()
    if not vectors:
        pytest.skip("Test vectors not found")

    for vec in vectors:
        print(f"Running vector: {vec['id']} - {vec['description']}")
        
        req_scope = Scope(**vec["required_scope"])
        bind_scope = Scope(**vec["binding_scope"])
        
        match_result = policy_engine._match_scope(req_scope, bind_scope)
        
        # Check match status
        is_match = match_result.matched
        assert is_match == vec["should_match"], f"Failed match expectation for {vec['id']}"
        
        # Check specificity if it matched
        if is_match and "specificity" in vec:
            assert match_result.specificity == vec["specificity"], f"Specificity mismatch for {vec['id']}"
