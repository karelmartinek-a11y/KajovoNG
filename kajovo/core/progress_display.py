"""Lidské názvy a odvození plánu pro živé zobrazení operací."""

from __future__ import annotations

from dataclasses import dataclass


STAGE_TITLES = {
    "RUN": "Celý běh", "Lokální validace": "Lokální kontrola před spuštěním",
    "Přílohy": "Kontrola vstupních příloh", "Diagnostika": "Sběr diagnostických podkladů",
    "Vstupní data": "Příprava vstupních dat", "Indexace": "Indexace podkladů pro hledání",
    "A0": "Analýza zadání", "A0R": "Upřesnění požadavků", "A1": "Architektonický plán",
    "A2": "Struktura projektu", "A2Q": "Nezávislá kontrola návrhu", "A3": "Vytváření souborů",
    "B0R": "Upřesnění požadovaných změn", "B1": "Plán změn", "B2": "Struktura změn",
    "B2Q": "Nezávislá kontrola změn", "B3": "Úprava souborů", "Upload": "Nahrávání podkladů",
    "Download": "Stahování výsledků", "Ukládání": "Ukládání do výstupu",
    "Validace kontraktů": "Kontrola výsledků", "BATCH": "Zpracování dávky",
    "Čekání na dávku": "Čekání na vzdálenou dávku", "Operace": "Aktuální operace",
    "A0R_REQUIREMENTS": "Profesionální requirements", "B0R_REQUIREMENTS": "Change requirements",
    "A2Q_QUALITY_GATE": "Quality gate", "B2Q_QUALITY_GATE": "Quality gate",
}

STATE_TITLES = {
    "active": "Probíhá", "waiting": "Čeká na odpověď služby", "completed": "Dokončeno",
    "failed": "Operace selhala", "partial": "Dokončeno částečně", "cancelled": "Zastaveno",
    "batch_pending": "Předáno do dávky", "response_pending": "Odpověď stále není dokončená",
    "submission_unknown": "Výsledek odeslání není znám", "dry_run": "Ověřeno bez zápisu",
    "files_complete_unverified": "Soubory převzaty, funkčnost neověřena",
    "cancelling": "Čeká se na potvrzení zrušení",
}

SOURCE_TITLES = {
    "local": "Lokální práce", "api": "OpenAI Responses API", "files_api": "OpenAI Files API",
    "batch_api": "OpenAI Batch API", "upload": "Upload", "download": "Download",
    "disk": "Zápis na disk", "validation": "Lokální validace",
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
        stages = ["B0R", "B1", "B2"]
        if quality:
            stages.append("B2Q")
        return stages + ["B3", "Validace kontraktů", "Ukládání"]
    if mode == "GENERATE":
        stages = ["A0", "A0R", "A1", "A2"]
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
    completed = {event.stage for event in events if event.stage != "RUN" and event.state == "completed"}
    current_index = keys.index(current) if current in keys else -1
    return [DisplayStep(key, stage_title(key),
                        "current" if key == current else
                        "done" if key in completed or (current_index >= 0 and index < current_index) else
                        "pending") for index, key in enumerate(keys)]


def event_sentence(event) -> str:
    title = stage_title(event.stage)
    state = state_title(event.state)
    source = source_title(getattr(event, "source", "local"))
    if event.detail:
        return f"{event.detail} ({source})"
    return f"{title}: {state} ({source})"
