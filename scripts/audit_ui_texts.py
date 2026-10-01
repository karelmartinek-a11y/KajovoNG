"""Úplný soupis textových zdrojů, zobrazovacích míst a dohledatelných vazeb UI.

Výstup rozlišuje viditelné texty, technická hlášení a interní konstanty.
Statická vazba ani název testu se nevydává za důkaz provedené operace.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
import sys


SECTIONS = {
    "application": "Hlavní okno a výběr modelu", "workbench": "Zadání",
    "photos": "Fotografie", "comics": "Komiks", "comic_editor": "Komiks · kresba a texty",
    "cascades": "Posloupnosti úloh", "cascade_items": "Posloupnosti · vstupy a výstupy",
    "resources": "Podklady", "versions": "Verze projektu", "converter": "Převod textů",
    "settings": "Nastavení", "components": "Sdílené dialogy a prvky",
    "presentation": "České názvy údajů", "user_errors": "Chybová hlášení",
    "file_dialogs": "Výběr souborů a složek", "context": "Účet a podklady",
}
SINKS = {
    "caption": (0, "Popisek nebo informace"), "label": (0, "Popisek nebo informace"),
    "panel": (0, "Název oddílu"), "action": (1, "Tlačítko"),
    "button": (1, "Tlačítko"), "text": (1, "Pole formuláře"),
    "choice": (1, "Výběr z možností"), "check": (1, "Přepínač nastavení"),
    "add": (1, "Pole formuláře"), "QPushButton": (0, "Tlačítko"),
    "QCheckBox": (0, "Přepínač"), "QLabel": (0, "Popisek"),
    "QPlainTextEdit": (0, "Textový obsah"), "QListWidgetItem": (0, "Položka seznamu"),
    "QTableWidgetItem": (0, "Buňka tabulky"), "addTab": (1, "Záložka"),
    "addItem": (0, "Položka výběru"), "addItems": (0, "Položky výběru"),
    "addRow": (0, "Popisek pole"), "setText": (0, "Aktualizovaný text"),
    "setPlainText": (0, "Textový obsah"), "setHtml": (0, "Formátovaný obsah"),
    "appendPlainText": (0, "Záznam průběhu"), "drawText": (-1, "Kreslený text"),
    "setWindowTitle": (0, "Název okna"), "setToolTip": (0, "Nápověda po najetí myší"),
    "setApplicationName": (0, "Název aplikace"),
    "setPlaceholderText": (0, "Nápověda v prázdném poli"),
    "setAccessibleName": (0, "Název pro čtečku obrazovky"),
    "setAccessibleDescription": (0, "Vysvětlení pro čtečku obrazovky"),
    "setHorizontalHeaderLabels": (0, "Záhlaví tabulky"),
    "DetailDialog": (0, "Dialog"), "ValueDialog": (0, "Dialog zadání hodnoty"),
    "MultiProgressDialog": (0, "Průběh práce"), "OperationDialog": (0, "Průběh práce"),
    "getText": (1, "Dialog zadání textu"), "getMultiLineText": (1, "Dialog zadání delšího textu"),
    "getItem": (1, "Dialog výběru"), "warning": (1, "Upozornění"),
    "get_open_file_name": (1, "Dialog otevření souboru"),
    "get_open_file_names": (1, "Dialog otevření souborů"),
    "get_save_file_name": (1, "Dialog uložení souboru"),
    "get_existing_directory": (1, "Dialog výběru složky"),
    "start": (0, "Název zpracování"), "start_read": (0, "Název čtení"),
    "adopt": (0, "Název zpracování"), "confirm": (1, "Potvrzovací dialog"),
    "TextCard": (0, "Oddíl výsledku"), "EvidenceView": (0, "Oddíl výsledku"),
}
TECH_CALLS = {"json.dumps", "ast.unparse", "canonical", "canonical_bytes"}
MAPPING_NAMES = {"LABELS", "STATES", "VALUES", "NAMES", "ROW_TEXT", "STAGE_TITLES", "MODE_LABELS",
                 "REASONING", "FORMATS", "FIELDS", "PROVIDER", "MODES", "COMIC_FIELDS", "COMIC_KINDS",
                 "COMIC_STATES", "COMIC_ERRORS", "COMIC_PRESETS", "RELATIONS", "API_CODES", "HTTP_MESSAGES", "SYSTEM_CODES", "WINDOWS_CODES"}
FIELD_PURPOSES = {
    "project": "Pojmenuje práci a její uložené záznamy.", "mode": "Určí vytváření projektu, změnu projektu, odpověď nebo samostatný soubor.",
    "model": "Vybere model použitý při odeslání zadání službě.", "temperature": "Určí různorodost odpovědi, pouze když ji model podporuje.",
    "in_dir": "Určí vstupní soubory projektu. Úprava projektu vyžaduje existující složku.",
    "out_dir": "Určí složku pro výsledky. Jádro kontroluje bezpečnost cest a souběžné zápisy.",
    "in_equals_out": "Směřuje výsledky do vstupní složky místo samostatné výstupní složky.",
    "versing": "Před prací uloží kopii souborů pro návrat k původnímu obsahu.",
    "send_as_c": "Přepne souborovou část na vzdálenou dávku; příprava a místní převzetí jsou samostatné kroky.",
    "maximum_quality": "Přidá nezávislou kontrolu připraveného návrhu před výrobou souborů.",
    "stop_after_plan": "Ukončí práci po přípravě plánu před tvorbou souborů.",
    "dry_run": "Připraví návrh změn bez zápisu do výsledného projektu.",
    "response_id": "Uvede předchozí odpověď pro výslovně zapnuté navázání rozhovoru.",
    "qfile_output_path": "Určí relativní název a podsložku jediného výsledného souboru.",
    "qfile_output_format": "Určí očekávaný formát a příponu jediného souboru.",
    "qfile_suggest_path": "Spustí samostatný návrh názvu; výroba čeká na další výslovné spuštění uživatelem.",
    "qa_continue_conversation": "Předá předchozí odpověď službě jen po výslovném zapnutí této volby.",
    "source": "Určí, zda vstup pochází z textu, místního souboru, nahraného souboru nebo dřívějšího kroku.",
    "kind": "Určí text, strukturovanou odpověď, soubor nebo rozhodovací větev.",
    "json_schema": "Určí povinná pole strukturované odpovědi; nejde o volné uživatelské zadání.",
    "decision_options": "Propojí možné hodnoty rozhodnutí s následujícími kroky.",
    "name": "Pojmenuje vstup nebo výstup, který další kroky vybírají podle uložené identity.",
    "value": "Dodá vlastní text, cestu nebo číslo již nahraného souboru podle vybraného zdroje vstupu.",
    "source_step_id": "Vybere předchozí krok poskytující vstup; jádro kontroluje existenci a pořadí kroku.",
    "source_output_id": "Vybere výstup předchozího kroku; jádro ověřuje jeho identitu a kompatibilitu.",
    "file_type": "Určí očekávaný druh výsledného souboru, nikoli jeho obsah.",
    "file_name": "Určí relativní název souboru; jádro odmítá cestu mimo povolenou složku.",
    "file_mode": "Určí vytvoření souboru nebo úpravu existujícího vstupu.",
    "modify_input_id": "Vybere vstup upravovaného souboru. Používá se pouze pro úpravu souboru.",
    "models.search": "Omezí zobrazený katalog podle části názvu modelu; neodesílá pracovní zadání.",
    "models.compatible": "Zobrazí pouze modely s podporou textového a souborového pracovního postupu.",
    "models.batch": "Filtruje modely podporující vybraný druh vzdálené dávky.",
    "models.batch_endpoint": "Určí, pro který druh dávky se posuzuje podpora modelu; nejde o odeslání dávky.",
    "models.images": "Zobrazí modely s podporou obrázků na vstupu.",
    "photos.template": "Vloží uložené zadání úprav fotografií. Samotný výběr žádné fotografie neupravuje.",
    "photos.prompt_model": "Vybere textový model pro placené vylepšení zadání, nikoli model úpravy obrázku.",
    "photos.image_model": "Vybere model použitý pro placenou úpravu fotografií.",
    "photos.quality": "Určí požadovanou kvalitu úpravy a předá původní kód poskytovateli služby.",
    "photos.size": "Určí výsledné rozměry obrázku; povolené možnosti vycházejí z podpory vybraného modelu.",
    "photos.format": "Určí formát ukládaných obrázků. České označení nemění kód png, jpeg nebo webp.",
    "photos.output": "Určí složku pro převzaté fotografie; stažení ověřuje a atomicky ukládá výsledky.",
    "settings.api_key": "Připraví přístupový klíč; aktivní účet se změní až po ověřeném trvalém uložení.",
    "git.root": "Vybere projekt, jehož soubory a uložené verze se budou číst nebo měnit.",
    "git.remote": "Určí vzdálený repozitář pro výslovné odesílání a stahování verzí.",
    "cascade.name": "Pojmenuje uloženou posloupnost kroků.",
    "cascade.project": "Pojmenuje spuštěnou práci a její záznam.",
    "cascade.input": "Vybere složku podkladů posloupnosti; validátor kontroluje její použitelnost.",
    "cascade.output": "Vybere složku výsledků posloupnosti; jádro kontroluje cesty a souběžný zápis.",
    "cascade.step.title": "Pojmenuje krok v přehledu a v průběhu práce.",
    "cascade.step.model": "Vybere model použitý při provedení tohoto kroku.",
    "cascade.step.context": "Určí převzetí kontextu předchozích kroků do aktuálního požadavku.",
    "cascade.step.conversation": "Určí pokračování vzdáleného rozhovoru; kontroluje se návaznost odpovědi.",
    "cascade.step.deterministic": "Požaduje podporované nastavení omezené náhodnosti pro daný model.",
    "cascade.step.default_temperature": "Zvolí výchozí různorodost odpovědi místo vlastní hodnoty.",
    "cascade.step.temperature": "Určí různorodost odpovědi, když není použitá výchozí hodnota a model ji podporuje.",
    "comic_library_dir": "Určí složku místní knihovny komiksů včetně její databáze a obrázků.",
    "log_dir": "Určí složku kanonických záznamů pracovních běhů a historie.",
    "cache_dir": "Určí složku dočasných dat, katalogu modelů a místních šablon.",
    "batch_poll_interval_s": "Určí prodlevu mezi dotazy na vzdálený stav dávky.",
    "batch_timeout_s": "Určí konec místního sledování; vzdálenou dávku tím nezruší.",
    "response_timeout_s": "Určí časový limit jednotlivého síťového požadavku.",
    "response_poll_timeout_s": "Určí limit sledování již odeslané odpovědi; nejistý výsledek se nevydává za bezpečné nové odeslání.",
    "default_model": "Určí model pro nové zadání, pokud je dostupný účtu a podporuje vybranou práci.",
    "default_temperature": "Určí výchozí různorodost odpovědi; validátor povoluje rozsah 0 až 2.",
    "dry_run_modify": "Předvolí přípravu změn bez zápisu pro nové úpravy projektu.",
    "ui_reduced_motion": "Omezí animace rozhraní; počet potvrzených kroků a skutečné zpracování nemění.",
    "max_attempts": "Omezí počet opakování podporovaných bezpečně opakovatelných požadavků.",
    "base_delay_s": "Určí první čekání před opakováním požadavku.",
    "max_delay_s": "Omezí nejdelší čekání mezi opakováními.",
    "jitter_s": "Přidá časový rozptyl k prodlevě mezi opakováními.",
    "circuit_breaker_failures": "Určí počet po sobě jdoucích chyb před dočasným pozastavením požadavků.",
    "circuit_breaker_cooldown_s": "Určí trvání dočasného pozastavení požadavků.",
    "allow_upload_sensitive": "Výslovně dovolí odeslání vstupů zachycených kontrolou citlivých názvů či obsahu; ostatní filtry platí dál.",
    "deny_extensions_in": "Vynechá vstupní soubory se zakázanými příponami.",
    "allow_extensions_in": "Omezí vstupní soubory na povolené přípony; prázdný seznam znamená bez tohoto omezení.",
    "deny_globs_in": "Vynechá vstupní cesty odpovídající zadaným vzorům.",
    "allow_globs_in": "Povolí vstupní cesty podle zadaných vzorů; prázdný seznam znamená bez tohoto omezení.",
    "host": "Určí adresu poštovního serveru nebo vzdáleného počítače podle otevřeného oddílu.",
    "port": "Určí síťový port dané služby; konfigurace ověřuje rozsah 1 až 65535.",
    "username": "Určí přihlašovací jméno k poštovnímu serveru.",
    "password": "Připraví heslo pro bezpečné uložení; v běžném zobrazení jsou znaky skryté.",
    "use_tls": "Zapne zabezpečení po navázání poštovního spojení; není slučitelné se současnou volbou SSL.",
    "use_ssl": "Zapne zabezpečení od začátku poštovního spojení; není slučitelné se současnou volbou TLS.",
    "from_email": "Určí adresu odesílatele oznámení a zkušební zprávy.",
    "to_email": "Určí adresu příjemce oznámení a zkušební zprávy.",
    "user": "Určí přihlašovací jméno ke vzdálenému počítači.",
    "key": "Určí soubor přihlašovacího klíče ke vzdálenému počítači.",
    "pin_required": "Vyžaduje ověření známého otisku vzdáleného počítače před diagnostikou.",
}
HANDLER_PURPOSES = {
    "start": "Zkontroluje zadání a zahájí jeho zpracování; u pracovních požadavků může následovat placená služba.",
    "refresh": "Načte aktuální přehled dané oblasti a zobrazí potvrzený výsledek.",
    "save": "Uloží připravené údaje po kontrole jejich platnosti.", "load": "Načte uživatelem vybraný soubor a zkontroluje jeho obsah.",
    "attach": "Připojí vybrané podklady k zadání; samotný soubor ve službě nemění.",
    "detach": "Odpojí vybrané podklady od zadání a zachová soubory ve službě.",
    "delete": "Po potvrzení odstraní vybrané záznamy nebo vzdálené podklady podle příslušné sekce.",
    "request_stop": "Vyžádá bezpečné zastavení a čeká na potvrzení pracovního procesu.",
    "show_result": "Otevře čitelné shrnutí výsledku; úplné údaje jsou dostupné v podrobnostech.",
    "show_details": "Otevře vysvětlení chyby, další postup a volitelné technické podrobnosti.",
    "save_key": "Ověří trvalé uložení přístupového klíče před jeho aktivací.",
    "test_mail": "Výslovně odešle zkušební zprávu podle právě zadaného nastavení.",
    "publish_staged": "Převezme připravené soubory po kontrole cílových změn; tím nepotvrzuje jejich funkčnost.",
    "complete_batch": "Převezme výsledky původní dávky bez nového odeslání zadání.",
    "clone": "Otevře kopii uloženého zadání pro další samostatnou práci.",
    "validate_panel": "Zkontroluje místní zadání komiksu bez generativního volání služby.",
    "accept": "Potvrdí údaje nebo souhlas dialogu; konkrétní následná operace je v jeho volajícím místě.",
    "reject": "Zavře dialog bez přijetí připravené změny.",
    "submit": "Zkontroluje a převede údaje dialogu; neplatný zápis se nepřijme.",
    "reset": "Vyčistí pracovní formulář dané sekce; uložené výsledky nemaže.",
    "browse": "Otevře výběr místní složky nebo souboru a nastaví vybranou cestu.",
    "pick_files": "Přidá vybrané místní fotografie do seznamu podkladů; placenou úpravu zatím neodesílá.",
    "pick_folder": "Vyhledá podporované fotografie ve vybrané složce a vloží je do seznamu podkladů.",
    "pick_output": "Vybere složku pro uložení upravených fotografií.",
    "remove": "Odebere vybrané fotografie ze zadání; původní soubory na disku nemaže.",
    "improve": "Odešle placené textové zadání k vylepšení; příliš pozdní odpověď nabídne jako odložený návrh.",
    "restore_prompt": "Vrátí původní uživatelské zadání před vylepšením.",
    "use_pending": "Převezme již získaný návrh zadání do aktuálního formuláře bez nového požadavku službě.",
    "discard_pending": "Zahodí odložený návrh zadání a zachová současný text.",
    "save_template": "Uloží současné zadání do místní knihovny šablon.",
    "update_template": "Přepíše vybranou místní šablonu současným zadáním po kontrole výběru.",
    "duplicate_template": "Vytvoří samostatnou místní kopii vybrané šablony.",
    "delete_template": "Po potvrzení odstraní vybranou místní šablonu; výstupní fotografie nemění.",
    "refresh_jobs": "Načte místní a vzdálený stav úprav fotografií; neodesílá nové zadání úprav.",
    "toggle_key": "Přepne skrytí znaků klíče v tomto formuláři; uložený klíč ani účet nemění.",
    "remove_key": "Po potvrzení odstraní trvale uložený klíč, ověří výsledek a vyčistí aktivní účet.",
    "select": "Přenese vybraný model do formuláře Zadání.",
    "set_default": "Uloží vybraný podporovaný model jako výchozí a obnoví související formuláře.",
    "details": "Zobrazí informace o vybrané položce; zobrazení nemění její obsah.",
    "detach_page": "Přesune nynější sekci do samostatného okna; zavřením ji vrátí do hlavního okna.",
    "toggle_navigation": "Zobrazí nebo skryje seznam sekcí v úzkém okně.",
    "open_converter": "Otevře samostatný místní převodník textových souborů se zálohou.",
    "open_detail": "Otevře detail vybraného uloženého zpracování; původní záznam se čte bez přepisu.",
    "show_more": "Otevře nabídku dalších dostupných akcí pro vybraný záznam.",
    "reset_filters": "Zruší omezení přehledu historie; uložené záznamy nemění.",
    "set_zoom": "Změní měřítko časové osy; žádnou pracovní operaci nespouští.",
    "fit_tracks": "Přizpůsobí šířku časových stop dostupnému místu.",
    "verify": "Zkontroluje neporušenost uloženého záznamu podle kontrolních otisků; funkčnost vytvořeného programu neposuzuje.",
    "open_bundle": "Otevře složku uloženého záznamu v systémovém správci souborů.",
    "export_bundle": "Vytvoří odvozenou kopii záznamu v archivu s odstraněnými citlivými údaji; původní záznam nepřepíše.",
    "open_batch": "Přejde na související vzdálenou dávku nebo nabídne její místní převzetí.",
    "open_parent": "Vybere v historii původní záznam, ze kterého tato práce vznikla.",
    "open_comic": "Přejde na související komiksovou operaci.",
    "attach_manual_resource": "Vybere chybějící místní podklad a připojí ho k uloženému zpracování po ověření identity.",
    "choose_reusable_clone": "Nabídne konkrétní uložený soubor jménem a vytvoří zadání s jeho ověřenou kopií.",
    "check_outputs": "Porovná nynější výsledné soubory s evidovanými kontrolními otisky.",
    "open_evidence": "Otevře oddělené technické záznamy požadavků, odpovědí, kontrol a návazností.",
    "pdf_page": "Změní zobrazovanou stránku PDF; uložený dokument se nemění.",
    "pdf_fit": "Přizpůsobí zvětšení PDF tak, aby byla viditelná celá stránka.",
    "pdf_zoom": "Změní velikost náhledu PDF; uložený dokument se nemění.",
    "copy_text": "Zkopíruje text ověřeného souboru do systémové schránky.",
    "save_text": "Uloží textovou kopii ověřeného souboru mimo původní záznam.",
    "metadata": "Přepne náhled na přesné technické údaje vybraného souboru.",
    "compare": "Porovná dvě vybrané ověřitelné místní textové přílohy; soubory se nemění.",
    "open": "Otevře ověřenou místní přílohu v příslušném systémovém programu.",
    "create_project": "Vytvoří místní komiksový projekt po zadání názvu.",
    "duplicate_project": "Vytvoří oddělenou místní kopii komiksového projektu.",
    "trash_project": "Přesune projekt do místního koše nebo obnoví již vybraný projekt z koše.",
    "refresh_projects": "Načte místní knihovnu projektů a obnoví výběr.",
    "save_style": "Uloží pravidla vzhledu projektu a zachová kanonické hodnoty pro vykreslení bublin.",
    "generate_bible": "Odešle placenou úlohu sestavení pravidel vzhledu a návaznosti komiksu.",
    "generate_story": "Odešle placenou textovou úlohu vytvoření příběhu projektu.",
    "generate_script": "Odešle placenou textovou úlohu vytvoření scénáře z příběhu.",
    "generate_storyboard": "Odešle placenou textovou úlohu vytvoření rozpisu jednotlivých obrázků příběhu.",
    "generate_continuity": "Odešle placenou kontrolu návaznosti uloženého příběhu a obrázků.",
    "materialize_storyboard": "Převede již vytvořený rozpis na místní obrázkové položky; nové generování služby nespouští.",
    "add_entity": "Vytvoří místní postavu nebo prostředí podle aktuálního oddílu.",
    "edit_entity": "Upraví místní popis vybrané postavy nebo prostředí.",
    "archive_entity": "Přepne u vybrané postavy či prostředí uložení do archivu nebo obnovení.",
    "generate_entity": "Odešle placené vytvoření vzorového obrázku postavy nebo prostředí.",
    "show_entity": "Zobrazí uložený popis a vzorové obrázky vybrané položky.",
    "add_entity_refs": "Připojí vybrané místní vzorové obrázky k postavě nebo prostředí.",
    "remove_entity_refs": "Odebere vybrané odkazy na vzorové obrázky z položky.",
    "add_style_refs": "Připojí vybrané obrázky jako podklady požadovaného vzhledu.",
    "remove_style_refs": "Odebere vybrané podklady vzhledu z projektu.",
    "add_panel": "Vytvoří novou místní obrázkovou položku příběhu.",
    "panel_action": "Podle uvedeného argumentu změní pořadí, vytvoří kopii nebo odstraní obrázkovou položku.",
    "insert_token": "Vloží do zadání atomický odkaz na vybranou postavu nebo prostředí; název zůstává čitelný.",
    "save_panel": "Uloží místní zadání, formát a textové vrstvy po kontrole aktuální revize.",
    "generate_panels": "Odešle placenou tvorbu označených obrázků s uloženými podklady a pravidly.",
    "edit_panel": "Odešle placenou změnu kresby vybraného obrázku s uvedeným zadáním.",
    "restore_version": "Vrátí vybranou dříve uloženou kresbu; samotnou placenou úpravu neopakuje.",
    "export_panel": "Vytvoří výsledný soubor obrázku s vykreslenými místními textovými vrstvami.",
    "job_details": "Otevře uložený výsledek nebo podrobnosti vybrané komiksové úlohy.",
    "cancel_selected": "Vyžádá zastavení vybrané komiksové úlohy; potvrzení služby se ověřuje samostatně.",
    "resume_selected": "Obnoví sledování nebo převzetí existující úlohy podle jejího uloženého stavu.",
    "retry_selected": "Odešle novou placenou kopii neúspěšné komiksové úlohy po výslovném potvrzení.",
    "add_step": "Vytvoří nový místní krok posloupnosti úloh.",
    "duplicate_step": "Vloží samostatnou kopii zvoleného kroku posloupnosti.",
    "remove_step": "Odstraní zvolený krok z připravované posloupnosti; uložené běhy nemění.",
    "move_step": "Posune zvolený krok o uvedený počet míst a znovu sestaví pořadí.",
    "commit_step": "Zapíše formulář kroku do právě připravované definice posloupnosti.",
    "edit_item": "Otevře definici vstupu či výstupu kroku; argument new určuje vytvoření nové položky.",
    "remove_item": "Odstraní zvolený vstup nebo výstup připravovaného kroku.",
    "edit_contract": "Otevře pokročilý technický zápis vstupů a výstupů a kontroluje jej před přijetím.",
    "definition_options": "Otevře pokročilé vlastnosti celkové definice posloupnosti.",
    "upload": "Nahraje uživatelem vybrané soubory do účtu služby po kontrole vstupních podmínek.",
    "create_store": "Vytvoří vzdálenou knihovnu dokumentů s uživatelem zadaným názvem.",
    "refresh_store_files": "Načte soubory právě vybrané vzdálené knihovny; opožděná odpověď nesmí změnit jiný výběr.",
    "add_store_files": "Připojí ke knihovně existující vzdálené soubory podle zadaných čísel.",
    "add_selected_files": "Připojí ke knihovně soubory vybrané v přehledu nahraných souborů.",
    "remove_store_files": "Po potvrzení odebere vybrané vazby souborů ke knihovně.",
    "attributes": "Otevře pokročilý technický zápis doplňujících údajů souboru v knihovně.",
    "set_remote": "Uloží vzdálenou adresu do nastavení verzovacího systému projektu.",
    "synchronize": "Podle argumentu odešle uložené verze do vzdáleného projektu nebo z něj stáhne změny.",
    "create_tag": "Uloží pojmenovaný milník projektu prostřednictvím místního verzovacího systému.",
    "delete_tag": "Po potvrzení odstraní vybraný místní milník.",
    "restore_tag": "Po potvrzení obnoví soubory ze zvoleného místního milníku.",
    "remove_repository": "Po potvrzení odstraní místní historii verzí projektu; jeho pracovní soubory zachová.",
    "open_file": "Otevře vybraný místní soubor k úpravě nebo porovnání s vybraným milníkem.",
    "save_file": "Uloží otevřený soubor po kontrole, že projekt a výběr mezitím zůstaly stejné.",
    "show_technical_status": "Otevře původní úplný stav verzovacího systému pro pokročilou diagnostiku.",
    "open_selected": "Otevře nebo obnoví průběh vybrané práce ze záznamu tohoto okna.",
    "select_page": "Zobrazí zvolenou hlavní sekci a její ovládání. Samotné přepnutí sekce pracovní požadavek neodesílá.",
}


def expression(node):
    return ast.unparse(node) if node is not None else ""


def context(node, parents):
    cls = method = ""
    current = node
    while current in parents:
        current = parents[current]
        if not method and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method = current.name
        if isinstance(current, ast.ClassDef):
            cls = current.name
            break
    return cls, method


def template(node):
    if isinstance(node, ast.Constant):
        return node.value
    return "".join(part.value if isinstance(part, ast.Constant) else "{" + expression(part.value)
                   + ("!" + chr(part.conversion) if part.conversion != -1 else "")
                   + (":" + template(part.format_spec) if part.format_spec else "") + "}"
                   for part in node.values)


def ancestry(node, parents):
    path = [node]
    while path[-1] in parents:
        path.append(parents[path[-1]])
    return path


def variable_sink(nodes, name):
    for root in nodes:
        for call in ast.walk(root):
            if not isinstance(call, ast.Call):
                continue
            function = expression(call.func).split(".")[-1]
            if function not in SINKS or not call.args:
                continue
            expected, kind = SINKS[function]
            index = expected if expected >= 0 else len(call.args) - 1
            if index < len(call.args) and isinstance(call.args[index], ast.Name) and call.args[index].id == name:
                return kind, call
    return None


def indirect_sink(node, parents):
    path = ancestry(node, parents)
    for owner in path[1:]:
        if isinstance(owner, ast.For) and owner.iter in path:
            group = parents.get(node)
            if isinstance(owner.target, (ast.Tuple, ast.List)) and isinstance(group, (ast.Tuple, ast.List)):
                if not isinstance(owner.iter, (ast.Tuple, ast.List)) or group not in owner.iter.elts:
                    continue
                index = group.elts.index(node)
                target = owner.target.elts[index] if index < len(owner.target.elts) else None
            else:
                target = owner.target
            if isinstance(target, ast.Name):
                found = variable_sink(owner.body, target.id)
                if found:
                    return found
        if isinstance(owner, ast.comprehension) and owner.iter in path and isinstance(owner.target, ast.Name):
            container = parents.get(owner)
            if isinstance(container, ast.ListComp) and isinstance(container.elt, ast.Tuple):
                first = container.elt.elts[0] if container.elt.elts else None
                if isinstance(first, ast.Name) and first.id == owner.target.id:
                    return "Možnost výběru", None
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = owner.args
            for argument, default in zip((args.posonlyargs + args.args)[-len(args.defaults):], args.defaults):
                if node is default and argument.arg in {"title", "caption", "label", "text", "stop_label"}:
                    found = variable_sink(owner.body, argument.arg)
                    if found:
                        return found
    return None


def classify(node, parents, studio, filename):
    path = ancestry(node, parents)
    if studio and (found := indirect_sink(node, parents)):
        kind, sink = found
        return "UI", kind, sink
    for parent in path[1:]:
        if isinstance(parent, ast.keyword) and parent.arg in {"identifier", "object_name", "objectName"}:
            return "Interní údaj", "Identifikátor ovládacího prvku nebo operace", None
        if isinstance(parent, ast.Call):
            name = expression(parent.func).split(".")[-1]
            if name == "get" and parent.args and node is parent.args[0]:
                return "Interní údaj", "Klíč datového pole", None
            if name in {"setObjectName", "setStyleSheet", "setProperty", "setData", "setOrganizationName"}:
                return "Interní údaj", "Technická vlastnost ovládacího prvku", None
            if (not studio and name != "setApplicationName") or name not in SINKS or not parent.args:
                continue
            child = path[path.index(parent) - 1]
            if child not in parent.args:
                continue
            index = parent.args.index(child)
            expected, kind = SINKS[name]
            visible = index == (expected if expected >= 0 else len(parent.args) - 1)
            if name in {"text", "choice", "check", "add", "button", "action"} and index == 0:
                return "Interní údaj", "Identifikátor ovládacího prvku", parent
            if name in {"addItem", "addTab"} and index != expected:
                return "Interní údaj", "Hodnota předávaná zpracování", parent
            if name in {"start", "start_read", "adopt"} and index != expected:
                return "Interní údaj", "Provozní argument operace", parent
            if visible or name in {"DetailDialog", "ValueDialog", "confirm", "getText", "getMultiLineText", "getItem"} and index in {1, 2}:
                return "UI", kind, parent
            if name in {"choice", "text"} and index == 2:
                if isinstance(parents.get(node), ast.Tuple) and parents[node].elts[-1] is node:
                    return "Interní údaj", "Hodnota předávaná zpracování", parent
                return "UI", "Možnost výběru nebo výchozí obsah", parent
        if isinstance(parent, ast.FormattedValue):
            return "Interní údaj", "Součást výrazu proměnlivého textu", None
    for parent in path[1:]:
        if isinstance(parent, ast.Dict):
            if node in parent.keys:
                return "Interní údaj", "Název datového pole nebo kód", None
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if (studio or filename in {"progress_model.py", "user_errors.py"}) and any(
                expression(target) in MAPPING_NAMES for target in targets
            ):
                if isinstance(parents.get(node), ast.Tuple) and parents[node].elts[-1] is node and filename == "workbench.py":
                    return "Interní údaj", "Kód způsobu práce", None
                return "UI", "Český název stavu, údaje nebo možnosti", None
    text = template(node)
    if any(isinstance(parent, ast.Raise) for parent in path):
        return "Technické podrobnosti", "Podrobnosti příčiny chyby", None
    if re.search(r"[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]", text) and not re.search(r"(def |class |import |\{.*:.*;|re\.compile)", text):
        return ("UI" if studio or filename == "user_errors.py" else "Technické podrobnosti"), "Text odvozený z dat nebo hlášení", None
    if isinstance(node, ast.JoinedStr) and studio:
        return "UI", "Šablona proměnlivého textu", None
    return "Interní údaj", "Datová konstanta nebo technický kód", None


def source_files(root):
    for directory in ("kajovo/studio", "kajovo/core", "kajovo/app", "utf8nobom"):
        for path in sorted((root / directory).rglob("*.py")):
            if not path.name.startswith("._") and path.name != "ui_audit.py":
                yield path


def collect(root):
    records, methods, files, views = [], {}, [], []
    for path in source_files(root):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        relative = path.relative_to(root).as_posix()
        studio = "/studio/" in relative
        files.append({"soubor": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cls, _ = context(node, parents)
                calls = [expression(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)]
                methods[(relative, cls, node.name)] = {"line": node.lineno, "calls": sorted(set(calls))}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and studio:
                bases = [expression(base) for base in node.bases]
                if any(base in {"QDialog", "QMainWindow", "QWidget", "QFileDialog", "QPlainTextEdit", "QTextEdit"} for base in bases):
                    views.append({"třída": node.name, "soubor": relative, "řádek": node.lineno, "druh": ", ".join(bases)})
            if not isinstance(node, (ast.Constant, ast.JoinedStr)):
                continue
            if isinstance(node, ast.Constant) and (not isinstance(node.value, str) or not node.value.strip()):
                continue
            if isinstance(parents.get(node), (ast.JoinedStr, ast.Expr)):
                continue
            cls, method = context(node, parents)
            scope, kind, sink = classify(node, parents, studio, path.name)
            handler = ""
            field = ""
            control = ""
            if sink is not None:
                name = expression(sink.func).split(".")[-1]
                if name in {"action", "button"} and len(sink.args) > 2:
                    handler = expression(sink.args[2])
                    control = expression(sink.args[0]).strip("'\"")
                if name in {"text", "choice", "check", "add"} and sink.args:
                    field = expression(sink.args[0]).strip("'\"")
            if not handler and isinstance(parents.get(node), ast.Tuple):
                group = parents[node]
                callbacks = [part for part in group.elts if isinstance(part, ast.Attribute)
                             and isinstance(part.value, ast.Name) and part.value.id == "self"
                             and (relative, cls, part.attr) in methods]
                if len(callbacks) == 1:
                    handler = expression(callbacks[0])
            if path.name == "settings.py":
                parent = parents.get(node)
                if isinstance(parent, ast.Dict) and node in parent.values:
                    index = parent.values.index(node)
                    name = parent.keys[index]
                    if isinstance(name, ast.Constant) and name.value in FIELD_PURPOSES:
                        field = name.value
            records.append({
                "text": template(node), "soubor": relative, "řádek": node.lineno,
                "třída": cls, "metoda": method, "oblast": scope, "druh": kind,
                "sekce": "Historie" if path.stem.startswith("history") else "Průběh práce" if "progress" in path.stem or path.stem == "operations" else SECTIONS.get(path.stem, "Zpracování · " + path.stem),
                "výraz": expression(sink or node), "pole": field, "obsluha": handler, "ovladač": control,
                "podmínky_zobrazení": "\n".join(expression(parent.test) for parent in ancestry(node, parents)
                                               if isinstance(parent, (ast.If, ast.IfExp))),
                "proměnlivý": isinstance(node, ast.JoinedStr),
            })
    records.sort(key=lambda row: (row["soubor"], row["řádek"], row["text"]))
    return records, methods, files, views


def enrich(records, methods, root):
    test_sources = []
    for path in sorted((root / "tests").glob("test_*.py")):
        if path.name.startswith("._"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {expression(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                attrs = {call.attr for call in ast.walk(node) if isinstance(call, ast.Attribute)}
                test_sources.append((path.relative_to(root).as_posix() + "::" + node.name, imports, attrs))
    traces = {}
    for number, row in enumerate(records, 1):
        row["id"] = f"T{number:05d}"
        method = row["metoda"]
        handler = row["obsluha"]
        targets = re.findall(r"self\.([a-zA-Z_]\w*)\(", handler)
        handler_name = handler.removeprefix("self.") if handler.startswith("self.") and "(" not in handler else targets[0] if targets else method
        key = (row["soubor"], row["třída"], handler_name)
        visited, pending, calls, locations = set(), [key, *((row["soubor"], row["třída"], target) for target in targets)], set(), []
        if key in traces:
            locations, calls = traces[key]
            pending = []
        while pending:
            current = pending.pop()
            if current in visited or current not in methods:
                continue
            visited.add(current)
            record = methods[current]
            locations.append(f"{current[0]}:{record['line']} · {current[1]}.{current[2]}")
            for call in record["calls"]:
                if call.startswith("self.") and call.count(".") == 1:
                    pending.append((current[0], current[1], call.split(".")[-1]))
                elif call.startswith(("client.", "self.client.", "self.service.", "service.", "photo_batch.", "self.context.operations.", "self.launcher.", "self.jobs.", "self.guard.", "adapter.", "self.context.")):
                    calls.add(call)
        traces[key] = locations, calls
        row["implementace"] = "\n".join(locations)
        row["navazující_volání"] = "\n".join(sorted(calls))
        module = row["soubor"].removesuffix(".py").replace("/", ".")
        tests = [name for name, imports, attrs in test_sources if any(module in item for item in imports)
                 and handler_name in attrs and handler_name not in {"__init__", "render"}]
        row["testy"] = "\n".join(tests)
        row["ověření"] = ("Dohledaná obsluha a příbuzné regresní testy. Rozsah a výsledek spuštění jsou v protokolu ověření. Samotný název testu nedokazuje tento jednotlivý text."
                           if tests else "Dohledání ve zdrojovém kódu. Pro tento jednotlivý text není doložen samostatný funkční test.")
        description = FIELD_PURPOSES.get(row["pole"], HANDLER_PURPOSES.get(handler_name, ""))
        if not description:
            description = ("Zobrazuje uvedenou informaci; sám text žádnou operaci nespouští."
                           if not handler else "Spustí uvedenou obsluhu. Přesná navazující volání a podmínky jsou uvedeny v implementaci.")
        row["účel_a_dopad"] = description
        row["mění_proces"] = "Ano, při aktivaci ovladače" if handler else "Nastavuje další zpracování" if row["pole"] else "Ne, pouze zobrazení"
        row["popis_obsahu"] = f"{row['druh']} v oblasti {row['sekce']}. " + (
            "Pevné znění je doplněné hodnotami v uvedených složených závorkách." if row["proměnlivý"] else "Zobrazuje přesně uvedené znění."
        )
        row["srozumitelnost"] = ("Interní údaj, nikoli samostatný uživatelský popisek."
            if row["oblast"] == "Interní údaj" else "Technické znění není určeno k samostatnému rozhodování základního uživatele. Zobrazuje se jako obsah podkladu nebo volitelné podrobnosti; uživatelská chyba má české shrnutí a další postup."
            if row["oblast"] == "Technické podrobnosti" else "Pokročilé nastavení je výslovně označené a popisuje účel i kontrolu zápisu."
            if "technický zápis" in row["text"].lower() or "JSON" in row["text"] else
            "Symbol potřebuje okolní popisek nebo dostupnou nápovědu; změna měřítka musí být slovně označená."
            if row["text"] in {"+", "−", "↑", "↓", "— / —"} else
            "Jméno služby, modelu nebo formátu je vlastní název. Okolní český popisek vysvětluje, k čemu se vybírá; znalost anglického významu není potřebná."
            if re.search(r"OpenAI|Git|Python|TypeScript|PNG|JPEG|WebP|UTF-8|PDF|SVG|HTML|CSV|TXT|gpt-", row["text"]) else
            "Proměnlivý obsah: pevná česká část vysvětluje význam, skutečnou hodnotu dodává uživatel, soubor nebo služba. Obsah dodaných dat nelze předem prohlásit za srozumitelný."
            if row["proměnlivý"] else
            "Ovladač používá české sloveso a jmenuje cílovou činnost. Přesný dopad a podmínky jsou v sousedních sloupcích."
            if row["obsluha"] else
            "Český název, údaj nebo vysvětlení v uvedeném kontextu. Jde o odborné posouzení; nebyl proveden výzkum s běžnými uživateli.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    records, methods, files, views = collect(root)
    enrich(records, methods, root)
    target = Path(args.output).resolve()
    target.mkdir(parents=True, exist_ok=True)
    result = {"schema": "kajovo.ui-texts.v1", "kořen": str(root), "soubory": files,
              "počty": dict(Counter(row["oblast"] for row in records)), "pohledy": views,
              "texty": [row for row in records if row["oblast"] != "Interní údaj"],
              "interní_údaje": [row for row in records if row["oblast"] == "Interní údaj"],
              "omezení": "Soupis pevných textů a šablon programu. Obsah uživatele, názvy jeho souborů a odpovědi služby jsou proměnlivá data. Vazba ani snímek neprokazují úspěch živé služby."}
    (target / "inventar.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = ["id", "text", "sekce", "druh", "soubor", "řádek", "třída", "metoda", "popis_obsahu",
               "účel_a_dopad", "mění_proces", "ovladač", "obsluha", "podmínky_zobrazení", "implementace", "navazující_volání", "testy", "ověření", "srozumitelnost", "oblast", "výraz"]
    with (target / "texty.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["texty"])
    print(json.dumps({"soubory": len(files), "texty": len(result["texty"]), "počty": result["počty"]}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
