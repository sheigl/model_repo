"""Configuration for the model deployment app.

Loads a YAML config file (default: config.yaml) that defines:
  - source repos on this server (the catalog sources of truth)
  - target machines and their per-category remote path mappings
  - HuggingFace settings

The config is intentionally simple and hand-editable; the Settings page in the
UI lets you add/remove targets and edit path mappings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(os.environ.get("MODEL_REPO_CONFIG", str(Path(__file__).parent / "config.yaml")))


@dataclass
class Source:
    """A source-of-truth repo on this server that gets cataloged."""
    name: str
    root: str          # absolute path to the repo root
    kind: str = "flat"  # "flat" (GGUF) or "comfyui"


@dataclass
class PathMapping:
    """Where a given category of files lands on a target machine."""
    remote_root: str   # base dir on the target, e.g. /mnt/1TB/AI/chat_models
    subdirs: dict = field(default_factory=dict)  # category -> relative subdir


@dataclass
class TargetMachine:
    name: str
    host: str          # hostname or IP reachable from this server (tailscale MagicDNS ok)
    user: str = "sheigl"
    ssh_key: str | None = None   # path to a private key, if not the default ~/.ssh/id_ed25519
    ntfs: bool = False           # target fs is NTFS -> drop -goP flags, case-sensitive warning
    categories: dict[str, PathMapping] = field(default_factory=dict)


@dataclass
class RegistryConfig:
    """The source-of-truth model hub (pluggable provider)."""
    enabled: bool = True
    provider: str = "huggingface"      # "huggingface" | "modelscope" | ...
    token_env: str = "HF_TOKEN"        # env var holding the auth token (if any)
    repo_root: str = "/mnt/4TB/AI/models"  # server-side cache dir
    options: dict = field(default_factory=dict)  # provider-specific options

    @property
    def default_repo_root(self) -> str:
        """Back-compat alias for the cache dir."""
        return self.repo_root


@dataclass
class Config:
    sources: list[Source] = field(default_factory=list)
    targets: dict[str, TargetMachine] = field(default_factory=dict)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    app_port: int = 8321
    bind_host: str = "0.0.0.0"

    @property
    def hf(self) -> RegistryConfig:
        """Back-compat alias for the source registry (`cfg.hf`)."""
        return self.registry

    # ---- loading / saving -------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        cfg = cls()
        p = Path(path or CONFIG_PATH)
        if not p.exists():
            return cfg  # empty config; UI will let the user populate it
        data = yaml.safe_load(p.read_text()) or {}
        _apply(cfg, data)
        return cfg

    def save(self, path: Path | None = None) -> None:
        p = Path(path or CONFIG_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sources": [asdict(s) for s in self.sources],
            "targets": {k: asdict(v) for k, v in self.targets.items()},
            "registry": asdict(self.registry),
            "app_port": self.app_port,
            "bind_host": self.bind_host,
        }
        p.write_text(yaml.safe_dump(data, default_flow_style=False))

    # ---- helpers ----------------------------------------------------------

    def target_names(self) -> list[str]:
        return list(self.targets.keys())


def _apply(cfg: Config, data: dict) -> None:
    cfg.app_port = int(data.get("app_port", 8321))
    cfg.bind_host = str(data.get("bind_host", "0.0.0.0"))

    for s in data.get("sources", []):
        cfg.sources.append(Source(**s))

    for name, t in data.get("targets", {}).items():
        cats = {}
        for cat, pm in (t.get("categories") or {}).items():
            cats[cat] = PathMapping(
                remote_root=pm["remote_root"],
                subdirs=pm.get("subdirs", {}),
            )
        cfg.targets[name] = TargetMachine(**{**t, "categories": cats})

    reg = data.get("registry") or data.get("hf") or {}
    cfg.registry = RegistryConfig(
        enabled=bool(reg.get("enabled", True)),
        provider=str(reg.get("provider", "huggingface")),
        token_env=str(reg.get("token_env", "HF_TOKEN")),
        repo_root=str(reg.get("repo_root", reg.get("default_repo_root", "/mnt/4TB/AI/models"))),
        options=dict(reg.get("options") or {}),
    )


# ---------------------------------------------------------------------------
# Default config: the known server repos + the two verified targets.
# These defaults are written on first run if no config.yaml exists, and can be
# edited freely from the Settings page.
# ---------------------------------------------------------------------------

def default_config() -> Config:
    cfg = Config()
    cfg.app_port = 8321
    cfg.bind_host = "0.0.0.0"

    # Source repos on this server (catalog sources of truth).
    cfg.sources = [
        Source(name="GGUF / LLM models", root="/mnt/4TB/AI/models", kind="flat"),
        Source(name="ComfyUI store", root="/mnt/4TB/AI/comfyui_models/models", kind="comfyui"),
    ]

    # ---- Target: ai.home (NTFS data drive) -------------------------------
    ai = TargetMachine(
        name="ai.home",
        host="ai.home",
        user="sheigl",
        ntfs=True,
    )
    ai.categories = {
        "llm": PathMapping(remote_root="/mnt/1TB/AI/chat_models"),
        "audio-gen": PathMapping(remote_root="/mnt/1TB/AI/chat_models"),
        "embedding": PathMapping(remote_root="/mnt/1TB/AI/chat_models/embedding"),
        "vision-projector": PathMapping(remote_root="/mnt/1TB/AI/chat_models"),
        "video-gen": PathMapping(remote_root="/mnt/1TB/AI/chat_models"),
        "image-gen": PathMapping(remote_root="/mnt/1TB/AI/ComfyUI/ComfyUI/models/checkpoints"),
        "audio-codec": PathMapping(remote_root="/mnt/1TB/AI/ComfyUI/ComfyUI/models/audio_encoders"),
        "vae": PathMapping(remote_root="/mnt/1TB/AI/ComfyUI/ComfyUI/models/vae"),
        "image-lora": PathMapping(remote_root="/mnt/1TB/AI/ComfyUI/ComfyUI/models/loras"),
        "upscaler": PathMapping(remote_root="/mnt/1TB/AI/ComfyUI/ComfyUI/models/upscale_models"),
        "detection-seg": PathMapping(remote_root="/mnt/1TB/AI/ComfyUI/ComfyUI/models/ultralytics"),
    }

    # ---- Target: desktop2.home -------------------------------------------
    d2 = TargetMachine(
        name="desktop2.home",
        host="desktop2.home",
        user="sheigl",
        ntfs=False,
    )
    d2.categories = {
        "llm": PathMapping(remote_root="/home/sheigl/AI/chat_models"),
        "audio-gen": PathMapping(remote_root="/home/sheigl/AI/chat_models"),
        "embedding": PathMapping(remote_root="/home/sheigl/AI/chat_models/embedding"),
        "vision-projector": PathMapping(remote_root="/home/sheigl/AI/chat_models"),
        "video-gen": PathMapping(remote_root="/home/sheigl/AI/chat_models"),
        "image-gen": PathMapping(remote_root="/home/sheigl/AI/stable-diffusion/checkpoints"),
        "audio-codec": PathMapping(remote_root="/home/sheigl/AI/sd_models/audio_encoders"),
        "vae": PathMapping(remote_root="/home/sheigl/AI/sd_models/vae"),
        "image-lora": PathMapping(remote_root="/home/sheigl/AI/sd_models/loras"),
        "upscaler": PathMapping(remote_root="/home/sheigl/AI/sd_models/upscale_models"),
    }

    cfg.targets = {"ai.home": ai, "desktop2.home": d2}
    return cfg


# ---------------------------------------------------------------------------
# Singleton config object used by the app. Loaded once at startup; if no
# config.yaml exists yet, a default is written so the UI has something to show.
# ---------------------------------------------------------------------------

def load_config(initialize: bool = False) -> Config:
    cfg = Config.load()
    if not cfg.sources and not cfg.targets:
        cfg = default_config()
        if initialize:
            try:
                cfg.save()
            except OSError:
                pass
    return cfg
