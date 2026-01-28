import json
import pytest
import os
from app.domain.rbac.policy_engine import PolicyEngine
from app.domain.rbac.models import Scope, ScopeType

# Path to vectors
VECTOR_PATH = "../../../contracts/test_vectors/rbac/scope_match_vectors.json"

@pytest.fixture
def policy_engine():
    return PolicyEngine()

def load_vectors():
    # Go up from tests/domain -> ai-gateway -> services -> talos -> contracts
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(base_dir, "../../../contracts/test_vectors/rbac/scope_match_vectors.json")
    
    if not os.path.exists(path):
        # Fallback for different CWD
        path = os.path.abspath(os.path.join(os.getcwd(), "../../contracts/test_vectors/rbac/scope_match_vectors.json"))

    if not os.path.exists(path):
        print(f"Vectors not found at {path}")
        return []
        
    with open(path, "r") as f:
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
        
        score = policy_engine._match_scope(req_scope, bind_scope)
        
        # Check match status
        is_match = score >= 0
        assert is_match == vec["should_match"], f"Failed match expectation for {vec['id']}"
        
        # Check specificity if it matched
        if is_match and "specificity" in vec:
            assert score == vec["specificity"], f"Specificity mismatch for {vec['id']}"
