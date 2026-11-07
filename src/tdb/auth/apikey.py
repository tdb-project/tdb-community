import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tdb.audit.logger import log_denial
from tdb.config import get_api_keys

bearer_scheme = HTTPBearer(auto_error=False)


def is_valid_api_key(token: str) -> bool:
    return any(hmac.compare_digest(token, key) for key in get_api_keys())


def bearer_token(authorization: str) -> str:
    """The credentials of a `Bearer` Authorization header, as HTTPBearer reads it."""
    scheme, _, credentials = authorization.partition(" ")
    return credentials.strip() if scheme.lower() == "bearer" else ""


def require_api_key(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
):
    if credentials is None or not is_valid_api_key(credentials.credentials):
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
