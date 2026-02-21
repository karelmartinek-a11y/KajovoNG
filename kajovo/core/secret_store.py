from __future__ import annotations

import os
from typing import Optional

SERVICE_NAME = "kajovo"


def _env_name(key: str) -> str:
    return f"KAJOVO_SECRET_{key.upper()}"


def set_secret(key: str, value: str) -> bool:
    value = value or ""
    try:
        import keyring  # type: ignore

        if value:
            keyring.set_password(SERVICE_NAME, key, value)
        else:
            try:
                keyring.delete_password(SERVICE_NAME, key)
            except Exception:
                pass
        return True
    except Exception:
        env_name = _env_name(key)
        if value:
            os.environ[env_name] = value
        else:
            os.environ.pop(env_name, None)
        return False


def get_secret(key: str) -> Optional[str]:
    try:
        import keyring  # type: ignore

        value = keyring.get_password(SERVICE_NAME, key)
        if value:
            return value
    except Exception:
        pass
    value = os.environ.get(_env_name(key), "")
    return value or None
