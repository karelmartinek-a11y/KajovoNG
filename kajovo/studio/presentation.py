"""České popisky pro známé údaje; uživatelský obsah a uložené kódy se nemění."""

from kajovo.core.progress_model import STATES


MODES = {
    "GENERATE": "Vytvoření projektu", "MODIFY": "Úprava projektu",
    "QA": "Odpověď na dotaz", "QFILE": "Vytvoření jednoho souboru",
    "KASKADA": "Posloupnost kroků", "KASKÁDA": "Posloupnost kroků",
    "COMIC": "Komiks", "PHOTO": "Úprava fotografií", "BATCH": "Dávkové zpracování",
    "LIVE": "Průběžné zpracování",
}

REASONING = {
    "none": "Bez rozšířeného uvažování", "minimal": "Nejmenší", "low": "Nízká",
    "medium": "Střední", "high": "Vysoká", "xhigh": "Velmi vysoká",
}

FORMATS = {
    "auto": "Automaticky", "txt": "Prostý text (.txt)", "md": "Formátovaný text (.md)",
    "json": "Strukturovaná data (.json)", "toml": "Nastavení programu (.toml)",
    "yaml": "Nastavení programu (.yaml)", "csv": "Tabulka (.csv)",
    "html": "Webová stránka (.html)", "css": "Vzhled webové stránky (.css)",
    "js": "Program pro web (.js)", "ts": "Zdrojový kód TypeScript (.ts)",
    "py": "Program v Pythonu (.py)", "svg": "Vektorový obrázek (.svg)",
    "xml": "Strukturovaná data (.xml)", "png": "Obrázek PNG (.png)",
    "jpeg": "Fotografie JPEG (.jpeg)", "webp": "Obrázek WebP (.webp)",
}

COMIC_PRESETS = {
    "1:1": "Čtverec (1:1)", "4:3": "Obrázek na šířku (4:3)",
    "3:4": "Obrázek na výšku (3:4)", "16:9": "Široký obrázek (16:9)",
    "9:16": "Vysoký obrázek (9:16)", "A4": "Papír A4 (210 × 297 mm)",
    "A5": "Papír A5 (148 × 210 mm)", "A6": "Papír A6 (105 × 148 mm)",
    "DL": "Úzký papír DL (110 × 220 mm)",
}

FIELDS = {
    "last_error": "Poslední chyba", "validations": "Kontroly výsledku",
    "batch_errors": "Chyby dávek", "recovery_events": "Obnovení práce",
    "project": "Projekt", "run_id": "Číslo běhu", "status": "Stav",
    "mode": "Způsob práce", "created_at": "Vytvořeno", "finished_at": "Ukončeno",
    "input_summary": "Zadání", "output_summary": "Výsledek", "model_summary": "Modely",
    "title": "Krok", "stage": "Krok", "human_summary": "Shrnutí",
    "human_message": "Událost", "output_text": "Odpověď", "display_name": "Soubor",
    "size_bytes": "Velikost v bajtech", "mime_type": "Druh souboru",
    "path_in_bundle": "Umístění v záznamu běhu", "source_run_id": "Zdrojový běh",
    "target_run_id": "Navazující běh", "relation_type": "Druh návaznosti",
    "timestamp": "Čas", "severity": "Závažnost", "notes": "Poznámka",
    "text": "Výsledný text", "saved": "Uložené soubory", "saved_files": "Uložené soubory",
    "published_files": "Soubory předané do projektu", "staged_files": "Soubory připravené k převzetí",
    "missing_deliverables": "Chybějící výsledky", "questions": "Otázky k upřesnění",
    "question": "Otázka", "message": "Zpráva", "next_step": "Další postup",
    "error": "Chyba", "human_error": "Vysvětlení chyby", "path": "Soubor",
    "answer": "Odpověď", "result": "Výsledek", "data": "Obsah výsledku",
    "qfile_plan": "Návrh souboru", "proposed_path": "Navržený název a umístění",
    "format": "Druh souboru", "output_dir": "Složka pro výsledky",
    "dry_run": "Příprava bez zápisu", "file_contract_valid": "Kontrola formátu souboru",
    "human_verified": "Ověření člověkem", "failure_detail": "Podrobnosti chyby",
    "request_total": "Počet úloh", "request_completed": "Dokončené úlohy",
    "request_failed": "Neúspěšné úlohy", "completed": "Dokončeno", "failed": "Neúspěšné",
    "total": "Celkem", "request_counts": "Počty úloh", "summary": "Shrnutí",
    "git_result": "Výsledek práce s verzemi", "files": "Soubory", "root": "Složka projektu",
    "remote": "Vzdálená adresa", "tags": "Milníky", "description": "Popis",
}

VALUES = {
    **STATES, **MODES,
    "queued": "Čeká ve frontě", "in_progress": "Služba zpracovává úlohu",
    "validating": "Kontrolují se podklady", "finalizing": "Připravují se výsledky",
    "pending": "Čeká na zpracování", "submitted": "Odesláno službě",
    "downloaded": "Výsledky jsou uložené", "info": "Informace",
    "warning": "Upozornění", "clone": "Kopie zadání", "continue": "Pokračování",
    "repair": "Oprava", "rerun": "Nové spuštění", "reuse_artifacts": "Použití uložených souborů",
    "passed": "Kontrola prošla", "not_run": "Kontrola nebyla provedena",
    "blocked": "Chybí podmínky pro pokračování", "skipped": "Přeskočeno",
}

COMIC_KINDS = {
    "bible": "Sestavení pravidel komiksu", "story": "Vytvoření příběhu",
    "script": "Vytvoření scénáře", "storyboard": "Rozpis obrázků příběhu",
    "continuity": "Kontrola návaznosti", "character": "Vzorový obrázek postavy",
    "environment": "Vzorový obrázek prostředí", "panel": "Vytvoření obrázku",
    "panels": "Vytvoření obrázků", "edit": "Úprava kresby",
}

COMIC_STATES = {
    **VALUES, "prepared": "Připraveno", "received": "Převzato ze služby",
    "submitting": "Odesílá se", "retrieving": "Stahují se výsledky",
    "processing": "Zpracovává se", "interrupted": "Přerušeno",
}

COMIC_FIELDS = {
    "story": "Příběh", "title": "Název", "premise": "Základ příběhu",
    "synopsis": "Děj", "summary": "Shrnutí", "script": "Scénář",
    "scenes": "Scény", "scene_id": "Číslo scény", "description": "Popis",
    "dialogue": "Dialog", "dialogues": "Dialogy", "text": "Text",
    "speaker": "Mluvčí", "character_id": "Postava", "environment_id": "Prostředí",
    "characters": "Postavy", "panels": "Obrázky příběhu", "panel_id": "Číslo obrázku",
    "name": "Název", "prompt": "Zadání kresby", "visual_description": "Popis kresby",
    "status": "Výsledek kontroly", "issues": "Nalezené problémy",
    "message": "Zpráva", "reason": "Důvod", "notes": "Poznámky",
    "recommendations": "Doporučení", "rules": "Pravidla", "style": "Vzhled",
    "line": "Kresba čar", "color": "Barevnost", "palette": "Barvy",
    "typography": "Písmo", "balloons": "Textové bubliny", "sfx": "Zvukové nápisy",
    "continuity": "Návaznost příběhu", "layout": "Rozvržení", "consistency": "Jednotný vzhled",
    "art_direction": "Celkový výtvarný styl", "linework_rules": "Pravidla kresby čar",
    "color_rules": "Pravidla barev", "lighting_rules": "Pravidla osvětlení",
    "materials": "Materiály a povrchy", "character_consistency": "Jednotný vzhled postav",
    "environment_consistency": "Jednotný vzhled prostředí", "camera": "Pohled a záběr",
    "speech_balloons": "Dialogové bubliny", "captions": "Titulky a vyprávění",
    "negative_constraints": "Čemu se vyhnout", "identity_rules": "Rozpoznatelnost postav",
    "style_consistency": "Jednotný styl kresby", "beats": "Dějové okamžiky",
    "id": "Číslo položky", "purpose": "Účel", "beat_id": "Dějový okamžik",
    "location": "Místo", "time": "Čas", "action": "Děj scény", "entity_ids": "Postavy a prostředí",
    "position": "Pořadí", "shot": "Záběr", "visual": "Popis kresby", "caption": "Titulek",
    "approved_panel_ids": "Schválené obrázky", "severity": "Závažnost",
    "scope": "Dotčená část", "resolution": "Navržené řešení",
}


def comic_readable(value, key=""):
    if isinstance(value, dict):
        return "\n\n".join(
            f"{COMIC_FIELDS.get(name, 'Další údaj')}\n{comic_readable(content, name)}"
            for name, content in value.items()
        )
    if isinstance(value, (list, tuple)):
        return "\n\n".join(f"{index}. {comic_readable(item, key)}" for index, item in enumerate(value, 1)) or "Žádné položky."
    if isinstance(value, bool):
        return "Ano" if value else "Ne"
    if key == "status":
        return {"PASS": "Kontrola prošla", "pass": "Kontrola prošla", "FAIL": "Kontrola našla problémy", "needs_changes": "Je třeba opravit nalezené problémy"}.get(value, "Výsledek kontroly není znám")
    if key == "severity":
        return {"blocking": "Brání pokračování", "major": "Závažný problém", "minor": "Drobný problém"}.get(value, "Jiná závažnost")
    return str(value)


def image_size_name(value):
    if value == "auto":
        return "Automaticky"
    if isinstance(value, str) and "x" in value:
        width, height = value.split("x", 1)
        if width.isdigit() and height.isdigit():
            return f"{width} × {height} obrazových bodů"
    return str(value)


def git_status_readable(value):
    lines = []
    changes = 0
    for line in str(value).splitlines():
        if line.startswith("## "):
            branch = line[3:].split("...")[0]
            if branch.startswith("No commits yet on "):
                branch = branch.removeprefix("No commits yet on ")
                lines.append("Projekt zatím nemá uloženou verzi.")
            elif branch.startswith("HEAD (no branch)"):
                branch = "samostatná uložená verze"
            lines.append("Větev projektu: " + branch)
            if "[ahead " in line or "[behind " in line or ", behind " in line:
                lines.append("Místní a vzdálené verze se liší. Podrobnosti jsou v technickém záznamu.")
        elif len(line) >= 3 and line[:2] in {"??", "!!"}:
            if line[:2] == "??":
                lines.append("Nový soubor: " + line[3:])
                changes += 1
        elif len(line) >= 3 and all(char in " MADRCUT?!" for char in line[:2]):
            codes = line[:2]
            name = line[3:]
            title = "Odstraněný soubor" if "D" in codes else "Přejmenovaný soubor" if "R" in codes else "Nový soubor" if "A" in codes else "Změněný soubor"
            if "U" in codes or codes in {"AA", "DD"}:
                title = "Soubor s nevyřešeným konfliktem"
            lines.append(f"{title}: {name}")
            changes += 1
        else:
            lines.append(line)
    if str(value).startswith("##") and not changes:
        lines.append("Všechny současné změny jsou uložené ve verzích.")
    return "\n".join(lines)


def mode_name(value):
    return MODES.get(str(value), "Jiný druh zpracování")


def integrity_summary(value):
    if not isinstance(value, dict):
        return "Výsledek kontroly neporušenosti není znám."
    status = value.get("status")
    if status == "verified" and value.get("valid") is not False:
        return "Záznam práce je neporušený. Tato kontrola neposuzuje funkčnost vytvořených souborů."
    if status == "changed":
        return "Záznam práce je změněný nebo poškozený. Použijte původní záznam nebo obnovte jeho nepoškozenou kopii."
    if status == "legacy":
        return "Starší záznam nemá podklady potřebné pro ověření neporušenosti."
    if status == "unsealed":
        return "Záznam ještě není uzavřený. Jeho neporušenost zatím nelze potvrdit."
    return "Neporušenost záznamu nebyla potvrzena. Podrobnosti jsou ve výsledku kontroly."


def file_kind_name(mime):
    return {
        "text/plain": "Prostý text", "text/markdown": "Formátovaný text",
        "text/csv": "Tabulka", "text/html": "Webová stránka",
        "application/json": "Strukturovaná data", "application/pdf": "Dokument PDF",
        "application/zip": "Archiv souborů", "image/png": "Obrázek PNG",
        "image/jpeg": "Fotografie JPEG", "image/webp": "Obrázek WebP",
        "image/svg+xml": "Vektorový obrázek",
    }.get(mime, "Textový soubor" if mime.startswith("text/") else "Obrázek" if mime.startswith("image/") else "Jiný druh souboru")


def human_readable(value, key=""):
    """Shrne známé položky bez surového JSONu a bez domýšlení ověření."""
    if isinstance(value, dict):
        parts = []
        for name, content in value.items():
            if name not in FIELDS or content is None or content == "" or content == []:
                continue
            parts.append(f"{FIELDS[name]}\n{human_readable(content, name)}")
        return "\n\n".join(parts) or (
            "Podrobný záznam je dostupný po zapnutí technických podrobností."
        )
    if isinstance(value, (list, tuple)):
        return "\n\n".join(human_readable(item, key) for item in value) or "Žádné položky."
    if isinstance(value, bool):
        return "Ano" if value else "Ne"
    if key in {"status", "mode", "severity", "relation_type"}:
        return VALUES.get(str(value), "Neznámá hodnota; přesný údaj je v technických podrobnostech.")
    if key == "format":
        return FORMATS.get(str(value), str(value))
    if key == "stage":
        from kajovo.core.progress_model import step_name

        return step_name(str(value))
    return str(value)
