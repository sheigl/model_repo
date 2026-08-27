"""Model categorization + quantization detection.

Ported from the user's audit script (model_report/report.py). Provides:
  - QUANTS / FORMATS lists for quantization detection
  - extract_quant() to pull a quant out of a filename
  - categorize(path) -> category string, based on directory context + filename

Categories match what the UI uses for filtering and per-target path mapping.
"""

from __future__ import annotations

import os
import re

# Quantization strings, longest-first so e.g. Q6_K_XL beats Q6_K.
QUANTS = [
    "IQ2_XXS", "IQ3_XXS", "Q1_0", "Q2_K_XL", "Q2_K", "Q3_K_XL", "Q3_K_S", "Q3_K_M",
    "Q4_0", "Q4_1", "Q4_K_M", "Q4_K_S", "Q4_K_XL", "Q4_K_P", "Q5_0", "Q5_1",
    "Q5_K_M", "Q5_K_S", "Q5_K_XL", "Q5_K_P", "Q6_K", "Q6_K_L", "Q6_K_XL", "Q6_K_P",
    "Q8_0", "Q8_K", "Q8_K_L", "Q8_K_XL", "Q8_K_P", "F16", "BF16", "F32", "FP8",
    "FP16", "fp8", "fp16", "bf16", "int8", "int4", "nvfp4", "q8_0", "q4_k_m",
    "q4_0", "q5_k_m", "q6_k", "fp8_scaled", "fp8mixed", "fp8_e4m3fn",
    "fp8_e4m3fn_scaled", "fp16_e4m3fn", "Q4_K", "Q5_K", "Q6_K", "Q8_K",
]
QUANTS = sorted(set(QUANTS), key=len, reverse=True)

FORMATS = ["BF16", "F16", "F32", "Q8_0", "Q8_K", "FP16", "FP8", "nvfp4", "Q4_0", "int8", "int4"]


def extract_quant(s: str) -> str:
    """Return the first quantization string found in *s*, else ''."""
    for q in QUANTS:
        if q in s:
            return q
    return ""


def categorize_repo(model_name: str, local_names: list[str]) -> str:
    """Infer a deploy category for a model hub repo (source of truth).

    Used to pick a target's destination path mapping when deploying. A repo that
    ships an mmproj is a vision model; otherwise fall back to filename heuristics
    on the base model name.
    """
    if any(n.lower().startswith("mmproj") for n in local_names):
        return "vision-projector"
    return categorize(f"/cache/{model_name}.gguf")


def categorize(path: str) -> str:
    """Assign a category to a model file based on directory context + filename.

    Ported from report.py's categorize(). Works on absolute paths so it can be
    used for both the server repo and (via remote path inspection) targets.
    """
    low = path.lower()
    name = os.path.basename(path).lower()

    # Vision projectors (mmproj) anywhere
    if "mmproj" in name:
        return "vision-projector"

    # OpenVINO IR
    if "/openvino/" in low or low.endswith("/openvino") or "/openvino" in low:
        return "openvino"

    # diffusiongemma raw checkpoints (unresolved builds)
    if "diffusiongemma" in name:
        return "unknown"

    # --- ComfyUI stores (both comfyui/ and comfyui_models/) ---
    if "/comfyui/models/" in low or "/comfyui_models/models/" in low:
        if "/text_encoders" in low:
            return "video-gen" if ("qwen3vl" in name and "minimax" in name) else "image-text-encoder"
        if "/checkpoints" in low:
            if "hunyuan3d" in name:
                return "3d"
            if "ace_step" in name or "acestep" in name:
                return "audio-gen"
            if "ltx" in name:
                return "video-gen"
            return "image-gen"
        if "/diffusion_models" in low:
            if "acestep" in name or "ace_step" in name:
                return "audio-gen"
            if any(v in name for v in ("wan", "ltx", "seedvr", "minimax", "qwen3vl", "hunyuan")):
                return "video-gen"
            return "image-gen"
        if "/unet" in low:
            return "video-gen" if ("wan" in name or "ltx" in name) else "image-gen"
        if "/clip" in low:
            return "video-gen" if "qwen3vl" in name else "image-text-encoder"
        if "/vae_approx" in low or "/vae" in low:
            return "audio-codec" if "minimax_h3_audio" in name else "vae"
        if "/loras" in low or "/embeddings" in low or "/lora" in low:
            return "image-lora"
        if "/upscale_models" in low or "/latent_upscale_models" in low or "/model_patches" in low:
            return "upscaler"
        if "/ultralytics" in low:
            return "detection-seg"
        # model-family dirs at the comfyui_models/models/ root
        if "/heartmula" in low or "/heartcodec" in low:
            return "audio-gen"
        if "/seedvr" in low:
            return "video-gen"
        return "unknown"

    # --- stable-diffusion store ---
    if "/stable-diffusion" in low:
        if "/vae" in low:
            return "vae"
        if "qwen3-8b" in name:
            return "llm"
        if "anima" in name or "ltx" in name:
            return "video-gen"
        return "image-gen"

    # --- audio stores ---
    if "/audio/" in low:
        return "audio-gen"

    # --- chat_models (this PC + desktop2) ---
    if "/chat_models" in low:
        if "/embedding" in low:
            return "embedding"
        if "/openvino" in low:
            return "openvino"
        if "lance" in name:
            return "video-gen"
        if any(k in name for k in ("embed", "rerank")):
            return "embedding"
        return "llm"

    # --- server models/ root ---
    if "/embedding" in low:
        return "embedding"
    if "/audio" in low:
        return "audio-gen"
    if "heartmula" in low or "heartcodec" in low:
        return "audio-gen"
    if "lance" in name:
        return "video-gen"
    if "/krea/" in low:
        if "lora" in low:
            return "image-lora"
        if "/vae" in low:
            return "vae"
        if "/text_encoder" in low:
            return "unknown"
        if "/transformer" in low:
            return "image-gen"
        return "image-gen"
    if any(k in low for k in ("/mtp/", "/dflash/", "/eagle/", "/gemma-4", "/deepseek-ai",
                              "/qwen/", "/mtg-llama", "/unsloth/", "/uncensored/", "/bonsai",
                              "/diffusiongemma")):
        return "llm"
    # fall back by filename
    if name.endswith(".gguf") and any(k in name for k in ("embed", "rerank")):
        return "embedding"
    if name.endswith((".gguf", ".safetensors", ".pt", ".pth")):
        return "llm"
    return "unknown"
