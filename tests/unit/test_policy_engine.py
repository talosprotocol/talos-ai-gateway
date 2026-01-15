import pytest
from app.policy import DeterministicPolicyEngine

# Mock Data
ROLES_DB = {
    "role-admin": {
        "id": "role-admin",
        "permissions": ["*:*"]
    },
    "role-auditor": {
        "id": "role-auditor",
        "permissions": ["audit:read", "audit:export"]
    },
    "role-developer": {
        "id": "role-developer",
        "permissions": ["deploy:*", "logs:read"]
    }
}

BINDINGS_DB = {
    # Super Admin (Platform Scope)
    "user-super": [
        {
            "principal_id": "user-super",
            "role_id": "role-admin",
            "scope": {"type": "platform"}
        }
    ],
    # Org Admin (Org Scope)
    "user-org-admin": [
        {
            "principal_id": "user-org-admin",
            "role_id": "role-auditor",
            "scope": {"type": "org", "org_id": "org-1"}
        }
    ],
    # Team Dev (Team Scope)
    "user-dev": [
        {
            "principal_id": "user-dev",
            "role_id": "role-developer",
            "scope": {"type": "team", "org_id": "org-1", "team_id": "team-A"}
        }
    ]
}

@pytest.fixture
def policy_engine():
    return DeterministicPolicyEngine(ROLES_DB, BINDINGS_DB)

def test_platform_admin_allow_all(policy_engine):
    principal = {"id": "user-super"}
    resource = {"id": "res-1", "org_id": "org-2", "team_id": "team-B"} # Random resource
    
    result = policy_engine.authorize(principal, "audit:delete", resource)
    assert result.allowed
    assert result.role_id == "role-admin"

def test_org_scope_match(policy_engine):
    principal = {"id": "user-org-admin"}
    # Resource in matching org
    resource = {"id": "res-2", "org_id": "org-1"} 
    
    result = policy_engine.authorize(principal, "audit:read", resource)
    assert result.allowed
    assert result.role_id == "role-auditor"

def test_org_scope_mismatch(policy_engine):
    principal = {"id": "user-org-admin"}
    # Resource in DIFFERENT org
    resource = {"id": "res-3", "org_id": "org-2"} 
    
    result = policy_engine.authorize(principal, "audit:read", resource)
    assert not result.allowed
    assert "scope" in result.reason or "No bindings" in result.reason

def test_team_scope_match(policy_engine):
    principal = {"id": "user-dev"}
    # Resource in matching team
    resource = {"id": "res-4", "org_id": "org-1", "team_id": "team-A"}
    
    # Check wildcard in namespace "deploy:*"
    result = policy_engine.authorize(principal, "deploy:rollback", resource)
    assert result.allowed
    assert result.role_id == "role-developer"

def test_team_scope_fail_wrong_team(policy_engine):
    principal = {"id": "user-dev"}
    # Same Org, Wrong Team
    resource = {"id": "res-5", "org_id": "org-1", "team_id": "team-B"}
    
    result = policy_engine.authorize(principal, "deploy:rollback", resource)
    assert not result.allowed

def test_permission_mismatch(policy_engine):
    principal = {"id": "user-org-admin"}
    resource = {"id": "res-2", "org_id": "org-1"}
    
    # Auditor cannot write
    result = policy_engine.authorize(principal, "audit:write", resource)
    assert not result.allowed
    assert "No matching permission" in result.reason
