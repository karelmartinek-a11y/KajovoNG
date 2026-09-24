"""Deterministická česká sdělení založená na doložených příčinách výjimek."""

from __future__ import annotations

import errno
import smtplib
import socket
import ssl
import subprocess
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class UserError:
    domain: str
    code: str
    message: str
    cause_known: bool
    detail: str
    next_step: str = "Otevřete technické podrobnosti a ověřte stav operace."
    retry_safe: bool = False


# Katalog zachovává význam kódu; například HTTP 429 samo nerozlišuje kredit a rychlost.
API_CODES = {
    "local_request_evidence_failed": ("Požadavek nebyl odeslán: selhala místní evidence požadavku v aplikaci.", "Jde o interní chybu aplikace; původní příčina je v technických podrobnostech."),
    "max_output_tokens": ("Odpověď dosáhla výstupního limitu a není úplná.", "Upravte rozpočet výstupu nebo rozdělení dodávky; neimportujte částečný obsah."),
    "credit_balance_exhausted": ("Předplacený kredit organizace byl vyčerpán.", "Doplňte kredit organizace u poskytovatele."),
    "organization_spend_limit_exceeded": ("Organizace dosáhla nastaveného rozpočtového limitu.", "Ověřte rozpočet organizace u poskytovatele."),
    "project_spend_limit_exceeded": ("Projekt dosáhl nastaveného rozpočtového limitu.", "Ověřte rozpočet projektu u poskytovatele."),
    "organization_usage_limit_exceeded": ("Organizace dosáhla limitu využití přiděleného poskytovatelem.", "Požádejte poskytovatele o zvýšení přiděleného limitu."),
    "slow_down": ("Rychlost odesílání požadavků rostla příliš rychle.", "Snižte rychlost a respektujte dobu čekání oznámenou službou."),
    "server_is_overloaded": ("Vybraný model je dočasně přetížený.", "Ověřte stav původního požadavku a dobu čekání oznámenou službou."),
    "invalid_api_key": ("Přístupový klíč služba odmítla.", "Zkontrolujte klíč v Nastavení."),
    "insufficient_quota": ("Služba odmítla požadavek kvůli vyčerpanému kreditu nebo přidělenému rozpočtu.", "Ověřte kredit a rozpočet projektu u poskytovatele."),
    "rate_limit_exceeded": ("Požadavek překročil povolenou rychlost zpracování služby.", "Vyčkejte podle informace služby a ověřte stav původního požadavku."),
    "context_length_exceeded": ("Vstup překročil kontextovou kapacitu vybraného modelu.", "Zmenšete zadání nebo zvolte kompatibilní model s větší kapacitou."),
    "model_not_found": ("Vybraný model není dostupný pro tento požadavek.", "Ověřte přesný model a přístup projektu v katalogu."),
    "unsupported_parameter": ("Vybraný model nepodporuje odeslaný parametr.", "Zkontrolujte parametr uvedený v technických podrobnostech."),
    "invalid_parameter": ("Služba odmítla neplatnou hodnotu parametru.", "Opravte parametr uvedený v technických podrobnostech."),
    "content_policy_violation": ("Služba odmítla obsah podle svých pravidel.", "Zkontrolujte zadání a přiložené soubory."),
}

SYSTEM_CODES = {
    errno.ENOENT: "Požadovaný soubor nebo adresář neexistuje.",
    errno.EACCES: "Systém nepovolil přístup k souboru nebo adresáři.",
    errno.EPERM: "Systém nepovolil požadovanou operaci.",
    errno.ENOSPC: "Na cílovém úložišti není dostatek volného místa.",
    errno.EROFS: "Cílové úložiště umožňuje pouze čtení.",
    errno.EEXIST: "V cílovém umístění již existuje soubor nebo adresář stejného názvu.",
    errno.ENOTDIR: "Zadaná cesta neoznačuje adresář.",
    errno.EISDIR: "Zadaná cesta označuje adresář místo souboru.",
    errno.ENAMETOOLONG: "Zadaná cesta je pro souborový systém příliš dlouhá.",
    errno.ECONNREFUSED: "Cílový počítač odmítl spojení.",
    errno.ECONNRESET: "Navázané spojení bylo přerušeno protistranou.",
    errno.ETIMEDOUT: "Spojení se nepodařilo dokončit v časovém limitu.",
}

WINDOWS_CODES = {
    2: SYSTEM_CODES[errno.ENOENT],
    3: "Zadaná cesta neexistuje.",
    5: SYSTEM_CODES[errno.EACCES],
    32: "Soubor používá jiný proces a nyní jej nelze změnit.",
    33: "Požadovanou část souboru uzamkl jiný proces.",
    112: SYSTEM_CODES[errno.ENOSPC],
    123: "Zadaná cesta obsahuje nepovolený název.",
    206: SYSTEM_CODES[errno.ENAMETOOLONG],
}


def _chain(error: BaseException) -> list[BaseException]:
    chain = []
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return chain


def describe_error(error: BaseException, *, operation: str = "Operaci") -> UserError:
    """Neznámou příčinu nedoplňuje odhadem ani podle podobnosti volného textu."""
    chain = _chain(error)
    from .contracts import ContractError
    for item in chain:
        if isinstance(item, ContractError):
            message = str(item)
            if item.issues:
                first = item.issues[0]
                message = f"{first.stage} {first.pointer}: {first.message}"
            if len(message) > 600:
                message = message[:600] + "… Úplný nález je v podrobnostech."
            if len(item.issues) > 1:
                message += f" Další nálezy: {len(item.issues) - 1}."
            return UserError("contract", item.code, message, True,
                             "\n".join(f"{i.stage} {i.pointer}: {i.message}" for i in item.issues) or str(item),
                             "Opravte uvedenou fázi; zachované podklady a pokusy jsou v Historii.")
    from .comic_types import ComicError
    for item in chain:
        if isinstance(item, ComicError):
            return UserError("comic", item.code, str(item), True, str(item),
                             "Opravte uvedené zadání nebo obnovte uloženou operaci.", item.retryable)
    detail = "\n\n".join(f"{type(item).__name__}: {item}" for item in chain)
    for item in chain:
        if isinstance(item, subprocess.CalledProcessError):
            detail += f"\nVýstup procesu:\n{item.stdout or ''}\nChybový výstup:\n{item.stderr or ''}"

    def result(domain, code, message, known=True, next_step="Ověřte technické podrobnosti a stav operace."):
        return UserError(domain, str(code), message, known, detail, next_step)

    for item in reversed(chain):
        code = getattr(item, "code", None)
        body = getattr(item, "body", None)
        if not code and isinstance(body, dict):
            nested = body.get("error", body)
            code = nested.get("code") if isinstance(nested, dict) else None
        if code in API_CODES:
            message, action = API_CODES[code]
            return result("openai", code, message, next_step=action)
        if isinstance(item, smtplib.SMTPAuthenticationError):
            return result("smtp", item.smtp_code, "Poštovní server odmítl přihlášení.")
        if isinstance(item, smtplib.SMTPRecipientsRefused):
            return result("smtp", "recipients_refused", "Poštovní server odmítl zadané příjemce zprávy.")
        if isinstance(item, smtplib.SMTPSenderRefused):
            return result("smtp", item.smtp_code, "Poštovní server odmítl adresu odesílatele.")
        if isinstance(item, smtplib.SMTPNotSupportedError):
            return result("smtp", "unsupported", "Poštovní server nepodporuje požadovanou funkci.")
        if isinstance(item, ssl.SSLCertVerificationError):
            return result("tls", "certificate", "Zabezpečené spojení nebylo navázáno, protože ověření certifikátu selhalo.")
        if isinstance(item, (ssl.SSLError, requests.exceptions.SSLError)):
            return result("tls", "handshake", "Zabezpečené spojení selhalo a přesná příčina není doložena.", False)
        if isinstance(item, socket.gaierror):
            return result("network", "name_resolution", "Název cílového počítače se nepodařilo převést na síťovou adresu.")
        if isinstance(item, (TimeoutError, requests.Timeout, subprocess.TimeoutExpired)):
            return result("timeout", "timeout", "Operace překročila časový limit a její konečný výsledek zatím není znám.", False)
        winerror = getattr(item, "winerror", None)
        if winerror in WINDOWS_CODES:
            return result("windows", winerror, WINDOWS_CODES[winerror])
        if isinstance(item, OSError) and item.errno in SYSTEM_CODES:
            return result("filesystem", item.errno, SYSTEM_CODES[item.errno])
        if isinstance(item, UnicodeError):
            return result("encoding", "invalid_encoding", "Text nelze přečíst nebo uložit v požadovaném kódování.")
        if isinstance(item, requests.ConnectionError):
            return result("network", "connection", "Spojení se službou selhalo a přesná příčina není doložena.", False)
        # Paramiko se importuje až zde; aplikace bez SSH nepoužije síť ani konfiguraci.
        from paramiko.ssh_exception import AuthenticationException, BadHostKeyException

        if isinstance(item, BadHostKeyException):
            return result("ssh", "host_key", "Identita vzdáleného počítače neodpovídá očekávanému klíči.")
        if isinstance(item, AuthenticationException):
            return result("ssh", "authentication", "Vzdálený počítač odmítl přihlášení.")
    for item in chain:
        status = getattr(item, "status_code", None)
        if status is not None:
            messages = {
                400: "Služba odmítla neplatný požadavek.",
                401: "Služba odmítla ověření přístupu.",
                403: "Služba nepovolila požadovanou operaci.",
                404: "Služba nenalezla požadovaný prostředek nebo jej pro tento přístup nezpřístupnila.",
                409: "Služba oznámila konflikt se současným stavem prostředku.",
                413: "Odesílaná data překročila velikost povolenou službou.",
                429: "Služba odmítla požadavek kvůli limitu, jehož přesný druh neuvedla.",
            }
            return result("http", status, messages.get(status, "Služba vrátila chybu a přesná příčina není doložena."), False)
    return result("unknown", type(error).__name__, f"{operation} se nepodařilo dokončit a přesná příčina není doložena.", False)
