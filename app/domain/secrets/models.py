"""Secrets Domain Models."""
from datetime import datetime, timezone
from typing import Dict, Any
from pydantic import BaseModel, Field, field_validator

ALGORITHM_AES_256_GCM = "aes-256-gcm"
SCHEMA_ID_ENVELOPE = "talos.secrets.envelope"
SCHEMA_VERSION_V1 = "v1"

class EncryptedEnvelope(BaseModel):
    """
    Encrypted data envelope (Draft 2020-12 / Normative).
    
    Ensures structural integrity and metadata compliance for secrets-at-rest.
    All binary fields are stored as lowercase hex strings.
    """
    kek_id: str = Field(..., min_length=1, max_length=255)
    iv: str = Field(..., pattern=r"^[0-9a-f]{24}$")        # 24 hex char (12 bytes)
    ciphertext: str = Field(..., pattern=r"^[0-9a-f]+$") # Hex
    tag: str = Field(..., pattern=r"^[0-9a-f]{32}$")       # 32 hex char (16 bytes)
    alg: str = Field(default=ALGORITHM_AES_256_GCM)
    schema_id: str = Field(default=SCHEMA_ID_ENVELOPE)
    schema_version: str = Field(default=SCHEMA_VERSION_V1)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")

    @field_validator("alg")
    @classmethod
    def validate_alg(cls, v):
        if v != ALGORITHM_AES_256_GCM:
            raise ValueError(f"Unsupported algorithm: {v}")
        return v
