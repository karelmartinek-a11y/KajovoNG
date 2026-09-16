from __future__ import annotations

import os
import warnings
from typing import Optional

SERVICE_NAME = "kajovo"
API_KEY_ACCOUNT = "openai_api_key"
_API_KEY_EMPTY_SENTINEL = "__KAJOVO_EXPLICIT_EMPTY_V1__"
_MISSING = object()


class APIKeyStoreError(RuntimeError):
    """Chyba úložiště bez přihlašovacích údajů v chybové zprávě."""


def _read_persisted_api_key() -> Optional[str]:
    """Přečte pouze legacy hodnotu z HKCU\\Environment kvůli jednorázové migraci."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, "OPENAI_API_KEY")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise APIKeyStoreError(
            "Nelze přečíst legacy API klíč z uživatelského registru Windows."
        ) from exc
    if kind != winreg.REG_SZ or not isinstance(value, str):
        raise APIKeyStoreError(
            "Legacy API klíč má neplatný typ záznamu v registru Windows."
        )
    return value


def _delete_persisted_api_key() -> None:
    """Odstraní legacy OPENAI_API_KEY až po ověřeném zápisu do credential storage."""
    if os.name != "nt":
        return
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            try:
                winreg.DeleteValue(key, "OPENAI_API_KEY")
            except FileNotFoundError:
                return
    except OSError as exc:
        raise APIKeyStoreError(
            "Ověřený API klíč je uložen v credential storage, ale legacy záznam "
            "v registru Windows se nepodařilo odstranit."
        ) from exc


def _keyring_module():
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise APIKeyStoreError("OS credential storage není dostupné.") from exc
    return keyring


def _read_keyring_api_key_record():
    keyring = _keyring_module()
    try:
        value = keyring.get_password(SERVICE_NAME, API_KEY_ACCOUNT)
    except Exception as exc:
        raise APIKeyStoreError("API klíč nelze přečíst z OS credential storage.") from exc
    if value is None:
        return _MISSING
    if not isinstance(value, str):
        raise APIKeyStoreError("API klíč v OS credential storage má neplatný typ.")
    return value


def _restore_keyring_api_key_record(previous) -> None:
    keyring = _keyring_module()
    try:
        if previous is _MISSING:
            try:
                keyring.delete_password(SERVICE_NAME, API_KEY_ACCOUNT)
            except keyring.errors.PasswordDeleteError:
                pass
            current = keyring.get_password(SERVICE_NAME, API_KEY_ACCOUNT)
            if current is not None:
                raise RuntimeError("Credential rollback se nepodařilo ověřit.")
            return

        keyring.set_password(SERVICE_NAME, API_KEY_ACCOUNT, previous)
        if keyring.get_password(SERVICE_NAME, API_KEY_ACCOUNT) != previous:
            raise RuntimeError("Credential rollback se nepodařilo ověřit.")
    except Exception as exc:
        raise APIKeyStoreError(
            "Obnovení původního API klíče v OS credential storage selhalo."
        ) from exc


def _write_keyring_api_key_record(record: str):
    previous = _read_keyring_api_key_record()
    keyring = _keyring_module()
    try:
        keyring.set_password(SERVICE_NAME, API_KEY_ACCOUNT, record)
        if keyring.get_password(SERVICE_NAME, API_KEY_ACCOUNT) != record:
            raise RuntimeError("Ověření zápisu do credential storage selhalo.")
    except Exception as exc:
        try:
            _restore_keyring_api_key_record(previous)
        except APIKeyStoreError as rollback_exc:
            raise APIKeyStoreError(
                "Uložení API klíče i obnovení původního credential záznamu selhalo."
            ) from rollback_exc
        raise APIKeyStoreError(
            "API klíč se nepodařilo bezpečně uložit a ověřit v OS credential storage."
        ) from exc
    return previous


def _record_from_api_key(value: str) -> str:
    return _API_KEY_EMPTY_SENTINEL if value == "" else value


def _api_key_from_record(record: str) -> str:
    return "" if record == _API_KEY_EMPTY_SENTINEL else record


def _warn_legacy_cleanup_failure(exc: APIKeyStoreError) -> None:
    warnings.warn(str(exc), RuntimeWarning, stacklevel=2)


def load_api_key() -> str:
    """Načte credential, případně bezpečně migruje legacy Windows registry hodnotu.

    Explicitně uložené smazání má stále přednost před zděděným OPENAI_API_KEY.
    Pokud credential backend není dostupný, zachová se kompatibilní čtení legacy
    registru/environmentu; žádný legacy záznam se při takovém fallbacku nemaže.
    """
    try:
        record = _read_keyring_api_key_record()
    except APIKeyStoreError:
        legacy = _read_persisted_api_key()
        value = legacy if legacy is not None else os.environ.get("OPENAI_API_KEY", "")
        os.environ["OPENAI_API_KEY"] = value
        return value

    if record is not _MISSING:
        value = _api_key_from_record(record)
        legacy = _read_persisted_api_key()
        if legacy is not None:
            try:
                _delete_persisted_api_key()
            except APIKeyStoreError as exc:
                _warn_legacy_cleanup_failure(exc)
        os.environ["OPENAI_API_KEY"] = value
        return value

    legacy = _read_persisted_api_key()
    if legacy is not None:
        try:
            _write_keyring_api_key_record(_record_from_api_key(legacy))
        except APIKeyStoreError as exc:
            warnings.warn(
                "Legacy API klíč zůstává v registru Windows, protože migrace do "
                f"OS credential storage selhala: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
        else:
            try:
                _delete_persisted_api_key()
            except APIKeyStoreError as exc:
                _warn_legacy_cleanup_failure(exc)
        os.environ["OPENAI_API_KEY"] = legacy
        return legacy

    value = os.environ.get("OPENAI_API_KEY", "")
    os.environ["OPENAI_API_KEY"] = value
    return value


def persist_api_key(value: str) -> bool:
    """Zapíše a readbackem ověří klíč v OS credential storage.

    Prázdná hodnota je ukládána jako interní nesekretní sentinel, aby výslovné
    smazání potlačilo stale OPENAI_API_KEY zděděný od parent procesu. Pokud po
    úspěšném credential zápisu nelze odstranit legacy registry záznam, credential
    změna se vrátí zpět a operace skončí chybou.
    """
    if not isinstance(value, str):
        raise APIKeyStoreError("API klíč musí být text.")

    previous = _write_keyring_api_key_record(_record_from_api_key(value))
    try:
        _delete_persisted_api_key()
    except APIKeyStoreError as exc:
        try:
            _restore_keyring_api_key_record(previous)
        except APIKeyStoreError as rollback_exc:
            raise APIKeyStoreError(
                "API klíč byl zapsán do credential storage, ale odstranění legacy "
                "registru i rollback credential změny selhaly."
            ) from rollback_exc
        raise APIKeyStoreError(
            "Legacy API klíč se nepodařilo odstranit; credential změna byla vrácena."
        ) from exc
    return True


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
