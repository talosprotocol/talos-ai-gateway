import sys
import os
sys.path.append(os.getcwd())


try:
    from app.main import app
    print("✅ Application imported successfully")
    
    # Verify RateLimitMiddleware is present
    found = False
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "RateLimitMiddleware":
            found = True
            break
            
    if found:
        print("✅ RateLimitMiddleware registered")
    else:
        print("❌ RateLimitMiddleware NOT found in middleware stack")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)
