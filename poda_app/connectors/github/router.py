"""Local configuration for the repository-scoped GitHub connector."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, SecretStr

from ...runtime.db import set_setting
from ...security import keychain
from . import service

router = APIRouter(prefix="/connections/github", tags=["github"])


class Connection(BaseModel):
    repo: str
    token: SecretStr
    write: bool = False


@router.get("")
def status():
    return service.status()


@router.put("")
def connect(req: Connection):
    token = req.token.get_secret_value().strip()
    if not 8 <= len(token) <= 300 or any(ch.isspace() for ch in token):
        raise HTTPException(422, "Enter a valid GitHub access token.")
    try:
        repo = service.valid_repo(req.repo.strip())
        data = service._request(repo, token=token)
        keychain.keychain_set(service.KEYCHAIN_SERVICE, service.KEYCHAIN_ACCOUNT, token)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith("GitHub returned HTTP"):
            raise HTTPException(400, str(exc)) from exc
        raise HTTPException(400, "GitHub could not verify the repository or save the token in the operating system vault.") from exc
    set_setting("github_repo", data["full_name"])
    set_setting("github_write", "true" if req.write else "false")
    return service.status()


@router.delete("")
def disconnect():
    set_setting("github_repo", "")
    set_setting("github_write", "false")
    keychain.keychain_delete(service.KEYCHAIN_SERVICE, service.KEYCHAIN_ACCOUNT)
    return service.status()
