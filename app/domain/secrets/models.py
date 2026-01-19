"""Secrets Domain Models."""
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator

ALGORITHM_AES_256_GCM = "aes-256-gcm"
SCHEMA_ID_ENVELOPE = "talos.secrets.envelope"
SCHEMA_VERSION_V1 = "v1"

class EncryptedEnvelope(BaseModel):
    """
    Encrypted data envelope (v1 Normative).
    
    Ensures structural integrity and metadata compliance for secrets-at-rest.
    All binary fields are stored as Base64URL string without padding.
    """
    kek_id: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    nonce_b64u: str = Field(..., description="12-byte nonce, Base64URL no padding")
    ciphertext_b64u: str = Field(..., description="Encrypted data, Base64URL no padding")
    tag_b64u: str = Field(..., description="16-byte authentication tag, Base64URL no padding")
    aad_b64u: Optional[str] = Field(None, description="Additional Authenticated Data, Base64URL no padding")
    
    alg: str = Field(default=ALGORITHM_AES_256_GCM)
    schema_id: str = Field(default=SCHEMA_ID_ENVELOPE)
    schema_version: str = Field(default=SCHEMA_VERSION_V1)
    
    @field_validator("alg")
    @classmethod
    def validate_alg(cls, v):
        if v != ALGORITHM_AES_256_GCM:
            raise ValueError(f"Unsupported algorithm: {v}")
        return v

    @field_validator("schema_version")
    @classmethod
    def validate_version(cls, v):
        if v != SCHEMA_VERSION_V1:
            raise ValueError(f"Unsupported schema version: {v}")
        return v
