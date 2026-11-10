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
GIB = 1024**3
DOWNLOAD_RESERVE = 128 * 1024**2


def quantization_label(code: str) -> str:
    """Describe nominal precision. Mixed GGUFs also store some tensors at higher precision."""
    code = code.upper()
    floating = {"F16": "16-bit float", "FP16": "16-bit float", "BF16": "16-bit bfloat",
                "F32": "32-bit float", "FP32": "32-bit float"}
    if code in floating:
        return floating[code]
    if code.startswith("MXFP4"):
        return "4-bit float"
    match = re.match(r"(?:UD-)?I?Q([1-8])(?:_|$)", code)
    return f"{match[1]}-bit" if match else "Bit level unknown"


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
    extra_shards: tuple[Asset, ...] = ()
    revision: str = "main"

    @property
    def weight_files(self) -> tuple[Asset, ...]:
        return (self.weights, *self.extra_shards)

    @property
    def weight_size(self) -> int:
        return sum(asset.size for asset in self.weight_files)

    @property
    def size(self) -> int:
        return self.weight_size + (self.projector.size if self.projector else 0)


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


def _published(label: str, role: str, repository: str, revision: str, variants: tuple, *,
               projector: Asset | None = None, thinking: bool = False, tools: bool = False) -> list[Model]:
    family = repository.split("/")[-1].removesuffix("-GGUF").lower()
    models = []
    for quant, files in variants:
        weights = (files,) if isinstance(files, Asset) else files
        models.append(Model(f"{family}-{quant.lower()}", label, role, quant, repository,
                            weights[0], projector, thinking, tools, weights[1:], revision))
    return models


# Publisher revisions, sizes, and SHA-256 digests from the Hugging Face file manifests.
_CATALOG = [
    *_published("Llama 3.2 3B", "general", "bartowski/Llama-3.2-3B-Instruct-GGUF", "5ab33fa94d1d04e903623ae72c95d1696f09f9e8", (
        ("Q4_K_M", Asset("Llama-3.2-3B-Instruct-Q4_K_M.gguf", 2019377696, "6c1a2b41161032677be168d354123594c0e6e67d2b9227c84f296ad037c728ff")),
        ("Q5_K_M", Asset("Llama-3.2-3B-Instruct-Q5_K_M.gguf", 2322154016, "0b94ccd04d908304cec5246a3d942b64417a423bc5c6d47c73bc557e590b5194")),
        ("Q6_K", Asset("Llama-3.2-3B-Instruct-Q6_K.gguf", 2643853856, "1771887c15fc3d327cfee6fd593553b2126e88834bf48eae50e709d3f70dd998")),
        ("Q8_0", Asset("Llama-3.2-3B-Instruct-Q8_0.gguf", 3421899296, "b5607b5090a8280063fff2d706bb3408ca6542341b06aab39c3eca0a28575921")),
    )),
    *_published("Qwen3.5 2B", "general", "unsloth/Qwen3.5-2B-GGUF", "f6d5376be1edb4d416d56da11e5397a961aca8ae", (
        ("Q3_K_M", Asset("Qwen3.5-2B-Q3_K_M.gguf", 1107149056, "8b049f98461020b7e15797e13413fc63b9b500ffa883c74618b618ecbc2bfccf")),
        ("Q4_K_M", Asset("Qwen3.5-2B-Q4_K_M.gguf", 1280835840, "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223")),
        ("Q5_K_M", Asset("Qwen3.5-2B-Q5_K_M.gguf", 1435238656, "1885b3a9195f8cc09da9a7a7a75afdc1e8d5cbf9fc4a499c3961dddea37098ac")),
        ("Q6_K", Asset("Qwen3.5-2B-Q6_K.gguf", 1574961408, "fc90339420b4298887aafb307a4291c55440b730133bbffe6ba9630503dcb548")),
        ("Q8_0", Asset("Qwen3.5-2B-Q8_0.gguf", 2012012800, "1b04acba824817554f4ce23639bc8495ff70453b8fcb047900c731521021f2c1")),
        ("BF16", Asset("Qwen3.5-2B-BF16.gguf", 3775709216, "dc11be2ca4519954a3e4b013ef59497b5522209987d67891851113eda89619f1")),
    ), projector=Asset("mmproj-F16.gguf", 668227264, "7035e9cb8d7c6a9681d07eef9a364783e86ea4cd73faab2eabb4f43a101830c7"), thinking=True, tools=True),
    *_published("Qwen3.5 4B", "general", "unsloth/Qwen3.5-4B-GGUF", "e87f176479d0855a907a41277aca2f8ee7a09523", (
        ("Q3_K_M", Asset("Qwen3.5-4B-Q3_K_M.gguf", 2293388448, "d6981ab4d77ba712b48ef69d69042d75b5e39b9dce5fb5a5b054fd08e06afb95")),
        ("Q4_K_M", Asset("Qwen3.5-4B-Q4_K_M.gguf", 2740937888, "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4")),
        ("Q5_K_M", Asset("Qwen3.5-4B-Q5_K_M.gguf", 3143656608, "8814232b85594dcd46c50e5b8b29324a7efe9e746edbe8a3d1df3d3fce7aad39")),
        ("Q6_K", Asset("Qwen3.5-4B-Q6_K.gguf", 3525956768, "fdedd781c9ce676ab66b018ca247ff78e8a33c98098a822c1e2d5075e7718f66")),
        ("Q8_0", Asset("Qwen3.5-4B-Q8_0.gguf", 4482403488, "10cc391b403021dd11c614679d2fd92f611c3681d29e29651b717316965d61e1")),
        ("BF16", Asset("Qwen3.5-4B-BF16.gguf", 8424393632, "9e6e2841a75f503ccb330831832fd7861266e187e0dbf149a954219ccb8c197a")),
    ), projector=Asset("mmproj-F16.gguf", 672423616, "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864"), thinking=True, tools=True),
    *_published("Qwen3.5 9B", "general", "unsloth/Qwen3.5-9B-GGUF", "3885219b6810b007914f3a7950a8d1b469d598a5", (
        ("Q3_K_M", Asset("Qwen3.5-9B-Q3_K_M.gguf", 4673643744, "8fed90306e4f019e2bf35f3766470b7bc59ea1a9dae00f5ceb20b43cb5514393")),
        ("Q4_K_M", Asset("Qwen3.5-9B-Q4_K_M.gguf", 5680522464, "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8")),
        ("Q5_K_M", Asset("Qwen3.5-9B-Q5_K_M.gguf", 6577841376, "dc2a39aef291f91a9116ad214058da0d86eb648743a124bd8c333787c4b9c91c")),
        ("Q6_K", Asset("Qwen3.5-9B-Q6_K.gguf", 7458301152, "91898433cf5ce0a8f45516a4cc3e9343b6e01d052d01f684309098c66a326c59")),
        ("Q8_0", Asset("Qwen3.5-9B-Q8_0.gguf", 9527502048, "809626574d0cb43d4becfa56169980da2bb448f2299270f7be443cb89d0a6ae4")),
        ("BF16", Asset("Qwen3.5-9B-BF16.gguf", 17920697312, "daebe40eeea7057c1cdf35ac56d13f507d8bf12171bbb7a6b6b0d3f05439159a")),
    ), projector=Asset("mmproj-F16.gguf", 918166080, "f70dc3509053962b0d0d3ee8a7eacebf5d60aa560cad78254ae8698516ae029f"), thinking=True, tools=True),
    *_published("Qwen3.6 35B MoE", "general", "unsloth/Qwen3.6-35B-A3B-GGUF", "a483e9e6cbd595906af30beda3187c2663a1118c", (
        ("UD-Q2_K_XL", Asset("Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf", 12290628576, "96b9c0af5c77a4ecaabe3983175112b5ece763261c1ece12b2494b692a70dad7")),
        ("UD-Q3_K_M", Asset("Qwen3.6-35B-A3B-UD-Q3_K_M.gguf", 16600710112, "1b715841683f960bd9a49f008181bd910ee169b78d4cf465b6fde7f4d929ff99")),
        ("UD-Q4_K_M", Asset("Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", 22134528992, "ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61")),
        ("UD-Q5_K_M", Asset("Qwen3.6-35B-A3B-UD-Q5_K_M.gguf", 26456194016, "c13ce26253ea334df472bd8fbd2d6da66d8a41195c17f6fcbf44c4d20ece0932")),
        ("UD-Q6_K", Asset("Qwen3.6-35B-A3B-UD-Q6_K.gguf", 29308320736, "4fe53b148b46f9b88830e2a3055c5b15c3a4d1e3ddc9a1384a108d8b9d59f043")),
        ("Q8_0", Asset("Qwen3.6-35B-A3B-Q8_0.gguf", 36903140320, "d1a395809f65a43a13ad119eb4e7acdef1ac6d68120f39902c8ab96e72794a59")),
    ), projector=Asset("mmproj-F16.gguf", 899283680, "8971ee4f331ff0a4c609374f32984b3d4e6dc086c0aa35f1d637fad1829e887f"), thinking=True, tools=True),
    *_published("GPT-OSS 20B", "general", "ggml-org/gpt-oss-20b-GGUF", "15dcfc7e7c608c6161507211ed43c5b590801b4b", (
        ("MXFP4", Asset("gpt-oss-20b-MXFP4.gguf", 12109566624, "27cd6c432c7672cb812a92f611cf3ba7bbc35928262bb1e1253ff4ee6ae35901")),
    ), thinking=True, tools=True),
    *_published("Qwen3.6 27B", "coding", "unsloth/Qwen3.6-27B-GGUF", "82d411acf4a06cfb8d9b073a5211bf410bfc29bf", (
        ("Q3_K_M", Asset("Qwen3.6-27B-Q3_K_M.gguf", 13586217184, "bdfa99d488b501f5ca711f35c930f005319a064eabcaae23290571a25c499eeb")),
        ("Q4_K_M", Asset("Qwen3.6-27B-Q4_K_M.gguf", 16817244384, "5ed60d0af4650a854b1755bd392f9aef4872643dc25a254bc68043fa638392a0")),
        ("Q5_K_M", Asset("Qwen3.6-27B-Q5_K_M.gguf", 19509790944, "cfecab168156269f25d5ffe9e13cf2a401ca2f43a9693fa00bcd1625316ccbde")),
        ("Q6_K", Asset("Qwen3.6-27B-Q6_K.gguf", 22523238624, "ec1805fe87e6519c461c1ed2d179865464a875ed241032ead65a354f979cfe14")),
        ("Q8_0", Asset("Qwen3.6-27B-Q8_0.gguf", 28595763424, "f93f517f38e696d35a1a7df2c0e3155a64f4c4dcd662107a146ae263f7fb14ce")),
        ("BF16", (
            Asset("BF16/Qwen3.6-27B-BF16-00001-of-00002.gguf", 50004497824, "897fb005062c86d71d86b4aabc702af46e2c2c81643bad3a77ce2bb5745b73f1"),
            Asset("BF16/Qwen3.6-27B-BF16-00002-of-00002.gguf", 3803783936, "75fd5ed66db232ef1526e7d18ac3e057af6fda8fb7d4802a4541e1972e5bd25e"),
        )),
    ), projector=Asset("mmproj-F16.gguf", 927607360, "eacf610d1ee4bd5ed0197a0777dd8f4fceb8eefa27009067c7d496cb68fbde45"), thinking=True, tools=True),
    *_published("Devstral Small 2 24B", "coding", "unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF", "6e458b8add42681bfd023de5eab93637694aaf82", (
        ("Q2_K", Asset("Devstral-Small-2-24B-Instruct-2512-Q2_K.gguf", 8890862752, "408a61e7dcc0eb7fda7d70979395f974fe17310b4f38f977c95d9b98095fe1aa")),
        ("Q3_K_M", Asset("Devstral-Small-2-24B-Instruct-2512-Q3_K_M.gguf", 11474619552, "53466aa3e486e6f0068c2593d12aeb15fb079c1dfe443ab922c468c39761bd16")),
        ("Q4_K_M", Asset("Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf", 14334446752, "d14ba9edee1bb4c4996a726deb81e49ae81800a3216f0774634238c380aee496")),
        ("Q5_K_M", Asset("Devstral-Small-2-24B-Instruct-2512-Q5_K_M.gguf", 16764521632, "2da6ca6c4ae387aa7f3f2f4a67bb3e1ca570ce0c69c21e26b8695e75172443b0")),
        ("Q6_K", Asset("Devstral-Small-2-24B-Instruct-2512-Q6_K.gguf", 19346476192, "b04b3e19730d7a1e19530f40b947b69c028b090cb7c58c1515cf1fc2ece5f821")),
        ("Q8_0", Asset("Devstral-Small-2-24B-Instruct-2512-Q8_0.gguf", 25055317152, "0760502e9228234f6cfa843f8870b8fc91c46a13664cf766c639229cccc80866")),
        ("BF16", Asset("Devstral-Small-2-24B-Instruct-2512-BF16.gguf", 47154056032, "6a86365cc26ec2e5ba1434aa85da15a7de28eb92015447623a780f1f86ab1d1b")),
    ), projector=Asset("mmproj-F16.gguf", 878054048, "236bb4f0500620164917f26749cd402485b7d749034ea817c6a2d65253b126b9"), tools=True),
    *_published("Qwen3 Coder 30B MoE", "coding", "ggml-org/Qwen3-Coder-30B-A3B-Instruct-GGUF", "0ca51122012fe4a20dc82988ee58780b008ce4e3", (
        ("Q4_K_M", Asset("Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf", 17988419552, "f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335")),
        ("Q8_0", Asset("Qwen3-Coder-30B-A3B-Instruct-Q8_0.gguf", 32483934176, "3963e7f49d7ed2b18e3486574582552d6431cf30122e78b14c28fcb593451f42")),
        ("BF16", Asset("Qwen3-Coder-30B-A3B-Instruct-BF16.gguf", 61095804896, "c3b2a4ac5830c12858610d6d19dee4dd24db3e904436f2ce860a0bb54399041b")),
    ), tools=True),
    *_published("Qwen3 Coder Next", "coding", "Qwen/Qwen3-Coder-Next-GGUF", "b82fb7382639d97b38fa7672e526c760c2fb358e", (
        ("Q4_K_M", (
            Asset("Qwen3-Coder-Next-Q4_K_M/Qwen3-Coder-Next-Q4_K_M-00001-of-00004.gguf", 15524827040, "6bcfc9f9c37901eeb92172e2ab871224dab36a453d263bcb2547f737409534da"),
            Asset("Qwen3-Coder-Next-Q4_K_M/Qwen3-Coder-Next-Q4_K_M-00002-of-00004.gguf", 14872168352, "817def0691ee9d08bf3dc4444be7aed29c9e52091e8fa9d97901ce7e7f6f01d3"),
            Asset("Qwen3-Coder-Next-Q4_K_M/Qwen3-Coder-Next-Q4_K_M-00003-of-00004.gguf", 14503294496, "23aa634d47dca9b4ca3ea249384e6f01951b24c83cdc076f37f6f43d6c99883f"),
            Asset("Qwen3-Coder-Next-Q4_K_M/Qwen3-Coder-Next-Q4_K_M-00004-of-00004.gguf", 3510702144, "249c768cc5f130dc731567d6edcbdacc48e14dec9e02c5dbe2b2185d2c5bdb2b"),
        )),
        ("Q5_K_M", (
            Asset("Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00001-of-00004.gguf", 17813589920, "d68162877891c4ba309c8601a69721ecfc7be2091ad99e8175a078ae59decdd3"),
            Asset("Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00002-of-00004.gguf", 17479501728, "3da1a3fda1061009126eb48c7098221653ab63e0a266d3b6d04f5eac3ae9aa74"),
            Asset("Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00003-of-00004.gguf", 17226413600, "df60c7a55c6e118d4984a829b74df6b9e863dcea749ee7dc25ab5e72913624e1"),
            Asset("Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00004-of-00004.gguf", 4190867520, "777ac63a6f94272934996b1cfd7e5619c6804e8580ec4e00311d2a465e298c6a"),
        )),
        ("Q6_K", (
            Asset("Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf", 20245400480, "96f5a82248f330b16f96b8764890807394a2c9ff15dd929739e84406a82f1c08"),
            Asset("Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00002-of-00004.gguf", 20249793440, "8aba362152a27d594b72de6116229a1cbacf10016ab032fcbeeabf3e496cf50e"),
            Asset("Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00003-of-00004.gguf", 20119727648, "3533e483d4a8f0c71d596b1a7f9b84a653c9034bdd04a7665d5cede7fcab48f6"),
            Asset("Qwen3-Coder-Next-Q6_K/Qwen3-Coder-Next-Q6_K-00004-of-00004.gguf", 4913543232, "cd44084ec6bb816667821ec2ceba024a10910c01f7d53b6c4b7fdb4c868f8c1d"),
        )),
        ("Q8_0", (
            Asset("Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00001-of-00004.gguf", 26190321568, "30b7554fc0c846a5dc3ecf585884c77471f73e3da698a8ba4fabd8e7868c6533"),
            Asset("Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00002-of-00004.gguf", 26212935584, "3f96379de5a5c4655cb378710ea571d5e9cc96f260120a44a6477198efcdc27d"),
            Asset("Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00003-of-00004.gguf", 26047157792, "5dd1ce07eaae95ee430331dc9c6f3120ff88e4211ad3a0cceeaa963f25328504"),
            Asset("Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00004-of-00004.gguf", 6361641024, "76730702c630bf76305139165cb85421858604030851dfd64fe96a5e67cda99d"),
        )),
    ), tools=True),
    *_published("Gemma 4 E4B", "vision", "unsloth/gemma-4-E4B-it-GGUF", "bfc15c382204943c3a8fff0c750b94ae2364d7a3", (
        ("Q3_K_M", Asset("gemma-4-E4B-it-Q3_K_M.gguf", 4058137728, "0980898d0e7d9c75cea05141bf4010393312101ef6cf5b31f8197e0de18655f4")),
        ("Q4_K_M", Asset("gemma-4-E4B-it-Q4_K_M.gguf", 4977171584, "85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87")),
        ("Q5_K_M", Asset("gemma-4-E4B-it-Q5_K_M.gguf", 5481798784, "a5d6e634db151368d2caa4270fd32ab604d96c2d1291950586989f46d8e17d36")),
        ("Q6_K", Asset("gemma-4-E4B-it-Q6_K.gguf", 7074929792, "fd83f3ef44d22e00ff0f638753dde9a66a83e8ccf79c11024f47d383c489531b")),
        ("Q8_0", Asset("gemma-4-E4B-it-Q8_0.gguf", 8192953472, "f8854aa4480df62585a279e7ca0a881554fc18a41c59c4f62642d16a2ae47012")),
        ("BF16", Asset("gemma-4-E4B-it-BF16.gguf", 15053097856, "38e0dba6818d18e2b41062c7b3b9083dcfddc44767078c415ff7e13661911ba5")),
    ), projector=Asset("mmproj-F16.gguf", 990372672, "ddf46c21d7078e95338cfc22306b19b276a29a5ad089023449dd54d4b6170a51"), thinking=True, tools=True),
    *_published("Gemma 4 12B", "vision", "unsloth/gemma-4-12B-it-GGUF", "fc034cfff751157913579611efad8462ac1be606", (
        ("Q3_K_M", Asset("gemma-4-12b-it-Q3_K_M.gguf", 5693872960, "dbf58f4c413b82b27106867eb42f7087e4172a172f6f201e2e11598a6582e504")),
        ("Q4_K_M", Asset("gemma-4-12b-it-Q4_K_M.gguf", 7121861440, "0a270ec9fe6b34f4a0d33992b6135117b484ebc4766ab76b51d4ae8c457e4c42")),
        ("Q5_K_M", Asset("gemma-4-12b-it-Q5_K_M.gguf", 8413576000, "32c554c7d1338a23837ae39b3db482213a4a642f96df3b2619f516a2375d16cd")),
        ("Q6_K", Asset("gemma-4-12b-it-Q6_K.gguf", 9786022720, "6f394336500bb5409bd68c31aa9d75097b5e6104f6cf30c82a09a7b64df41faf")),
        ("Q8_0", Asset("gemma-4-12b-it-Q8_0.gguf", 12669647680, "f20e7ff1be28c283eeeb18fc895733791c56a5851d5cd3fe9691b7f7d12afa72")),
        ("BF16", Asset("gemma-4-12b-it-BF16.gguf", 23832066656, "5a5eefea73350705c6753105b725a51301d653e8d6646173db29a7e8da8e6efd")),
    ), projector=Asset("mmproj-F16.gguf", 175115840, "91f086971e56d7a7d8d39e271873fccdb49541bd259d6e02c401a4f1cb7a219e"), thinking=True, tools=True),
    *_published("Gemma 4 26B MoE", "vision", "unsloth/gemma-4-26B-A4B-it-GGUF", "c099eb48e663fd284577b04978a94ffccb261841", (
        ("UD-Q2_K_XL", Asset("gemma-4-26B-A4B-it-UD-Q2_K_XL.gguf", 10546934240, "2a1d26dfe6ea00a467940a5728316af6edb366bbdba950d65b85d232392fb658")),
        ("UD-Q3_K_M", Asset("gemma-4-26B-A4B-it-UD-Q3_K_M.gguf", 12728497888, "811882f6df81c8ea204a5d382f196d6bd601245091786912d4c70df41cdfa052")),
        ("UD-Q4_K_M", Asset("gemma-4-26B-A4B-it-UD-Q4_K_M.gguf", 16947541728, "f2c28b3dc4776931ac6f879e11f203dec637ea0f14267a86ec8f6165f63f293f")),
        ("UD-Q5_K_M", Asset("gemma-4-26B-A4B-it-UD-Q5_K_M.gguf", 21150365408, "769d386a69d43782321c1bad04d41d29a2e84b2c06e6a277cd99fd6265ec0e80")),
        ("UD-Q6_K", Asset("gemma-4-26B-A4B-it-UD-Q6_K.gguf", 23172478688, "76bf3eee49cca5b076b34a4b575ecd505dabc470512b46f1207b4f9da08385f0")),
        ("Q8_0", Asset("gemma-4-26B-A4B-it-Q8_0.gguf", 26859861728, "5f7cbd0f4564e84342fc34321a09acb54b1a3da9215124e5bf444baa6dda152c")),
    ), projector=Asset("mmproj-F16.gguf", 1193058784, "418a6d8723067cd712235facbbc5cba6c8fbbd413fc1292d2aace5a027d5a42f"), thinking=True, tools=True),
    *_published("Qwen3-VL 4B", "vision", "Qwen/Qwen3-VL-4B-Instruct-GGUF", "1cd86afb9a95c410a6038ab3b40d8b578c892266", (
        ("Q4_K_M", Asset("Qwen3VL-4B-Instruct-Q4_K_M.gguf", 2497281664, "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a")),
        ("Q8_0", Asset("Qwen3VL-4B-Instruct-Q8_0.gguf", 4280406144, "054721f478bc5fa6beffb7f38eae575d45298f88cbb8d2f83ef675a727863eb1")),
        ("F16", Asset("Qwen3VL-4B-Instruct-F16.gguf", 8051286144, "4a6fb1ed053e162887bfc372bd7ffaaa9b8e4049ac6e9b9398ae7277162ec761")),
    ), projector=Asset("mmproj-Qwen3VL-4B-Instruct-F16.gguf", 836180256, "256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331"), tools=True),
    *_published("Qwen3-VL 8B", "vision", "Qwen/Qwen3-VL-8B-Instruct-GGUF", "f982a07559d4a2f6c8744d840bf6fccab30eea96", (
        ("Q4_K_M", Asset("Qwen3VL-8B-Instruct-Q4_K_M.gguf", 5027784800, "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2")),
        ("Q8_0", Asset("Qwen3VL-8B-Instruct-Q8_0.gguf", 8709519456, "0d264b3941185d00a74f75c4245521dae088ff1efc90ab8d1754e83f5844adb0")),
        ("F16", Asset("Qwen3VL-8B-Instruct-F16.gguf", 16388044896, "2715c1a097f1943fb88ad59c7c0e9288a7a50c84d60cf18c6227bd9a40520972")),
    ), projector=Asset("mmproj-Qwen3VL-8B-Instruct-F16.gguf", 1159029824, "ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38"), tools=True),
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


def _weights(directory: Path, metadata: dict) -> Path | None:
    if "shards" not in metadata:
        weights = directory / "model.gguf"
        return weights if _gguf(weights) and weights.stat().st_size == metadata["size"] else None
    shards = metadata["shards"]
    if not isinstance(shards, list) or len(shards) < 2:
        return None
    total = 0
    for index, shard in enumerate(shards, 1):
        filename = shard["name"]
        suffix = f"-{index:05d}-of-{len(shards):05d}.gguf"
        if Path(filename).name != filename or not filename.endswith(suffix):
            return None
        if index == 1:
            stem = filename.removesuffix(suffix)
        if filename != stem + suffix:
            return None
        path = directory / filename
        if not _gguf(path) or path.stat().st_size != shard["size"]:
            return None
        total += shard["size"]
    return directory / shards[0]["name"] if total == metadata["size"] else None


def installed(name: str) -> dict | None:
    try:
        directory = _path(name)
        if directory.is_symlink():
            return None
        metadata = json.loads((directory / "model.json").read_text(encoding="utf-8"))
        weights = _weights(directory, metadata)
        projector = directory / "projector.gguf"
        if weights is None:
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
    return {key: value for key, value in model.items() if key not in {"path", "projector", "shards"}} | {
        "quant_label": quantization_label(model.get("quant", ""))}


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
    if shutil.disk_usage(root()).free < item.size + DOWNLOAD_RESERVE:
        raise ValueError("There is insufficient disk space for this model.")
    with tempfile.TemporaryDirectory(prefix=".download-", dir=root()) as temporary:
        staged = Path(temporary)
        base = f"https://huggingface.co/{item.repository}/resolve/{item.revision}/"
        shards = [{"name": Path(asset.name).name, "size": asset.size} for asset in item.weight_files] if item.extra_shards else []
        downloads = list(zip(item.weight_files, (shard["name"] for shard in shards))) if shards else [(item.weights, "model.gguf")]
        if item.projector:
            downloads.append((item.projector, "projector.gguf"))
        offset = 0
        for asset, filename in downloads:
            report = (lambda count, start=offset: progress(start + count)) if progress else None
            download(base + quote(asset.name), staged / filename, asset.digest, asset.size, report)
            offset += asset.size
        metadata = {
            "label": item.label, "role": item.role, "quant": item.quant, "repository": item.repository,
            "size": item.weight_size, "projector_size": item.projector.size if item.projector else 0,
            "vision": bool(item.projector), "thinking": item.thinking, "tools": item.tools,
        }
        if shards:
            metadata["shards"] = shards
        return _publish(_path(name), staged, metadata)


def import_model(path: str, projector: str | None = None, *, thinking: bool = False, tools: bool = False) -> dict:
    source = Path(path).expanduser()
    projection = Path(projector).expanduser() if projector else None
    for file in (source, projection) if projection else (source,):
        if not file.is_absolute() or file.suffix.lower() != ".gguf" or not _gguf(file):
            raise ValueError("Choose an absolute path to a regular GGUF file.")
        if re.search(r"-\d{5}-of-\d{5}\.gguf$", file.name, re.I):
            raise ValueError("Import a single-file GGUF. Merge split GGUF files before importing them.")
    name = "import-" + uuid.uuid4().hex
    quant = re.search(r"(?:^|[.-])((?:UD-)?(?:I?Q|TQ)\d[\w_]*|MXFP4(?:_MOE)?|BF16|FP?16|FP?32)$", source.stem, re.I)
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
    disk = hardware.get("disk_free_bytes")
    if disk is None and hardware.get("disk_free_gb") is not None:
        disk = hardware["disk_free_gb"] * GIB

    def estimate(item: Model) -> dict:
        # File size includes every expert and the vision projector. Allow for
        # buffers and a typical context, then leave OS headroom in shared RAM.
        needed = item.size / GIB * 1.05 + 2
        budget = "unified memory" if unified else "system RAM"
        if vram and needed <= vram * .9:
            status, detail = "fits", "Fits estimated GPU memory budget"
        elif vram and needed <= vram:
            status, detail = "tight", "Close to the GPU memory limit"
        elif not ram:
            status, detail = "unknown", "Memory capacity could not be measured"
        elif needed <= ram - max(3, min(8, ram * .16)):
            if vram:
                status, detail = "tight", "May need CPU or split offload"
            else:
                status, detail = "fits", f"Fits estimated {budget} budget"
        elif needed <= ram - max(1, min(3, ram * .09)):
            status, detail = "tight", f"Close to the {budget} limit"
        else:
            status, detail = "exceeds", f"Over the estimated {budget} budget"
        return {"fit": detail, "fit_status": status, "estimated_memory_gb": round(needed, 1),
                "disk_fit": None if disk is None else disk >= item.size + DOWNLOAD_RESERVE}

    local = {item["id"] for item in inventory()}
    return [{"id": item.id, "label": item.label, "role": item.role, "quant": item.quant,
             "quant_label": quantization_label(item.quant), "vision": bool(item.projector),
             "bytes": item.size, "installed": item.id in local, "repository": item.repository, **estimate(item)}
            for item in _CATALOG]
