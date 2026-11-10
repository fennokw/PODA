"""Install the checksum-pinned browser assets used by local chat previews."""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import time
from urllib.request import urlopen

ASSETS = Path(__file__).resolve().parents[1] / "static" / "chat-vendor"
REGISTRY = "https://registry.npmjs.org/"
PACKAGES = (
    ("mermaid/-/mermaid-12.3.0.tgz",
     "4113TdXeLAsMruJ79OpFL5O8+LTFXhQqZ260rF3tgUmiNAtRm4Uume04I+65PRN6yZ69J4f8rVYWW8zRk65UYw==",
     {"dist/mermaid.min.js": "mermaid.js", "LICENSE": "mermaid-license.txt"}),
    ("katex/-/katex-0.19.1.tgz",
     "jG8uKntjSEcm2EpQOh0XturpPuuC9r3Sq5HLHdsFdWZT27E672SF19QEi+of0/YeOy15lDgRhKvr5614uRrEfA==",
     {"dist/katex.min.js": "katex.js", "dist/katex.min.css": "katex.css", "LICENSE": "katex-license.txt"}),
    ("markdown-it/-/markdown-it-15.0.3.tgz",
     "+Az7Ojz3iRGu8KYMvJ6xVhvrxP3eE+vEtJX75Ow88XBpcQNV/0lGX5ZynIIBftREo4DC2jsveAMDaeltmm8QvQ==",
     {"dist/browser/markdown-it.umd.min.js": "markdown-it.js", "LICENSE": "markdown-it-license.txt"}),
    ("@highlightjs/cdn-assets/-/cdn-assets-11.12.0.tgz",
     "KvOKXODaiFmId9xaq3xc5xCL66wVLUuOngDbO9B/kewbFTqdGbn2nJxNhN3H5R1cgDTVj6R8vH0zgiNDEGjpDw==",
     {"highlight.min.js": "highlight.js", "styles/github-dark.min.css": "highlight.css", "LICENSE": "highlight-license.txt"}),
)


def _download(path: str, digest: str) -> bytes:
    limit = 40 * 1024 * 1024
    for attempt in range(3):
        try:
            with urlopen(REGISTRY + path, timeout=30) as response:
                data = response.read(limit + 1)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    if len(data) > limit or hashlib.sha512(data).digest() != base64.b64decode(digest):
        raise ValueError(f"Checksum mismatch for {path}")
    return data


def install(target: Path = ASSETS) -> None:
    marker = target / "versions.txt"
    versions = "\n".join(path for path, _, _ in PACKAGES) + "\n"
    if (marker.is_file() and marker.read_text(encoding="utf-8") == versions
            and all((target / name).is_file() for _, _, files in PACKAGES for name in files.values())
            and (target / "fonts" / "KaTeX_Main-Regular.woff2").is_file()):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".chat-assets-", dir=target.parent) as directory:
        staged = Path(directory)
        for path, digest, files in PACKAGES:
            pending = dict(files)
            with tarfile.open(fileobj=BytesIO(_download(path, digest)), mode="r|gz") as archive:
                for member in archive:
                    relative = member.name.removeprefix("package/")
                    name = pending.pop(relative, None)
                    if path.startswith("katex/") and re.fullmatch(r"dist/fonts/KaTeX_[\w-]+\.woff2", relative):
                        name = relative.removeprefix("dist/")
                    if name is None:
                        continue
                    if not member.isfile():
                        raise ValueError(f"Unexpected archive entry: {member.name}")
                    destination = staged / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source:
                        destination.write_bytes(source.read())
            if pending:
                raise ValueError(f"Missing browser assets in {path}: {', '.join(pending)}")
        for source in staged.rglob("*"):
            if source.is_file():
                destination = target / source.relative_to(staged)
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
        marker.write_text(versions, encoding="utf-8")


if __name__ == "__main__":
    install(Path(sys.argv[1]) if len(sys.argv) > 1 else ASSETS)
