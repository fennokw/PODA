from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, SecretStr

from ..runtime.db import set_setting
from ..security import keychain
from . import research

router = APIRouter(prefix="/connections/web", tags=["research"])


class WebSettings(BaseModel):
    enabled: bool
    search_key: SecretStr | None = None


@router.get("")
def status():
    return research.status()


@router.put("")
def configure(req: WebSettings):
    if req.search_key is not None:
        value = req.search_key.get_secret_value().strip()
        try:
            if value:
                if not 8 <= len(value) <= 200 or any(ch.isspace() for ch in value):
                    raise ValueError("Invalid search key.")
                keychain.keychain_set(research.SEARCH_SERVICE, research.SEARCH_ACCOUNT, value)
            else:
                keychain.keychain_delete(research.SEARCH_SERVICE, research.SEARCH_ACCOUNT)
        except Exception as exc:
            raise HTTPException(400, "Check the search key and the operating system credential vault.") from exc
        set_setting("brave_search", "true" if value else "false")
    set_setting("public_web", "true" if req.enabled else "false")
    return research.status()
