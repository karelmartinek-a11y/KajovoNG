from __future__ import annotations

import os
from typing import Optional

SERVICE_NAME = "kajovo"


class APIKeyStoreError(RuntimeError):
    """Chyba úložiště bez přihlašovacích údajů v chybové zprávě."""


def _read_persisted_api_key() -> Optional[str]:
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, "OPENAI_API_KEY")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise APIKeyStoreError("Nelze přečíst uložený API klíč z uživatelského registru Windows.") from exc
    if kind != winreg.REG_SZ or not isinstance(value, str):
        raise APIKeyStoreError("Uložený API klíč má neplatný typ záznamu v registru Windows.")
    return value


def load_api_key() -> str:
    """Uložená hodnota včetně výslovného smazání má přednost před prostředím rodiče."""
    stored = _read_persisted_api_key()
    value = stored if stored is not None else os.environ.get("OPENAI_API_KEY", "")
    os.environ["OPENAI_API_KEY"] = value
    return value


def persist_api_key(value: str) -> bool:
    """Zapíše a ověří trvalou hodnotu; prázdný záznam potlačuje staré prostředí."""
    if os.name != "nt":
        raise APIKeyStoreError("Trvalé ukládání API klíče vyžaduje uživatelský registr Windows.")
    if not isinstance(value, str):
        raise APIKeyStoreError("API klíč musí být text.")
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                previous = winreg.QueryValueEx(key, "OPENAI_API_KEY")
            except FileNotFoundError:
                previous = None
            written = False
            try:
                winreg.SetValueEx(key, "OPENAI_API_KEY", 0, winreg.REG_SZ, value)
                written = True
                if winreg.QueryValueEx(key, "OPENAI_API_KEY") != (value, winreg.REG_SZ):
                    raise OSError("Ověření zápisu selhalo.")
            except OSError:
                if written:
                    try:
                        if previous is None:
                            winreg.DeleteValue(key, "OPENAI_API_KEY")
                        else:
                            winreg.SetValueEx(key, "OPENAI_API_KEY", 0, previous[1], previous[0])
                    except OSError as exc:
                        raise APIKeyStoreError("Ověření uložení API klíče i obnovení původního záznamu selhalo. Aktuální klíč nebyl změněn.") from exc
                raise
        return True
    except OSError as exc:
        raise APIKeyStoreError("API klíč se nepodařilo trvale uložit a ověřit v uživatelském registru Windows. Aktuální klíč nebyl změněn.") from exc


def _env_name(key: str) -> str:
    return f"KAJOVO_SECRET_{key.upper()}"


def set_secret(key: str, value: str) -> bool:
    value = value or ""
    env_name = _env_name(key)
    os.environ.pop(env_name, None)
    try:
        import keyring  # type: ignore

        if value:
            keyring.set_password(SERVICE_NAME, key, value)
        else:
            try:
                keyring.delete_password(SERVICE_NAME, key)
            except keyring.errors.PasswordDeleteError:
                pass
        return True
    except Exception:
        if value:
            os.environ[env_name] = value
        else:
            os.environ.pop(env_name, None)
        return False


def get_secret(key: str) -> Optional[str]:
    env_name = _env_name(key)
    if env_name in os.environ:
        return os.environ[env_name] or None
    try:
        import keyring  # type: ignore

        value = keyring.get_password(SERVICE_NAME, key)
        if value:
            return value
    except Exception:
        pass
    value = os.environ.get(_env_name(key), "")
    return value or None
