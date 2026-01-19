import sys
from sqlalchemy import text
from app.dependencies import SessionLocal
from app.adapters.postgres.stores import PostgresSecretStore

def verify_secrets():
    print("Verifying Secrets Encryption...")
    db = SessionLocal()
    store = PostgresSecretStore(db)
    
    name = "test-secret-1"
    value = "super-sensitive-api-key"
    
    print(f"1. Setting secret '{name}' with value '{value}'")
    store.set_secret(name, value)
    
    print("2. Retrieving secret value...")
    retrieved = store.get_secret_value(name)
    if retrieved == value:
        print("   [SUCCESS] Decrypted value matches original.")
    else:
        print(f"   [FAILURE] Decrypted value mismatch. Got: {retrieved}")
        sys.exit(1)
        
    print("3. Inspecting raw database content...")
    # SQL injection safe here as it's test
    result = db.execute(text("SELECT encrypted_value FROM secrets WHERE name = :name"), {"name": name}).fetchone()
    raw_val = result[0]
    print(f"   Raw value in DB: {raw_val}")
    
    if raw_val == value:
        print("   [FAILURE] Raw value matches plaintext! Encryption NOT working.")
        sys.exit(1)
    
    if len(raw_val) > 20 and raw_val != value:
        print("   [SUCCESS] Raw value appears encrypted (different from plaintext).")
    else:
        print("   [WARNING] Raw value suspicious.")

    # Cleanup
    store.delete_secret(name)
    print("4. Cleanup complete.")
    db.close()

if __name__ == "__main__":
    verify_secrets()
