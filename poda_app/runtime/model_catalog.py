"""Verified GGUF downloads and PODA's local model inventory."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote
import uuid

from . import config
from .llama_install import download

CACHE_TYPES = ("f16", "bf16", "f32", "q8_0", "q5_0", "q5_1", "q4_0", "q4_1", "iq4_nl")


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    digest: str


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    role: str
    quant: str
    repository: str
    weights: Asset
    projector: Asset | None = None
    thinking: bool = False
    tools: bool = False

    @property
    def size(self) -> int:
        return self.weights.size + (self.projector.size if self.projector else 0)


def _qwen(size: str, variants: tuple) -> list[Model]:
    family = f"Qwen3-{size}"
    return [Model(f"{family.lower()}-{quant.lower()}", f"Qwen3 {size}", "general", quant, f"Qwen/{family}-GGUF",
                  Asset(f"{family}-{quant}.gguf", count, sha), thinking=True, tools=True)
            for quant, count, sha in variants]


def _coder(size: str, variants: tuple) -> list[Model]:
    family = f"Qwen2.5-Coder-{size}-Instruct"
    return [Model(f"{family.lower()}-{quant.lower()}", f"Qwen2.5 Coder {size}", "coding", quant, f"Qwen/{family}-GGUF",
                  Asset(f"{family.lower()}-{quant.lower()}.gguf", count, sha))
            for quant, count, sha in variants]


_CATALOG = [
    *_qwen("4B", (
        ("Q4_K_M", 2497280256, "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"),
        ("Q5_K_M", 2889513184, "aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f"),
        ("Q6_K", 3306260704, "8a08533841623c2a689763b9318d8e27b3e052ad5b38015d4be4c04fa96f68a3"),
        ("Q8_0", 4280404704, "8c2f07f26af9747e41988551106f149b03eb9b5cb6df636027b6bf6278473300"),
    )),
    *_qwen("8B", (
        ("Q4_K_M", 5027783488, "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"),
        ("Q5_K_M", 5851112224, "068bae163faa96ad48032daf4e071a6a28fe67d8dcc95367609c2ff165e52738"),
        ("Q6_K", 6725899040, "cb042ccd76795a8830d6be6bd4165245847cc68e41797b13bd61aed4c2cfbce6"),
        ("Q8_0", 8709518112, "408b955510e196121c1c375201744783b5c9a43c7956d73fc78df54c66e883d6"),
    )),
    *_qwen("14B", (
        ("Q4_K_M", 9001752960, "500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0"),
        ("Q5_K_M", 10514569568, "e7c9aba1129ca2936be9eca01419d9f86af40e08caa01230d5574b34d08e3e31"),
        ("Q6_K", 12121937248, "ec1f1d1421d7636a23e17645cac6fda39ee4b5f1f11e42eb09238089705ef699"),
        ("Q8_0", 15698533728, "a0dfe649137410b7d82f06a209240508e218f32f5b6fd81b69d6932160cfcd9d"),
    )),
    *_coder("7B", (
        ("Q2_K", 3015940032, "6ce2630974b0ef631e2b29064cd10a9cb16b278d001961d72bfd86987392c8d9"),
        ("Q3_K_M", 3808391104, "ff5c64615cf8a44651d208e9d8da1f753feefc21605e6c1ee67957aa77257c3c"),
        ("Q4_K_M", 4683073536, "509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c"),
        ("Q5_K_M", 5444831232, "586844eac4d6d6321689f0192c8aa8e69cd8625974a5cc2d925b1a03366e4d16"),
        ("Q6_K", 6254198784, "46291ddea1bfb608fe63d9a1907eea6918bda87a7626593edc4bf97c5fd73f9d"),
        ("Q8_0", 8098525184, "b36a4e1c3ddf2ba6fd5501b926128e5fc6430881caaff07e9629bd18f06f685f"),
    )),
    *_coder("14B", (
        ("Q3_K_M", 7339203968, "93b69ebf2883dcd4d8fa0ebd4b4589ae0f3eedc721ac391a46d36d90762f4640"),
        ("Q4_K_M", 8988110272, "c1e659736d89ac1065fb495330fb824d94001974a4bfa78e7270e43476a8d940"),
        ("Q5_K_M", 10508873152, "98ab25e0132e3f1e6d3554e1b64de2b5021908819b740d9c208430117e49a775"),
        ("Q6_K", 12124683712, "302a079369ad8b66c8e8ec1bfa62d109c64d8015e9bfd52d9c6cf4c6c9f36b5f"),
    )),
    *(Model(f"gemma-3-4b-it-{quant.lower()}", "Gemma 3 4B Vision", "vision", quant, "ggml-org/gemma-3-4b-it-GGUF",
            Asset(f"gemma-3-4b-it-{quant}.gguf", size, sha),
            Asset("mmproj-model-f16.gguf", 851251104, "8c0fb064b019a6972856aaae2c7e4792858af3ca4561be2dbf649123ba6c40cb"))
      for quant, size, sha in (
          ("Q4_K_M", 2489757856, "882e8d2db44dc554fb0ea5077cb7e4bc49e7342a1f0da57901c0802ea21a0863"),
          ("Q8_0", 4130226336, "97b06383df48336e7d2f9b56b6ce545e0fa476407a62c0bd081b53447a58e644"),
      )),
    Model("smolvlm-500m-instruct-q8_0", "SmolVLM 500M Vision", "vision", "Q8_0",
          "ggml-org/SmolVLM-500M-Instruct-GGUF",
          Asset("SmolVLM-500M-Instruct-Q8_0.gguf", 436806912, "9d4612de6a42214499e301494a3ecc2be0abdd9de44e663bda63f1152fad1bf4"),
          Asset("mmproj-SmolVLM-500M-Instruct-Q8_0.gguf", 108783360, "d1eb8b6b23979205fdf63703ed10f788131a3f812c7b1f72e0119d5d81295150")),
    Model(config.EMBED_MODEL, "Nomic Embed Text v1.5", "embedding", "F16", "nomic-ai/nomic-embed-text-v1.5-GGUF",
          Asset("nomic-embed-text-v1.5.f16.gguf", 274290560, "f7af6f66802f4df86eda10fe9bbcfc75c39562bed48ef6ace719a251cf1c2fdb")),
]
CATALOG = {item.id: item for item in _CATALOG}


def root() -> Path:
    return config.DATA_DIR / "models"


def _path(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,125}", name) or ".." in name:
        raise ValueError("Invalid model ID.")
    return root() / name


def _gguf(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as source:
        return source.read(4) == b"GGUF"


def installed(name: str) -> dict | None:
    try:
        directory = _path(name)
        if directory.is_symlink():
            return None
        metadata = json.loads((directory / "model.json").read_text(encoding="utf-8"))
        weights = directory / "model.gguf"
        projector = directory / "projector.gguf"
        if not _gguf(weights) or metadata["size"] != weights.stat().st_size:
            return None
        if metadata.get("vision") and (not _gguf(projector) or metadata["projector_size"] != projector.stat().st_size):
            return None
        return {**metadata, "id": name, "path": weights, "projector": projector if metadata.get("vision") else None}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def inventory() -> list[dict]:
    if not root().is_dir():
        return []
    return [item for directory in sorted(root().iterdir()) if not directory.name.startswith(".")
            if (item := installed(directory.name))]


def summary(model: dict) -> dict:
    return {key: value for key, value in model.items() if key not in {"path", "projector"}}


def _publish(target: Path, staged: Path, metadata: dict) -> dict:
    (staged / "model.json").write_text(json.dumps(metadata), encoding="utf-8")
    staged.rename(target)
    return installed(target.name)


def install_model(name: str, progress=None) -> dict:
    item = CATALOG.get(name)
    if not item:
        raise ValueError("Choose a GGUF from the PODA catalog.")
    if current := installed(name):
        return current
    root().mkdir(mode=0o700, parents=True, exist_ok=True)
    if shutil.disk_usage(root()).free < item.size + 128 * 1024**2:
        raise ValueError("There is insufficient disk space for this model.")
    with tempfile.TemporaryDirectory(prefix=".download-", dir=root()) as temporary:
        staged = Path(temporary)
        base = f"https://huggingface.co/{item.repository}/resolve/main/"
        download(base + quote(item.weights.name), staged / "model.gguf", item.weights.digest, item.weights.size, progress)
        if item.projector:
            report = (lambda count: progress(item.weights.size + count)) if progress else None
            download(base + quote(item.projector.name), staged / "projector.gguf", item.projector.digest,
                     item.projector.size, report)
        return _publish(_path(name), staged, {
            "label": item.label, "role": item.role, "quant": item.quant, "repository": item.repository,
            "size": item.weights.size, "projector_size": item.projector.size if item.projector else 0,
            "vision": bool(item.projector), "thinking": item.thinking, "tools": item.tools,
        })


def import_model(path: str, projector: str | None = None, *, thinking: bool = False, tools: bool = False) -> dict:
    source = Path(path).expanduser()
    projection = Path(projector).expanduser() if projector else None
    for file in (source, projection) if projection else (source,):
        if not file.is_absolute() or file.suffix.lower() != ".gguf" or not _gguf(file):
            raise ValueError("Choose an absolute path to a regular GGUF file.")
        if re.search(r"-\d{5}-of-\d{5}\.gguf$", file.name, re.I):
            raise ValueError("Import a single-file GGUF. Merge split GGUF files before importing them.")
    name = "import-" + uuid.uuid4().hex
    quant = re.search(r"(?:^|[.-])((?:I?Q|TQ)\d[\w_]*|BF16|F16|F32)$", source.stem, re.I)
    root().mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=root()) as temporary:
        staged = Path(temporary)
        shutil.copyfile(source, staged / "model.gguf")
        if projection:
            shutil.copyfile(projection, staged / "projector.gguf")
        return _publish(_path(name), staged, {
            "label": source.stem, "role": "vision" if projection else "general",
            "quant": quant[1].upper() if quant else "Imported GGUF",
            "size": (staged / "model.gguf").stat().st_size,
            "projector_size": (staged / "projector.gguf").stat().st_size if projection else 0,
            "vision": bool(projection), "thinking": thinking, "tools": tools,
        })


def catalog(hardware: dict | None = None) -> list[dict]:
    hardware = hardware or {}
    unified = hardware.get("unified_memory_gb")
    ram = unified or hardware.get("ram_total_gb")
    vram = None if unified else hardware.get("gpu_memory_dedicated_gb")
    disk = hardware.get("disk_free_gb")

    def estimate(item: Model) -> str:
        weights = item.size / 1024**3
        if disk is not None and weights > disk:
            return "Insufficient disk space"
        if unified and weights + 2 <= max(0, unified - 4):
            return "Fits estimated unified memory budget"
        if vram and weights + 2 <= vram:
            return "Fits estimated GPU memory budget"
        if ram and weights + 2 <= max(0, ram - 4):
            return "May use CPU or split offload"
        return "Tight or unknown memory fit"

    local = {item["id"] for item in inventory()}
    return [{"id": item.id, "label": item.label, "role": item.role, "quant": item.quant,
             "bytes": item.size, "installed": item.id in local, "repository": item.repository, "fit": estimate(item)}
            for item in _CATALOG]
