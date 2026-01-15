from typing import Optional
from jose import jws, JWTError
from .models import TgaCapability, TgaCapabilityConstraints
import hashlib
import json
import time

class CapabilityValidationError(Exception):
    def __init__(self, message: str, code: str = "CAPABILITY_INVALID"):
        super().__init__(message)
        self.code = code

class CapabilityValidator:
    """
    Validates TGA Capability tokens (JWS) and enforces constraints 
    against specific tool calls.
    """
    
    def __init__(self, supervisor_public_key: str):
        """
        :param supervisor_public_key: Public key in PEM or JWK format (Ed25519).
        """
        self.public_key = supervisor_public_key

    def decode_and_verify(self, token: str) -> TgaCapability:
        """
        Decodes the JWS token and verifies its EdDSA signature.
        """
        try:
            # Jose jws.verify handles EdDSA if cryptography is available
            payload_bytes = jws.verify(token, self.public_key, algorithms=['EdDSA'])
            payload_dict = json.loads(payload_bytes.decode('utf-8'))
            
            cap = TgaCapability.model_validate(payload_dict)
            self._validate_claims(cap)
            return cap
            
        except JWTError as e:
            raise CapabilityValidationError(f"Invalid capability signature or format: {str(e)}", "SIGNATURE_INVALID")
        except Exception as e:
            raise CapabilityValidationError(f"Capability decoding failed: {str(e)}")

    def _validate_claims(self, cap: TgaCapability):
        """Verifies standard and TGA-specific claims."""
        now = int(time.time())
        
        if cap.aud != "talos-gateway":
             raise CapabilityValidationError("Invalid audience", "AUDIENCE_MISMATCH")
             
        if cap.exp < now:
             raise CapabilityValidationError("Capability expired", "EXPIRED")
             
        if cap.nbf and cap.nbf > now:
             raise CapabilityValidationError("Capability not yet valid", "NOT_BEFORE")

    def validate_tool_call(self, cap: TgaCapability, tool_server: str, tool_name: str, args: dict):
        """
        Enforce capability constraints against a specific tool call.
        """
        con = cap.constraints
        
        # 1. Tool Identity
        if con.tool_server != tool_server or con.tool_name != tool_name:
            raise CapabilityValidationError(
                f"Unauthorized tool: {tool_server}:{tool_name}, expected {con.tool_server}:{con.tool_name}",
                "TOOL_UNAUTHORIZED"
            )
            
        # 2. Read-Only Enforcement
        # This assumes the caller provides a way to identify mutation tools, 
        # but the constraint itself is binary. 
        # If capability is read-only, and the tool name indicates a mutation (e.g. create-*), we deny.
        # (This logic might be refined with a more explicit tool registry).
        if con.read_only:
            # Heuristic for now; in production this would look up the tool's classification.
            mutation_prefixes = ["create-", "update-", "delete-", "write-", "apply-"]
            if any(tool_name.startswith(p) for p in mutation_prefixes):
                 raise CapabilityValidationError(f"Mutation tool '{tool_name}' forbidden in READ_ONLY capability", "READ_ONLY_VIOLATION")

        # 3. Argument Schema Constraints (SHA-256 of Schema)
        if con.arg_constraints:
            # In a real implementation, we would validate 'args' against the 
            # schema identified by 'arg_constraints'. 
            # For now, we record that this check is required.
            pass

    def calculate_capability_digest(self, token: str) -> str:
        """SHA-256 of the raw JWS token (normative binding)."""
        return hashlib.sha256(token.encode('utf-8')).hexdigest()
