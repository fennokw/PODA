"""Repository-scoped GitHub access for browser and desktop PODA."""
from __future__ import annotations

import base64
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

from ...runtime.db import get_setting
from ...security import egress, keychain

KEYCHAIN_SERVICE = "PODA.Public.GitHub"
KEYCHAIN_ACCOUNT = "repository"
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
SHA = re.compile(r"[0-9a-f]{40}")
BRANCH = re.compile(r"poda/[A-Za-z0-9][A-Za-z0-9_-]{0,70}")
BASE = "https://api.github.com/repos/"


def valid_repo(repo: str) -> str:
    if not REPO.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/")):
        raise ValueError("Enter a GitHub repository as owner/name.")
    return repo


def status() -> dict:
    return {"repo": get_setting("github_repo"), "write": get_setting("github_write") == "true"}


def _request(repo, path="", method="GET", payload=None, *, token=None):
    repo = valid_repo(repo)
    if token is None:
        if status()["repo"].casefold() != repo.casefold():
            raise PermissionError("Select this repository in Files & access before using it.")
        token = keychain.keychain_get(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        if not token:
            raise PermissionError("The saved GitHub token needs to be reconnected.")
        if method != "GET" and not status()["write"]:
            raise PermissionError("GitHub write access is disabled for this repository.")
    response = egress.guarded_request("github", method, BASE + repo + path, headers={
        "Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
    }, json_body=payload, timeout=18, purpose="PODA repository access")
    if not response.ok:
        raise RuntimeError(f"GitHub returned HTTP {response.status_code}. Check repository access and token permissions.")
    return response.json()


def _ok(data, verification):
    return {"ok": True, "result": data, "verification": verification}


def _path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or any(
        part in {"", ".", ".."} or part.lower() == ".git" for part in value.split("/")
    ):
        raise ValueError("Use a normalized path inside the repository.")
    return quote(value, safe="/")


def map_tree(repo, query="", ref=""):
    project = _request(repo)
    ref = ref or project["default_branch"]
    commit = _request(repo, "/commits/" + quote(ref, safe=""))["sha"]
    tree = _request(repo, "/git/trees/" + commit + "?recursive=1")
    words = set(re.findall(r"[a-z0-9]{3,}", query.lower()))
    paths = [e["path"] for e in tree["tree"] if e["type"] == "blob" and e.get("size", 0) < 500_000]
    files = sorted(paths, key=lambda p: (-len(words.intersection(re.findall(r"[a-z0-9]{3,}", p.lower()))),
                                         p.count("/"), p.lower()))
    return _ok({"repo": project["full_name"], "ref": ref, "commit": commit, "files": files[:60],
                "candidates": len(paths), "truncated": len(files) > 60 or tree.get("truncated", False)},
               "Read the GitHub tree at the reported commit.")


def read_file(repo, path, ref):
    if not SHA.fullmatch(ref):
        raise ValueError("Use a pinned GitHub commit SHA from github_repo_map.")
    result = _request(repo, "/contents/" + _path(path) + "?" + urlencode({"ref": ref}))
    if not isinstance(result, dict) or result.get("type") != "file" or result.get("encoding") != "base64":
        raise ValueError("The selected repository object is not a readable text file.")
    if result.get("size", 0) > 160_000:
        raise ValueError("Choose a source file under 160 kB.")
    content = base64.b64decode(result["content"]).decode("utf-8")
    return _ok({"path": path, "ref": ref, "blob": result["sha"], "content": content,
                "url": result["html_url"]}, "Read a GitHub source file at the pinned revision.")


def pull_context(repo, number):
    path = "/pulls/" + str(number)
    data = _request(repo, path)
    files = _request(repo, path + "/files?per_page=25")
    return _ok({"title": data["title"], "url": data["html_url"], "state": data["state"],
                "body": (data.get("body") or "")[:1800], "head": data["head"]["sha"],
                "changed_files": data["changed_files"],
                "files": [e["filename"] for e in files]}, "Read the GitHub pull request and up to 25 changed paths.")


def publish(repo, base, expected_base, branch, title, changes):
    if not SHA.fullmatch(expected_base) or not BRANCH.fullmatch(branch) or branch == base:
        raise ValueError("Use a pinned base commit and a new branch named poda/<short-name>.")
    if not 1 <= len(changes) <= 12:
        raise ValueError("Include 1–12 changed files in one reviewable commit.")
    entries = []
    seen = set()
    size = 0
    # Revalidate every nested entry, including proposals replayed after a restart.
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "content"}:
            raise ValueError("Every change needs a path and complete replacement text.")
        path, body = change["path"], change["content"]
        if not isinstance(path, str) or not isinstance(body, str):
            raise ValueError("The file path and contents must be text.")
        _path(path)
        if path in seen or len(path) > 500 or "\x00" in body:
            raise ValueError("Use distinct, bounded text file paths.")
        seen.add(path)
        size += len(body.encode())
        entries.append({"path": path, "type": "blob", "mode": "100644", "content": body})
    if size > 450_000:
        raise ValueError("A patch can contain up to 450 kB of replacement text.")
    commit = _request(repo, "/commits/" + quote(base, safe=""))
    if commit["sha"] != expected_base:
        raise RuntimeError("The GitHub base branch changed. Refresh the repo map and review a new patch.")
    base_tree = commit["commit"]["tree"]["sha"]
    previous = _request(repo, "/git/trees/" + base_tree + "?recursive=1")
    if previous.get("truncated"):
        raise RuntimeError("GitHub truncated the base tree. Use a local checkout for this change.")
    modes = {item["path"]: item for item in previous["tree"]}
    for entry in entries:
        original = modes.get(entry["path"])
        if original:
            if original["mode"] not in {"100644", "100755"}:
                raise ValueError("This tool edits regular source files only.")
            entry["mode"] = original["mode"]
    tree = _request(repo, "/git/trees", "POST", {"base_tree": base_tree, "tree": entries})
    if tree["sha"] == base_tree:
        raise ValueError("The proposed files match the GitHub base.")
    head = _request(repo, "/git/commits", "POST", {"message": title, "tree": tree["sha"], "parents": [expected_base]})
    try:
        _request(repo, "/git/refs", "POST", {"ref": "refs/heads/" + branch, "sha": head["sha"]})
    except RuntimeError:
        existing = _request(repo, "/git/ref/heads/" + quote(branch, safe="/"))["object"]["sha"]
        prior = _request(repo, "/git/commits/" + existing)
        if prior["tree"]["sha"] != tree["sha"] or [p["sha"] for p in prior["parents"]] != [expected_base]:
            raise RuntimeError("That work branch already points to other changes. Use a new branch.")
        head = prior
    owner = repo.split("/")[0]
    prior_pulls = _request(repo, "/pulls?" + urlencode({"state": "open", "head": owner + ":" + branch, "base": base}))
    pull = prior_pulls[0] if prior_pulls else _request(repo, "/pulls", "POST", {
        "title": title, "head": branch, "base": base, "draft": True,
        "body": "Prepared in PODA. Review the diff and run the required checks before merging.",
    })
    verified = _request(repo, "/pulls/" + str(pull["number"]))
    if verified["head"]["sha"] != head["sha"] or verified["base"]["ref"] != base:
        raise RuntimeError("GitHub returned a different PR revision than the published branch.")
    return _ok({"commit": head["sha"], "pr": verified["html_url"], "draft": verified["draft"]},
               "Read back the GitHub pull request and checked its head and base.")
