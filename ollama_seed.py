import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add app to path
sys.path.append(os.path.join(os.getcwd(), "services/ai-gateway"))

from app.adapters.postgres.models import Base, LlmUpstream, ModelGroup, Deployment, Team, Org, VirtualKey
from app.utils.id import uuid7

# Use the port found in start.sh output (5433)
DATABASE_URL = "postgresql://talos:talos_dev_password@localhost:5433/talos"

def seed_ollama():
    engine = create_engine(DATABASE_URL)
    
    # Initialize Tables
    print("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        # 0. Create Org and Team for Dev
        org_id = str(uuid7())
        team_id = str(uuid7())
        key_id = str(uuid7())
        
        db.query(VirtualKey).delete()
        db.query(Team).delete()
        db.query(Org).delete()
        
        org = Org(id=org_id, name="Dev Org")
        db.add(org)
        print(f"Created Org: {org_id}")
        
        team = Team(id=team_id, org_id=org_id, name="Dev Team")
        db.add(team)
        print(f"Created Team: {team_id}")
            
        vk = VirtualKey(
            id=key_id,
            team_id=team_id,
            key_hash="p1:a5b691eedbc9280706218172ff3382f965ced2593f4046ece8da8b9290cd8f0a", # test-key-hard
            scopes=["*:*", "a2a.*", "llm.*", "mcp.*"],
            allowed_model_groups=["*"],
            allowed_mcp_servers=["*"],
            revoked=False
        )
        db.add(vk)
        print(f"Created VirtualKey: {key_id}")

        # 1. Create Ollama Upstream
        ollama_id = "ollama-local"
        existing = db.query(LlmUpstream).filter(LlmUpstream.id == ollama_id).first()
        if not existing:
            upstream = LlmUpstream(
                id=ollama_id,
                provider="openai", 
                endpoint="http://localhost:11434/v1",
                credentials_ref="NONE",
                enabled=True,
                version=1
            )
            db.add(upstream)
            print(f"Created Ollama upstream: {ollama_id}")

        # 2. Create Model Group
        group_id = "ollama-group"
        existing_group = db.query(ModelGroup).filter(ModelGroup.id == group_id).first()
        if not existing_group:
            group = ModelGroup(
                id=group_id,
                name="Ollama Group",
                enabled=True,
                version=1
            )
            db.add(group)
            db.flush() 

            # Add Deployment for gemma4
            deployment = Deployment(
                id=str(uuid7()),
                model_group_id=group_id,
                upstream_id=ollama_id,
                model_name="gemma4:latest",
                weight=100
            )
            db.add(deployment)
            print(f"Created Model Group {group_id} with gemma4:latest")

        db.commit()
    except Exception as e:
        print(f"Error seeding: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_ollama()
