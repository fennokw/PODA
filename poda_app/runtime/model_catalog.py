"""Curated, locally executable Ollama model families and conservative fit estimates.

Tags/sizes checked against Ollama's official library, October 2026.
No cloud variants, no invented quantization tags, no downloads here.
Apple Silicon GPU memory SHARES unified memory: never add its capacity to RAM.
"""
from __future__ import annotations

FAMILIES = [
 {"family":"Qwen 3.6 27B","role":"coding","notes":"Agentic coding, long-horizon tool use; vision capable",
  "variants":[("qwen3.6:27b",18.5,"Q4_K_M"),("qwen3.6:27b-q8_0",30,"Q8_0")]},
 {"family":"Qwen 3.6 35B MoE","role":"general","notes":"Long workflows; total weights matter despite sparse activation",
  "variants":[("qwen3.6:35b",23.5,"Q4_K_M"),("qwen3.6:35b-a3b-q8_0",39,"Q8_0")]},
 {"family":"Qwen 3.5 4B","role":"general","notes":"Compact vision and tool use",
  "variants":[("qwen3.5:4b",3.7,"default")]},
 {"family":"Qwen 3.5 9B","role":"general","notes":"Balanced local chat and vision",
  "variants":[("qwen3.5:9b",7.2,"default")]},
 {"family":"Gemma 4 12B","role":"vision","notes":"Multimodal image understanding and general reasoning",
  "variants":[("gemma4:12b",8.0,"default")]},
 {"family":"Gemma 4 26B","role":"vision","notes":"Larger local multimodal reasoning",
  "variants":[("gemma4:26b",19.0,"default")]},
 {"family":"Qwen3-VL 4B","role":"vision","notes":"Lightweight image reasoning",
  "variants":[("qwen3-vl:4b",3.3,"default")]},
 {"family":"Qwen3-VL 8B","role":"vision","notes":"Image, charts and screenshots",
  "variants":[("qwen3-vl:8b",6.2,"default")]},
 {"family":"Qwen3-Coder-Next","role":"coding","notes":"Agentic coding; large 80B total weights",
  "variants":[("qwen3-coder-next:q4_K_M",52.0,"Q4_K_M"),("qwen3-coder-next:q8_0",85.0,"Q8_0")]},
 {"family":"Devstral Small 2","role":"coding","notes":"Multi-file software engineering; vision",
  "variants":[("devstral-small-2:24b",15.0,"Q4_K_M")]},
 {"family":"GPT-OSS 20B","role":"general","notes":"Local reasoning and tools (not a cloud endpoint)",
  "variants":[("gpt-oss:20b",14.0,"default")]},
 {"family":"Llama 3.2 3B","role":"general","notes":"Very fast small on-device assistant",
  "variants":[("llama3.2:3b",2.0,"default")]},
]
LIBRARY_URL = "https://ollama.com/library/"


def assess(hw: dict, installed: list[str]) -> dict:
    mem = float(hw.get("unified_memory_gb") or hw.get("ram_total_gb") or 0)
    discrete = hw.get("gpu_memory_dedicated_gb")
    # macOS Apple Silicon memory is one pool; a nominal reported GPU allocation
    # is a limit within RAM, not additional capacity.
    if hw.get("memory_architecture") == "unified":
        total = mem
        bottleneck = "shared unified RAM/GPU"
    elif discrete:
        total = float(discrete)
        bottleneck = "dedicated VRAM"
    else:
        total = mem
        bottleneck = "system RAM (GPU acceleration not confirmed)"
    # OS/PODA/Ollama + conservative context/runtime overhead for 8K tokens.
    reserve = max(4.0, min(10.0, total * 0.22))
    usable = max(0, total - reserve - 1.8)
    disk_free = float(hw.get("disk_free_gb") or 0)
    out = []
    for family in FAMILIES:
        options = []
        for tag, weight_gb, quant in family["variants"]:
            # disk weights are a proxy for loaded weights; add overhead.
            estimated_working = round(weight_gb * 1.08 + 1.8,1)
            capacity_ok = total>0 and estimated_working <= (total-reserve)
            disk_ok = disk_free >= (weight_gb*1.12+1.0)
            status = "compatible estimate" if capacity_ok and disk_ok else (
                "not enough free disk" if capacity_ok else "not compatible for responsive local use")
            options.append({"tag":tag,"quant":quant,"disk_gb":weight_gb,
                            "estimated_resident_gb":estimated_working,
                            "memory_fit":capacity_ok,"disk_fit":disk_ok,
                            "status":status,"installed":tag in installed or
                            (quant=="default" and any(m.startswith(tag+":") for m in installed))})
        best = next((o for o in sorted(options,key=lambda v: -v["disk_gb"]) if o["memory_fit"] and o["disk_fit"]),None)
        out.append({**{k:v for k,v in family.items() if k!="variants"}, "variants":options,
                    "recommended_quant":best["quant"] if best else None,
                    "recommended_tag":best["tag"] if best else None,
                    "verdict":best["status"] if best else "not compatible on detected hardware"})
    return {"hardware":{"memory_architecture":hw.get("memory_architecture","unknown"),
                        "unified_memory_gb":hw.get("unified_memory_gb"),
                        "ram_total_gb":hw.get("ram_total_gb"),
                        "gpu_dedicated_vram_gb":hw.get("gpu_memory_dedicated_gb"),
                        "gpu_shared_with_ram":hw.get("memory_architecture")=="unified",
                        "disk_free_gb":disk_free,
                        "available_model_budget_gb":round(usable,1),
                        "bottleneck":bottleneck},
            "families":out,
            "disclaimer":"Conservative estimates, not measured tokens/s. Runtime KV cache, background apps, Metal offload and thermal limits affect performance. Download sizes and tags can change; confirm on Ollama before installing. GPU/RAM is NOT double-counted."}
