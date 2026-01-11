"""Auth Domain Logic."""
import jwt
import requests
import logging
import os
from typing import Optional, Dict, List, Set, Any
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException

logger = logging.getLogger(__name__)

class JwtValidator:
    def __init__(self, jwks_url: Optional[str] = None, issuer: Optional[str] = None, audience: Optional[str] = None, secret: Optional[str] = None):
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
        self.secret = secret
        self._jwks_cache: Optional[Dict[str, Any]] = None
        self._jwks_last_fetch: Optional[datetime] = None

    def _fetch_jwks(self) -> Dict[str, Any]:
        if not self.jwks_url:
            return {}
        
        now = datetime.now(timezone.utc)
        if self._jwks_cache and self._jwks_last_fetch and (now - self._jwks_last_fetch) < timedelta(hours=1):
            return self._jwks_cache

        try:
            response = requests.get(self.jwks_url, timeout=10)
            response.raise_for_status()
            self._jwks_cache = response.json()
            self._jwks_last_fetch = now
            return self._jwks_cache
        except Exception as e:
            logger.error(f"Failed to fetch JWKS from {self.jwks_url}: {e}")
            if self._jwks_cache:
                return self._jwks_cache
            raise HTTPException(status_code=500, detail="Identity provider unavailable")

    def validate_token(self, token: str) -> Dict[str, Any]:
        """Validate JWT and return claims."""
        try:
            if self.secret and not self.jwks_url:
                # Symmetric validation for DEV/internal
                return jwt.decode(token, self.secret, algorithms=["HS256"], audience=self.audience, issuer=self.issuer)
            
            # Asymmetric validation via JWKS
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")
            
            jwks = self._fetch_jwks()
            public_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                    break
            
            if not public_key and jwks:
                raise HTTPException(status_code=401, detail="Invalid token key ID")
            
            return jwt.decode(
                token, 
                public_key, 
                algorithms=["RS256"], 
                audience=self.audience, 
                issuer=self.issuer
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            raise HTTPException(status_code=401, detail="Invalid token")
        except Exception as e:
            logger.error(f"Unexpected error during token validation: {e}")
            raise HTTPException(status_code=401, detail="Authentication failed")

def get_admin_validator() -> JwtValidator:
    """Get singleton-like validator for admin API."""
    return JwtValidator(
        jwks_url=os.getenv("AUTH_ADMIN_JWKS_URL"),
        issuer=os.getenv("AUTH_ADMIN_ISSUER"),
        audience=os.getenv("AUTH_ADMIN_AUDIENCE"),
        secret=os.getenv("AUTH_ADMIN_SECRET")
    )
