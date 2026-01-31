from fastapi import HTTPException
from typing import Optional, Dict, Any

def raise_talos_error(
    code: str, 
    status_code: int, 
    message: str, 
    details: Optional[Dict[str, Any]] = None
) -> None:
    """Raise a standardized Talos HTTPException.
    
    Args:
        code: Error code (AUTH_INVALID, RBAC_DENIED, etc.)
        status_code: HTTP Status Code (401, 403, etc.)
        message: Human readable message
        details: Optional extra details
    """
    error_body: Dict[str, Any] = {
        "code": code,
        "message": message
    }
    if details:
        error_body["details"] = details
        
    raise HTTPException(status_code=status_code, detail={"error": error_body})
