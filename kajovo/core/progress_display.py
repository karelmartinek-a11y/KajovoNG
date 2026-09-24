"""Lidské názvy a odvození plánu pro živé zobrazení operací."""

from __future__ import annotations

from dataclasses import dataclass


STAGE_TITLES = {
    "RUN": "Celý běh",
    "Lokální validace": "Lokální kontrola před spuštěním",
    "Přílohy": "Kontrola vstupních příloh",
    "Diagnostika": "Sběr diagnostických podkladů",
    "Vstupní data": "Příprava vstupních dat",
    "Indexace": "Indexace podkladů pro hledání",
    "A0": "Analýza zadání",
    "A0R": "Rozpracování požadavků",
    "A1": "Architektonický plán",
    "A2": "Struktura projektu",
    "A2_SPINE": "Návrh souborů a rozhraní",
    "A2_DETAIL": "Specifikace jednotlivých souborů",
    "A2Q": "Kontrola implementačního návrhu",
    "A3": "Vytváření souborů",
    "B0R": "Rozpracování požadavků na změnu",
    "B1": "Plán změn",
    "B2": "Struktura změn",
    "B2_SPINE": "Návrh změn souborů a rozhraní",
    "B2_DETAIL": "Specifikace jednotlivých změn",
    "B2Q": "Kontrola návrhu změn",
    "B3": "Úprava souborů",
    "Upload": "Nahrávání podkladů",
    "Download": "Stahování výsledků",
    "Ukládání": "Ukládání do výstupu",
    "Validace kontraktů": "Kontrola výsledků",
    "BATCH": "Zpracování dávky",
    "Čekání na dávku": "Čekání na vzdálenou dávku",
    "Operace": "Aktuální operace",
    "A0R_REQUIREMENTS": "Profesionální requirements",
    "B0R_REQUIREMENTS": "Change requirements",
    "A2Q_QUALITY_GATE": "Quality gate",
    "B2Q_QUALITY_GATE": "Quality gate",
}

STATE_TITLES = {
    "created": "Vytvořeno",
    "preparing": "Připravuje se",
    "running": "Běží",
    "active": "Probíhá",
    "waiting": "Čeká na odpověď služby",
    "validating_result": "Ověřuje výsledek",
    "repairing": "Opravuje podklad",
    "response_pending": "Čeká na odpověď",
    "batch_prepared": "BATCH připraven",
    "batch_pending": "BATCH běží",
    "importing": "Přebírá se",
    "ready_to_import": "K převzetí",
    "completed": "Dokončeno",
    "completed_unverified": "Převzato bez ověření funkčnosti",
    "needs_clarification": "Čeká na upřesnění zadání",
    "waiting_manual_resource": "Čeká na ruční podklad",
    "closed": "Dokončeno / uzavřeno",
    "dry_run": "Dry-run / návrh bez zápisu",
    "plan_ready": "Ověřený plán připraven / výroba zastavena",
    "qfile_plan_ready": "Návrh QFILE připraven / čeká na potvrzení",
    "partial": "Částečně dokončeno",
    "files_complete_unverified": "Soubory převzaty, funkčnost neověřena",
    "unfinished_record": "Konec fáze nezapsán",
    "cancelled": "Zrušeno",
    "stopped": "Zastaveno",
    "cancelling": "Ruší se",
    "failed": "Operace selhala",
    "error": "Chyba",
    "submission_unknown": "Neznámý výsledek odeslání",
    "corrupt_state": "Chyba evidence",
    "unknown": "Neznámý stav",
    "expired": "Vypršel čas služby",
    "not_started": "Ještě nezačalo",
    "blocked": "Blokováno",
    "skipped": "Přeskočeno",
    "queued": "Čeká ve frontě",
    "validating": "Služba validuje",
    "in_progress": "Vzdálené zpracování probíhá",
    "finalizing": "Služba finalizuje výstup",
    "pending": "Čeká na zpracování",
    "submitted": "Odesláno službě",
    "downloaded": "Výsledky převzaty",
}

SOURCE_TITLES = {
    "local": "Lokální zpracování",
    "api": "OpenAI Responses API",
    "files_api": "OpenAI Files API",
    "batch_api": "OpenAI Batch API",
    "vector_store": "OpenAI Vector Store",
    "image_api": "OpenAI Image API",
    "upload": "Upload",
    "download": "Download",
    "disk": "Zápis na disk",
    "validation": "Lokální validace",
}


@dataclass(frozen=True)
class DisplayStep:
    key: str
    title: str
    state: str = "pending"


def stage_title(stage: str) -> str:
    if stage in STAGE_TITLES:
        return STAGE_TITLES[stage]
    base = stage.split("_", 1)[0]
    return STAGE_TITLES.get(base, stage.replace("_", " "))


def state_title(state: str) -> str:
    return STATE_TITLES.get(state, state.replace("_", " "))


def source_title(source: str) -> str:
    return SOURCE_TITLES.get(source, source.replace("_", " "))


def default_plan(mode: str = "", quality: bool = False) -> list[str]:
    if mode == "MODIFY":
        stages = ["B0R", "B1", "B2_SPINE", "B2_DETAIL", "B2"]
        if quality:
            stages.append("B2Q")
        return stages + ["B3", "Validace kontraktů", "Ukládání"]
    if mode == "GENERATE":
        stages = ["A0R", "A1", "A2_SPINE", "A2_DETAIL", "A2"]
        if quality:
            stages.append("A2Q")
        return stages + ["A3", "Validace kontraktů", "Ukládání"]
    return []


def build_steps(events, *, mode: str = "", quality: bool = False) -> list[DisplayStep]:
    """Sestaví kompaktní plán z doložených událostí bez domýšlení výsledků."""
    keys = default_plan(mode, quality)
    seen = []
    for event in events:
        if event.stage not in seen and event.stage != "RUN":
            seen.append(event.stage)
    for stage in seen:
        if stage not in keys:
            keys.append(stage)
    current = next((event.stage for event in reversed(events) if event.stage != "RUN"), "")
    completed = {
        event.stage
        for event in events
        if event.stage != "RUN" and event.state == "completed"
    }
    return [
        DisplayStep(
            key,
            stage_title(key),
            "done"
            if key in completed
            else "current"
            if key == current
            else "pending",
        )
        for key in keys
    ]


def event_sentence(event) -> str:
    title = stage_title(event.stage)
    state = state_title(event.state)
    source = source_title(getattr(event, "source", "local"))
    if event.detail:
        return f"{event.detail} ({source})"
    return f"{title}: {state} ({source})"
