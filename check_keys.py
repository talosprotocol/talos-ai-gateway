import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add app to path
sys.path.append(os.path.join(os.getcwd(), "services/ai-gateway"))

from app.adapters.postgres.models import VirtualKey

DATABASE_URL = "postgresql://talos:talos_dev_password@localhost:5433/talos"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

def check():
    keys = db.query(VirtualKey).all()
    print(f"Found {len(keys)} keys in DB.")
    for k in keys:
        print(f"ID: {k.id}, Hash: {k.key_hash}, Revoked: {k.revoked}, Scopes: {k.scopes}")
    db.close()

if __name__ == "__main__":
    check()
