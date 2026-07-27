from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tdb.audit.logger import log_denial
from tdb.config import get_api_keys

bearer_scheme = HTTPBearer(auto_error=False)


def require_api_key(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
):
    if credentials is None or credentials.credentials not in get_api_keys():
        presented = credentials.credentials if credentials else ""
        log_denial(
            action="auth",
            reason="missing_api_key" if credentials is None else "invalid_api_key",
            key_hint=presented[:6] + "..." if presented else "",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return credentials.credentials
