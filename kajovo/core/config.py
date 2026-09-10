from __future__ import annotations

import os, json
import math
from dataclasses import dataclass, asdict, field, fields, is_dataclass
from typing import List, Optional
from .utils import ensure_dir, atomic_write_text
from .secret_store import get_secret, set_secret

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
    # Adresa zdroje ceníku pro ruční i automatické načtení.
    source_url: str = "https://openai.com/api/pricing/"
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
    pin_required: bool = False


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
    response_timeout_s: float = 300.0
    default_model: str = ""
    default_temperature: float = 0.2
    dry_run_modify: bool = False

def load_settings(path: str = DEFAULT_SETTINGS_FILE) -> AppSettings:
    raw = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("Nastavení musí být JSON objekt.")

    def merge(obj, data):
        allowed = {f.name for f in fields(obj)}
        for k, v in data.items():
            if k not in allowed:
                continue
            cur = getattr(obj, k)
            if is_dataclass(cur):
                if not isinstance(v, dict):
                    raise ValueError(f"Nastavení {k} musí být objekt.")
                merge(cur, v)
            else:
                optional_list = k in ("deny_extensions_in", "allow_extensions_in", "deny_globs_in", "allow_globs_in")
                if cur is not None and not (optional_list and v is None) and type(v) is not type(cur) and not (type(cur) is float and type(v) is int):
                    raise ValueError(f"Nesprávný typ nastavení {k}.")
                if (isinstance(cur, list) or k.startswith(("allow_", "deny_")) and k != "allow_upload_sensitive") and v is not None:
                    if not isinstance(v, list) or not all(isinstance(item, str) for item in v):
                        raise ValueError(f"Nastavení {k} musí být seznam textů.")
                setattr(obj, k, v)

    s = AppSettings()
    merge(s, raw)
    numeric_positive = [s.retry.max_attempts, s.retry.circuit_breaker_failures,
                        s.batch_poll_interval_s, s.batch_timeout_s, s.response_timeout_s, s.smtp.port,
                        s.logging.max_total_mb, s.logging.max_runs]
    numeric_nonnegative = [s.retry.base_delay_s, s.retry.max_delay_s, s.retry.jitter_s,
                           s.retry.circuit_breaker_cooldown_s, s.pricing.cache_ttl_hours]
    if any(not math.isfinite(v) or v <= 0 for v in numeric_positive) or any(
        not math.isfinite(v) or v < 0 for v in numeric_nonnegative
    ) or not 0 <= s.default_temperature <= 2 or s.smtp.port > 65535:
        raise ValueError("Číselné nastavení je mimo povolený rozsah.")
    # Hesla načtená z JSON se přesouvají do keyringu nebo prostředí.
    migration_results = []
    if s.smtp.password:
        migration_results.append(set_secret("smtp_password", s.smtp.password))
    if s.ssh.password:
        migration_results.append(set_secret("ssh_password", s.ssh.password))
    s.smtp.password = get_secret("smtp_password") or ""
    s.ssh.password = get_secret("ssh_password") or ""
    if migration_results and all(migration_results):
        save_settings(s, path)
    return s

def save_settings(s: AppSettings, path: str = DEFAULT_SETTINGS_FILE) -> None:
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    set_secret("smtp_password", s.smtp.password or "")
    set_secret("ssh_password", s.ssh.password or "")
    payload = asdict(s)
    # Hesla se do JSON neukládají.
    payload.setdefault("smtp", {})["password"] = ""
    payload.setdefault("ssh", {})["password"] = ""
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
