"""Projekce potvrzených událostí; nezávislá na Qt a délce zobrazovaného logu."""

from collections import OrderedDict
from dataclasses import replace

from .progress import TERMINAL_RUN_STATES


ERRORS = {"failed", "error", "expired", "corrupt_state"}
BLOCKED = {"submission_unknown", "response_pending", "batch_pending", "unknown",
           "needs_clarification", "waiting_manual_resource"}
LABELS = {
    "RUN_CHECK": "Kontrola rozpracované práce",
    "RUN_INPUT": "Uložení nastavení a podkladů",
    "RUN_RUNTIME": "Příprava podkladů",
    "RUN_FINALIZE": "Ukončení práce a uložení stavu",
    "OPERATION": "Provedení operace",
    "BATCH_SUBMIT": "Příprava a odeslání hromadné úlohy",
    "KASKÁDA": "Postup posloupnosti úloh",
    "QA_INPUT": "Příprava podkladů k otázce",
    "QA_RESPONSE": "Získání odpovědi", "QA_VALIDATION": "Kontrola odpovědi",
    "QA": "Získání odpovědi", "QFILE_PLAN": "Návrh názvu a umístění souboru",
    "QFILE": "Vytvoření souboru", "Lokální validace": "Kontrola modelu a nastavení",
    "A0": "Příprava dlouhého zadání", "A0R": "Zjištění požadavků",
    "B0R": "Zjištění požadovaných změn", "A1": "Sestavení plánu", "B1": "Sestavení plánu změn",
    "A2_SPINE": "Návrh struktury výsledku", "B2_SPINE": "Návrh struktury změn",
    "A2_DETAIL": "Podrobnosti ke každému souboru", "B2_DETAIL": "Podrobnosti změn souborů",
    "A2": "Kontrola návazností plánu", "B2": "Kontrola návazností změn",
    "A2Q": "Nezávislé ověření plánu", "B2Q": "Nezávislé ověření změn",
    "A3": "Vytváření souborů", "B3": "Úprava souborů",
    "BATCH": "Zpracování hromadné úlohy", "Upload": "Nahrávání podkladů",
    "Download": "Stahování výsledků", "Ukládání": "Bezpečné uložení souborů",
    "Validace kontraktů": "Kontrola vytvořených souborů",
}
STATES = {
    "active": "Práce probíhá", "preparing": "Připravuje se", "running": "Práce probíhá",
    "created": "Práce je připravena", "waiting": "Čekáme na odpověď služby",
    "validating_result": "Kontrolujeme přijatý výsledek", "repairing": "Opravujeme výsledek",
    "completed": "Hotovo", "closed": "Práce je uzavřena", "failed": "Práci se nepodařilo dokončit",
    "error": "Práci se nepodařilo dokončit", "expired": "Vypršel čas služby",
    "cancelled": "Práce byla zastavena", "stopped": "Práce byla zastavena",
    "cancelling": "Čekáme na potvrzení zastavení",
    "submission_unknown": "Odeslání se nepodařilo potvrdit",
    "response_pending": "Čekáme na odpověď služby", "batch_pending": "Hromadná úloha ještě běží",
    "partial": "Dokončeno jen částečně", "completed_unverified": "Výsledek čeká na ověření",
    "files_complete_unverified": "Soubory čekají na závěrečné ověření",
    "dry_run": "Zkušební návrh je připraven bez zápisu",
    "plan_ready": "Plán je připraven; výroba nebyla spuštěna",
    "qfile_plan_ready": "Návrh souboru čeká na vaše potvrzení",
    "needs_clarification": "Je třeba upřesnit zadání",
    "waiting_manual_resource": "Čekáme na váš soubor", "corrupt_state": "Uložený stav nelze bezpečně načíst",
    "unknown": "Výsledek není potvrzen", "unfinished_record": "Závěr práce nebyl zaznamenán",
    "ready_to_import": "Výsledky čekají na převzetí", "importing": "Přebíráme výsledky",
    "batch_prepared": "Hromadná úloha je připravena",
}
PROVIDER = {
    "queued": "Požadavek čeká ve frontě", "in_progress": "Služba zpracovává požadavek",
    "validating": "Služba kontroluje zadání", "finalizing": "Služba dokončuje výstup",
    "completed": "Služba dokončila zpracování; místní převzetí se ověřuje samostatně",
    "failed": "Služba ohlásila chybu", "cancelled": "Služba potvrdila zrušení",
    "cancelling": "Služba vyřizuje zrušení", "expired": "Službě vypršel čas",
    "connection_error": "Spojení není dostupné; stav služby se nepodařilo aktualizovat",
}


def step_name(stage):
    if stage.startswith("Krok "):
        return stage
    if stage in LABELS:
        return LABELS[stage]
    # Neznámý interní identifikátor patří do technických podrobností.
    if "_" in stage or stage.isupper():
        return "Zpracování dílčího kroku"
    return stage


class ProgressModel:
    def __init__(self):
        self.steps = OrderedDict()
        self.last = None
        self.current = ""
        self.terminal = ""
        self.declared = False
        self.measurement = None
        self.received = False

    def update(self, event):
        if event.stage == "PLAN":
            self.declared = True
            for key in event.planned_steps:
                self.steps.setdefault(key, None)
            return
        alias = {"QA": "QA_RESPONSE", "A0R_REQUIREMENTS": "A0R", "B0R_REQUIREMENTS": "B0R",
                 "A2Q_QUALITY_GATE": "A2Q", "B2Q_QUALITY_GATE": "B2Q",
                 "Příprava BATCH": "BATCH_SUBMIT", "BATCH SUBMIT": "BATCH_SUBMIT"}.get(event.stage)
        if alias:
            event = replace(event, stage=alias)
        self.last = event
        if event.stage == "RUN":
            if event.state in TERMINAL_RUN_STATES:
                self.terminal = event.state
            return
        self.current = event.stage
        if event.stage not in self.steps:
            pending = next((key for key, value in self.steps.items() if value is None), None)
            if pending is not None:
                expanded = OrderedDict()
                for key, value in self.steps.items():
                    if key == pending:
                        expanded[event.stage] = event
                    expanded[key] = value
                self.steps = expanded
        self.steps[event.stage] = event
        if event.completed is not None and event.total is not None and event.total >= 0:
            self.measurement = event

    def rows(self):
        rows = []
        for key, event in self.steps.items():
            if event is None:
                state = "not_run" if self.terminal else "pending"
            elif event.state == "completed":
                state = "done"
            elif event.state in ERRORS or (key == self.current and self.terminal in ERRORS):
                state = "error"
            elif event.state in BLOCKED or (key == self.current and self.terminal in BLOCKED):
                state = "blocked"
            elif event.state == "skipped":
                state = "skipped"
            elif self.terminal:
                state = "unconfirmed"
            elif key == self.current:
                state = "current"
            else:
                state = "unconfirmed"
            rows.append((key, state))
        return rows


def step_states(events):
    model = ProgressModel()
    for event in events:
        model.update(event)
    return model.rows()
