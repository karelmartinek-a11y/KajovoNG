"""Deterministická česká sdělení založená na doložených příčinách výjimek."""

from __future__ import annotations

import errno
import re
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
    next_step: str = "Obnovte přehled. Pokud chyba pokračuje, požádejte o pomoc správce aplikace."
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
    "invalid_input_fidelity_model": ("Model odm\u00edtl volbu pro zachov\u00e1n\u00ed detail\u016f p\u016fvodn\u00ed fotografie; fotografie se neupravila.", "Spus\u0165te \u00fapravu znovu. Pokud se chyba vr\u00e1t\u00ed, vyberte jin\u00fd model pro fotografie."),
    "invalid_json": ("Služba vrátila nečitelný výsledek.", "Obnovte stav dávky. Pokud chyba trvá, otevřete její podrobnosti."),
    "invalid_request": ("Služba odmítla požadavek, protože zadané údaje nejsou platné.", "Zkontrolujte zadání a zvolené možnosti. Před opakováním ověřte stav původní operace."),
    "server_error": ("Služba měla dočasnou chybu.", "Počkejte chvíli a obnovte stav původní operace."),
    "internal_error": ("Služba měla dočasnou chybu.", "Počkejte chvíli a obnovte stav původní operace."),
    "batch_expired": ("Služba ukončila dávku po vypršení její platnosti.", "Zkontrolujte dokončené výsledky; chybějící úlohy odešlete znovu až po ověření stavu."),
    "batch_cancelled": ("Dávka byla zrušena dříve, než dokončila všechny úlohy.", "Uložte dostupné výsledky; nedokončené úlohy lze spustit znovu."),
    "invalid_image": ("Služba nemohla přečíst některou zdrojovou fotografii.", "Otevřete obrázek v počítači a případně jej uložte znovu jako PNG, JPEG nebo WebP."),
    "invalid_image_format": ("Formát některé fotografie není podporován.", "Použijte PNG, JPEG nebo WebP."),
    "image_request_failed": ("Služba nedokázala upravit některou fotografii.", "Zkontrolujte zadání a stav dávky. Ostatní dokončené výsledky lze uložit."),
    "unsupported_value": ("Vybraný model nepodporuje jednu z voleb této úpravy.", "Zvolte podporovanou hodnotu nebo jiný model."),
    "file_not_found": ("Služba nenašla jeden z odeslaných souborů.", "Zkontrolujte, zda je soubor stále dostupný, a odešlete úlohu znovu."),
}

HTTP_MESSAGES = {
    400: ("Služba odmítla zadané údaje.", "Zkontrolujte vybrané možnosti a zadání."),
    401: ("Služba odmítla přístupový klíč.", "Zkontrolujte přístupový klíč v Nastavení."),
    403: ("Účet nemá oprávnění k této operaci.", "Ověřte oprávnění účtu a vybraný projekt u poskytovatele."),
    404: ("Služba požadovaný model nebo soubor nenalezla.", "Obnovte katalog modelů a zkontrolujte, zda má účet k vybrané položce přístup."),
    408: ("Služba odpověděla příliš pozdě.", "Obnovte stav původní operace, než ji spustíte znovu."),
    409: ("Stav u služby se mezitím změnil.", "Obnovte přehled a pokračujte podle nového stavu."),
    413: ("Odesílané soubory jsou příliš velké.", "Zmenšete soubory nebo je rozdělte do menší dávky."),
    422: ("Služba zadané údaje nedokázala zpracovat.", "Zkontrolujte zadání a zvolené možnosti."),
    429: ("Služba požadavek odmítla kvůli limitu účtu nebo rychlosti požadavků.", "Ověřte stav původní operace a limity účtu; potom chvíli vyčkejte."),
    500: ("Služba měla vnitřní chybu.", "Počkejte chvíli a obnovte stav původní operace."),
    502: ("Spojení se službou bylo dočasně přerušeno.", "Obnovte stav původní operace."),
    503: ("Služba je dočasně nedostupná.", "Počkejte chvíli a obnovte stav původní operace."),
    504: ("Služba nestihla operaci dokončit včas.", "Obnovte stav původní operace; výsledek může být stále ve zpracování."),
}

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [SKRYTÝ KLÍČ]"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"), "[SKRYTÝ KLÍČ]"),
    (re.compile(r"(?i)(api[_ -]?key|password|passwd|secret|token)(\s*[=:]\s*)[^\s,;]+"), r"\1\2[SKRYTO]"),
)


def _safe_detail(value: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class RecordedError(Exception):
    """Bezpečný obal strukturované chyby uložené ve výsledku dávky."""

    def __init__(self, code="", status_code=None, detail=""):
        self.code = str(code or "")
        self.status_code = status_code
        self.body = {}
        super().__init__(detail or self.code or "Neznámá chyba výsledku")


def describe_recorded_error(value, *, operation="Zpracování výsledku") -> UserError:
    """Převede chybu uloženou v JSONL nebo v místní evidenci na zprávu pro UI."""
    if isinstance(value, str):
        try:
            import json

            value = json.loads(value)
        except (TypeError, ValueError):
            return describe_error(RecordedError(detail="Nepodařilo se přečíst podrobnosti chyby."), operation=operation)
    payload = value if isinstance(value, dict) else {}
    nested = payload.get("error", payload)
    if isinstance(nested, dict) and isinstance(nested.get("body"), dict):
        nested = nested["body"].get("error") or nested["body"]
    if not isinstance(nested, dict):
        nested = {}
    code = nested.get("code") or nested.get("type") or payload.get("code")
    response = payload.get("response")
    status = payload.get("status_code") or (response.get("status_code") if isinstance(response, dict) else None)
    if isinstance(response, dict):
        response_body = response.get("body")
        if isinstance(response_body, dict):
            response_error = response_body.get("error")
            if isinstance(response_error, dict):
                code = response_error.get("code") or code
    error = RecordedError(code=code, status_code=status, detail=str(nested.get("message") or code or ""))
    return describe_error(error, operation=operation)

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
            interface_issue = next(
                (issue for issue in item.issues if issue.code == "interface.unknown_path"),
                None,
            )
            legacy_user_launch_issue = (
                "IFC-USER-LAUNCH" in str(item)
                and "interface odkazuje na neznámou cestu" in str(item)
            )
            if interface_issue or legacy_user_launch_issue:
                actual = interface_issue.actual if interface_issue else {}
                consumers = actual.get("consumers", []) if isinstance(actual, dict) else []
                if any(str(value).casefold() in {"uživatel", "user"} for value in consumers):
                    message = (
                        "Návrh omylem uvedl uživatele programu jako soubor projektu. "
                        "Kontrola proto tvorbu zastavila dřív, než vznikly soubory programu."
                    )
                elif legacy_user_launch_issue:
                    message = (
                        "Návrh programu chybně propojil jeho části. "
                        "Kontrola proto tvorbu zastavila dřív, než vznikly soubory programu."
                    )
                else:
                    message = (
                        "Návrh programu odkazuje na soubor, který v něm není uveden. "
                        "Kontrola proto tvorbu zastavila dřív, než vznikly soubory programu."
                    )
                detail = "\n".join(
                    f"{issue.stage} {issue.pointer}: {issue.message}; skutečné hodnoty: {issue.actual}"
                    for issue in item.issues
                ) or str(item)
                return UserError(
                    "contract",
                    "interface.unknown_path",
                    message,
                    True,
                    _safe_detail(detail),
                    "Spusťte tvorbu znovu. Pokud se chyba opakuje, požádejte o pomoc správce aplikace.",
                    False,
                )
            message = "Uložené údaje neodpovídají očekávanému formátu."
            if item.issues:
                first = item.issues[0]
                if first.pointer:
                    message += f" Problém se týká části {first.pointer}."
            if len(item.issues) > 1:
                message += f" Bylo nalezeno dalších {len(item.issues) - 1} problémů."
            return UserError("contract", item.code, message, True,
                             _safe_detail("\n".join(f"{i.stage} {i.pointer}: {i.message}" for i in item.issues) or str(item)),
                             "Zkontrolujte zadání a obnovte přehled. Uložené podklady zůstávají zachované.")
    from .comic_types import ComicError
    for item in chain:
        if isinstance(item, ComicError):
            return UserError("comic", item.code, "Operaci v Komiksu se nepodařilo dokončit.", True, _safe_detail(str(item)),
                             "Zkontrolujte zadání nebo obnovte uloženou operaci.", item.retryable)
    detail = _safe_detail("\n\n".join(f"{type(item).__name__}: {item}" for item in chain))
    for item in chain:
        if isinstance(item, subprocess.CalledProcessError):
            detail += f"\nVýstup procesu:\n{item.stdout or ''}\nChybový výstup:\n{item.stderr or ''}"
    detail = _safe_detail(detail)

    def result(domain, code, message, known=True, next_step="Ověřte technické podrobnosti a stav operace."):
        return UserError(domain, str(code), message, known, detail, next_step)

    for item in chain:
        if isinstance(item, ValueError) and "sha-256" in str(item).lower() and "neodpov" in str(item).lower():
            return result(
                "integrity",
                "artifact.changed",
                "Soubor se změnil od uložení, proto jej nelze bezpečně otevřít ani převzít.",
                next_step="Použijte původní soubor nebo obnovte nepoškozenou kopii běhu.",
            )

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
            return UserError("timeout", "operation.timeout", "Operace trvala déle, než se čekalo; její výsledek zatím není znám.", False,
                             detail, "Obnovte stav operace, než ji spustíte znovu.", False)
        winerror = getattr(item, "winerror", None)
        if winerror in WINDOWS_CODES:
            return result("windows", winerror, WINDOWS_CODES[winerror])
        if isinstance(item, OSError) and item.errno in SYSTEM_CODES:
            return result("filesystem", item.errno, SYSTEM_CODES[item.errno])
        if isinstance(item, UnicodeError):
            return result("encoding", "invalid_encoding", "Text nelze přečíst nebo uložit v požadovaném kódování.")
        if isinstance(item, requests.ConnectionError):
            return UserError("network", "connection.lost", "Spojení se službou se přerušilo; výsledek operace zatím není znám.", False,
                             detail, "Obnovte stav operace, než ji spustíte znovu.", False)
        # Paramiko se importuje až zde; aplikace bez SSH nepoužije síť ani konfiguraci.
        from paramiko.ssh_exception import AuthenticationException, BadHostKeyException

        if isinstance(item, BadHostKeyException):
            return result("ssh", "host_key", "Identita vzdáleného počítače neodpovídá očekávanému klíči.")
        if isinstance(item, AuthenticationException):
            return result("ssh", "authentication", "Vzdálený počítač odmítl přihlášení.")
    for item in chain:
        status = getattr(item, "status_code", None)
        if status is not None:
            message, next_step = HTTP_MESSAGES.get(
                status,
                ("Služba operaci odmítla.", "Obnovte stav operace; pokud problém trvá, otevřete technické podrobnosti."),
            )
            return UserError("http", f"http.{status}", message, status in HTTP_MESSAGES and status != 429,
                             detail, next_step, status in {429, 500, 502, 503, 504})
    if isinstance(error, (ValueError, TypeError)):
        raw = str(error).strip()
        technical = (
            len(raw) > 240
            or any(token in raw for token in ("_", "{", "}", "[", "]", "SHA-", "JSON", "BATCH", "PHOTO", "provider", "traceback", "Traceback"))
            or re.search(r"(?:[A-Za-z]:\\|/[^ /]+/|\b[a-zA-Z]+Error\b)", raw)
            or not re.search(r"[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]", raw)
        )
        message = (
            f"{operation} se nepodařilo dokončit kvůli zadaným nebo uloženým údajům."
            if technical or not raw
            else _safe_detail(raw)
        )
        next_step = "Zkontrolujte vyplněná pole a zkuste to znovu."
        code = "input.invalid"
    else:
        message = f"{operation} se nepodařilo dokončit a přesnou příčinu se nepodařilo určit."
        next_step = "Obnovte přehled. Pokud chyba pokračuje, požádejte o pomoc správce aplikace."
        code = "operation.unexpected"
    return UserError("unknown", code, message, False, detail, next_step)
