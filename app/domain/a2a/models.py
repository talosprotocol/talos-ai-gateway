from typing import Optional, Literal
from pydantic import BaseModel, Field, constr
from datetime import datetime

# Common types matching schemas
UUIDv7 = constr(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", min_length=36, max_length=36)
PrincipalID = constr(min_length=1, max_length=255)
Base64Url = constr(pattern=r"^[A-Za-z0-9_-]+$", min_length=1)
SHA256Hex = constr(pattern=r"^[0-9a-f]{64}$")

class SessionCreateRequest(BaseModel):
    schema_id: Literal["talos.a2a.session_create_request"] = "talos.a2a.session_create_request"
    schema_version: Literal["v1"] = "v1"
    responder_id: PrincipalID
    ratchet_state_blob_b64u: Base64Url
    ratchet_state_digest: SHA256Hex
    expires_at: Optional[datetime] = None

class SessionAcceptRequest(BaseModel):
    schema_id: Literal["talos.a2a.session_accept_request"] = "talos.a2a.session_accept_request"
    schema_version: Literal["v1"] = "v1"
    ratchet_state_blob_b64u: Base64Url
    ratchet_state_digest: SHA256Hex

class SessionRotateRequest(BaseModel):
    schema_id: Literal["talos.a2a.session_rotate_request"] = "talos.a2a.session_rotate_request"
    schema_version: Literal["v1"] = "v1"
    ratchet_state_blob_b64u: Base64Url
    ratchet_state_digest: SHA256Hex

class EncryptedFrame(BaseModel):
    schema_id: Literal["talos.a2a.encrypted_frame"] = "talos.a2a.encrypted_frame"
    schema_version: Literal["v1"] = "v1"
    session_id: UUIDv7
    sender_id: PrincipalID
    sender_seq: int = Field(ge=0)
    header_b64u: Base64Url
    ciphertext_b64u: Base64Url
    frame_digest: SHA256Hex
    ciphertext_hash: SHA256Hex
    created_at: datetime

class FrameSendRequest(BaseModel):
    schema_id: Literal["talos.a2a.frame_send_request"] = "talos.a2a.frame_send_request"
    schema_version: Literal["v1"] = "v1"
    frame: EncryptedFrame

class GroupCreateRequest(BaseModel):
    schema_id: Literal["talos.a2a.group_create_request"] = "talos.a2a.group_create_request"
    schema_version: Literal["v1"] = "v1"
    name: Optional[str] = Field(None, min_length=1, max_length=255)

class GroupMemberAddRequest(BaseModel):
    schema_id: Literal["talos.a2a.group_member_add_request"] = "talos.a2a.group_member_add_request"
    schema_version: Literal["v1"] = "v1"
    member_id: PrincipalID
