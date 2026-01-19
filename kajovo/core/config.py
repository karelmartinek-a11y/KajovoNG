from __future__ import annotations

import os, json
from dataclasses import dataclass, asdict, field
from typing import List, Optional
from .utils import ensure_dir

DEFAULT_SETTINGS_FILE = "kajovo_settings.json"
DEFAULT_DENY_EXTENSIONS = [
    ".exe",
    ".dll",
    ".zip",
    ".7z",
    ".rar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".db",
    ".sqlite",
    ".pkl",
    ".pt",
    ".onnx",
]
DEFAULT_DENY_GLOBS = [
    "**/.git/**",
    "**/node_modules/**",
    "**/venv/**",
    "**/.venv/**",
    "**/LOG/**",
]

@dataclass
class RetryPolicy:
    max_attempts: int = 6
    base_delay_s: float = 0.8
    max_delay_s: float = 20.0
    jitter_s: float = 0.25
    circuit_breaker_failures: int = 6
    circuit_breaker_cooldown_s: float = 20.0

@dataclass
class LoggingPolicy:
    max_total_mb: int = 2048
    max_runs: int = 200
    encrypt_logs: bool = False
    mask_secrets: bool = False

@dataclass
class PricingPolicy:
    # Default to public OpenAI pricing JSON (GitHub mirror); fallback model fetch is used if unavailable.
    source_url: str = "https://raw.githubusercontent.com/openai/openai-python/refs/heads/main/pricing.json"
    cache_ttl_hours: int = 72
    auto_refresh_on_start: bool = True

@dataclass
class SecurityPolicy:
    allow_upload_sensitive: bool = False
    deny_extensions_in: Optional[List[str]] = field(
        default_factory=lambda: list(DEFAULT_DENY_EXTENSIONS)
    )
    allow_extensions_in: Optional[List[str]] = None
    deny_globs_in: Optional[List[str]] = field(
        default_factory=lambda: list(DEFAULT_DENY_GLOBS)
    )
    allow_globs_in: Optional[List[str]] = None


@dataclass
class SMTPSettings:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    use_ssl: bool = False
    from_email: str = ""
    to_email: str = ""


@dataclass
class SSHSettings:
    user: str = ""
    host: str = ""
    key: str = ""
    password: str = ""


@dataclass
class AppSettings:
    db_path: str = "kajovo.sqlite"
    log_dir: str = "LOG"
    cache_dir: str = "cache"
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    logging: LoggingPolicy = field(default_factory=LoggingPolicy)
    pricing: PricingPolicy = field(default_factory=PricingPolicy)
    security: SecurityPolicy = field(default_factory=SecurityPolicy)
    smtp: SMTPSettings = field(default_factory=SMTPSettings)
    ssh: SSHSettings = field(default_factory=SSHSettings)
    batch_poll_interval_s: float = 4.0
    batch_timeout_s: float = 60.0 * 60.0
    default_model: str = ""
    default_temperature: float = 0.2
    dry_run_modify: bool = False

def load_settings(path: str = DEFAULT_SETTINGS_FILE) -> AppSettings:
    if not os.path.exists(path):
        return AppSettings()
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    def merge(obj, data):
        for k, v in data.items():
            if not hasattr(obj, k):
                continue
            cur = getattr(obj, k)
            if hasattr(cur, "__dict__") and isinstance(v, dict):
                merge(cur, v)
            else:
                setattr(obj, k, v)

    s = AppSettings()
    merge(s, raw)
    return s

def save_settings(s: AppSettings, path: str = DEFAULT_SETTINGS_FILE) -> None:
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(s), f, ensure_ascii=False, indent=2, default=str)
