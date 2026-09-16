"""Instrukce, strict kontrakty a dohledatelnost GENERATE a MODIFY."""

from __future__ import annotations

from copy import deepcopy

import jsonschema

from .contracts import ContractError, ValidationIssue, structure_response_format, validate_paths
from .model_registry import model_spec
from .structured_output import array, builtin_format, obj, response_format


def stage_instructions(stage: str, *, batch: bool = False) -> str:
    """Vrátí přesný společný text a instrukci fáze bez pracovních dat."""
    if stage == "A0":
        return CORE_INSTRUCTIONS + "\n\n" + (
            "Proveď pouze technický příjem vstupního zadání. Zachovej přesně celý "
            "obsah zadání i pořadí všech jeho částí, bez shrnutí, parafrází nebo "
            "vynechávání. Nevytvářej requirements, neinterpretuj požadavky a "
            "nenavrhuj řešení. Potvrď příjem podle předepsaného textového kontraktu."
        )
    stage = {
        "A0R": "A0R_REQUIREMENTS", "A1": "A1_PLAN", "A2": "A2_STRUCTURE",
        "A2Q": "A2Q_QUALITY_GATE", "A3": "A3_FILE",
        "B0R": "B0R_REQUIREMENTS", "B1": "B1_PLAN", "B2": "B2_STRUCTURE",
        "B2Q": "B2Q_QUALITY_GATE", "B3": "B3_FILE",
    }.get(stage, stage)
    if stage not in _STAGE_INSTRUCTIONS:
        raise ValueError(f"Neznámá fáze: {stage}")
    instructions = CORE_INSTRUCTIONS + "\n\n" + _STAGE_INSTRUCTIONS[stage]
    if stage in ("A3_FILE", "B3_FILE") and batch:
        instructions += (
            "\n\nDávkové dodání: vrať celé znění souboru v jediné úplné části, "
            "bez pokračování. Obsah nezkracuj ani nevynechávej."
        )
    elif stage in ("A3_FILE", "B3_FILE"):
        instructions += (
            "\n\nTechnické dělení výstupu: jeden chunk smí obsahovat nejvýše "
            "500 řádků obsahu souboru. Delší soubor vrať v navazujících chunkech "
            "podle předepsaného kontraktu; zachovej úplný obsah, pořadí a návaznost "
            "bez vynechání nebo opakování řádků."
        )
    return instructions


def apply_quality(payload: dict, maximum_quality: bool) -> None:
    """Upraví payload na místě; Standard zachovává běžnou politiku."""
    if not maximum_quality:
        return
    spec = model_spec(payload["model"])
    order = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    supported = spec["reasoning"]
    if set(supported) - set(order):
        raise ValueError("Matice obsahuje neznámou úroveň reasoning.")
    if spec["reasoning_supported"] and supported:
        reasoning = dict(payload.get("reasoning") or {})
        reasoning["effort"] = max(supported, key=order.index)
        payload["reasoning"] = reasoning
    elif not spec["reasoning_supported"]:
        payload.pop("reasoning", None)
    effort = (payload.get("reasoning") or {}).get("effort")
    if spec["sampling"] == "omit" or (
        spec["sampling"] == "explicit_none" and effort != "none"
    ):
        payload.pop("temperature", None)
        payload.pop("top_p", None)


def _prefix(mode: str) -> str:
    if mode not in ("GENERATE", "MODIFY"):
        raise ValueError(f"Neznámý režim: {mode}")
    return "A" if mode == "GENERATE" else "B"


def requirements_format(mode: str) -> dict:
    """Vrátí minimální strict requirements kontrakt příslušného režimu."""
    prefix = _prefix(mode)
    text = {"type": "string"}
    keys = (
        "product_intent explicit_requirements implicit_requirements invariants "
        "assumptions user_flows system_flows states_and_lifecycle "
        "validation_and_error_behaviour quality_attributes security_and_privacy "
        "persistence_and_consistency dependencies_and_integrations "
        "acceptance_criteria definition_of_done"
        if prefix == "A" else
        "requested_change current_behaviour_to_preserve explicit_change_requirements "
        "implicit_change_requirements impacted_flows impacted_states impacted_contracts "
        "validation_and_error_behaviour migration_or_compatibility_concerns "
        "acceptance_criteria definition_of_done"
    ).split()
    contract = prefix + "0R_REQUIREMENTS"
    properties = {"contract": {"type": "string", "enum": [contract]}}
    for index, key in enumerate(keys):
        properties[key] = (
            text if index == 0 else
            array(obj({"id": text, "description": text})) if key.endswith("requirements")
            else array(text)
        )
    return response_format(contract, obj(properties))


def _extend(schema: dict, properties: dict) -> None:
    schema["properties"].update(deepcopy(properties))
    schema["required"] = list(schema["properties"])


def enriched_plan_format(mode: str) -> dict:
    """Vrátí nezávislou kopii plánu doplněnou o položky architektury."""
    prefix = _prefix(mode)
    if prefix == "A":
        from .generate_batch import plan_format
        result = deepcopy(plan_format())
    else:
        result = deepcopy(builtin_format("B1_PLAN"))
    text = {"type": "string"}
    _extend(result["format"]["schema"], {
        "architecture_items": array(obj({
            "id": text, "requirement_ids": array(text), "responsibility": text,
        })),
    })
    return result


def enriched_structure_format(mode: str, *, implementation: bool = False) -> dict:
    """Vrátí A2/B2 i pro quality gate, nikoli samostatný gate kontrakt."""
    prefix = _prefix(mode)
    text = {"type": "string"}
    strings = array(text)
    if prefix == "A":
        from .generate_batch import structure_format
        result = deepcopy(structure_format())
        files_key = "files"
    else:
        result = deepcopy(structure_response_format("B2_STRUCTURE"))
        files_key = "touched_files"
        _extend(result["format"]["schema"], {
            "invariants": strings,
            "interfaces": array(obj({"id": text, "definition": text})),
            "preserved_files": array(obj({
                "path": text, "provides": strings, "requirement_ids": strings,
                "architecture_item_ids": strings, "behavior": text,
            })),
        })
    item = result["format"]["schema"]["properties"][files_key]["items"]
    if prefix == "B":
        _extend(item, {
            "purpose": text, "language": text,
            "kind": {"type": "string", "enum": ["text", "binary"]},
            "dependencies": strings, "provides": strings, "requires": strings,
            "behavior": text, "preserved_behaviour": strings,
        })
    _extend(item, {"requirement_ids": strings, "architecture_item_ids": strings})
    if implementation:
        from .context_compiler import implementation_schema
        _extend(result["format"]["schema"], {"implementation": implementation_schema()})
    return result


def _ids(values: list, label: str) -> set:
    if any(not isinstance(value, str) or not value.strip() or value != value.strip()
           for value in values) or len(set(values)) != len(values):
        raise ContractError(f"{label}: identifikátory musí být jedinečné a neprázdné.")
    return set(values)


def validate_stage_schema(value, fmt, stage):
    """Vrátí všechny nezávislé vady schématu s přesným místem."""
    errors = list(jsonschema.Draft202012Validator(fmt["format"]["schema"]).iter_errors(value))
    if errors:
        issues = [ValidationIssue(
            "schema_invalid", stage,
            "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in e.absolute_path),
            e.message, e.validator_value, e.instance,
        ) for e in errors]
        raise ContractError("Neplatná specifikace: " + "; ".join(i.message for i in issues), issues=issues)


def validate_requirements(req, mode):
    prefix = _prefix(mode)
    stage = prefix + "0R"
    validate_stage_schema(req, requirements_format(mode), stage)
    keys = (("explicit_requirements", "implicit_requirements") if prefix == "A" else
            ("explicit_change_requirements", "implicit_change_requirements"))
    issues, seen = [], set()
    intent_key = "product_intent" if prefix == "A" else "requested_change"
    if not req[intent_key].strip():
        issues.append(ValidationIssue("requirements_intent_empty", stage, "/" + intent_key,
                                      "Specifikace nemá popsaný záměr.", "neprázdný záměr", ""))
    for key in keys:
        for index, item in enumerate(req[key]):
            identifier = item["id"]
            pointer = f"/{key}/{index}"
            if not identifier.strip() or identifier != identifier.strip() or identifier in seen:
                issues.append(ValidationIssue("requirement_id_invalid", stage, pointer + "/id",
                                              "ID požadavku musí být jedinečné a neprázdné.", None, identifier))
            seen.add(identifier)
            if not item["description"].strip():
                issues.append(ValidationIssue("requirement_description_empty", stage, pointer + "/description",
                                              "Požadavek nemá popis.", "neprázdný popis", ""))
    if issues:
        raise ContractError("; ".join(i.message for i in issues), issues=issues)
    return seen


def validate_plan(req, plan, mode):
    required = validate_requirements(req, mode)
    stage = _prefix(mode) + "1"
    validate_stage_schema(plan, enriched_plan_format(mode), stage)
    issues, seen, covered = [], set(), set()
    for index, item in enumerate(plan["architecture_items"]):
        pointer = f"/architecture_items/{index}"
        identifier = item["id"]
        if not identifier.strip() or identifier != identifier.strip() or identifier in seen:
            issues.append(ValidationIssue("architecture_id_invalid", stage, pointer + "/id",
                                          "ID architektury musí být jedinečné a neprázdné.", None, identifier))
        seen.add(identifier)
        refs = item["requirement_ids"]
        unknown = sorted(set(refs) - required)
        if unknown or len(refs) != len(set(refs)):
            issues.append(ValidationIssue("architecture_requirement_invalid", stage, pointer + "/requirement_ids",
                                          f"Architektura {identifier}: neplatné odkazy na požadavky {unknown}.",
                                          sorted(required), refs))
        if not item["responsibility"].strip():
            issues.append(ValidationIssue("architecture_responsibility_empty", stage, pointer + "/responsibility",
                                          f"Architektura {identifier} nemá odpovědnost.", "neprázdná odpovědnost", ""))
        covered.update(refs)
    if required - covered:
        issues.append(ValidationIssue("plan_coverage_missing", stage, "/architecture_items",
                                      "Plán nepokrývá všechny požadavky.", sorted(required), sorted(covered)))
    if mode == "MODIFY":
        for pointer, values in (
            ("/change_plan", plan["change_plan"]["files_to_modify"] + plan["change_plan"]["files_to_add"]),
            ("/diagnosis/evidence", plan["diagnosis"]["evidence"]),
        ):
            try:
                validate_paths(values)
            except (ContractError, ValueError) as exc:
                issues.append(ValidationIssue("plan_paths_invalid", stage, pointer, str(exc), None,
                                              [item["path"] for item in values]))
    if issues:
        raise ContractError("; ".join(i.message for i in issues), issues=issues)
    return seen


def bound_reference_format(fmt, req, plan, mode):
    """Omezí reference ve výstupu; ostatní povinnosti zůstávají ve scopes."""
    result = deepcopy(fmt)
    required = sorted(validate_requirements(req, mode))
    architecture = sorted(item["id"] for item in (plan or {}).get("architecture_items", []))

    def bind(node):
        if not isinstance(node, dict):
            return
        for key, child in node.get("properties", {}).items():
            child = deepcopy(child)
            node["properties"][key] = child
            ids = required if key == "requirement_ids" else architecture if key == "architecture_item_ids" else None
            if ids is not None:
                if ids:
                    child["items"] = {"type": "string", "enum": ids}
                else:
                    child["maxItems"] = 0
            bind(child)
        bind(node.get("items"))

    bind(result["format"]["schema"])
    from .structured_output import validate_schema
    try:
        validate_schema(result["format"]["schema"])
    except ValueError as exc:
        raise ContractError(f"Registr odkazů nelze odeslat v podporovaném schématu: {exc}") from exc
    return result


def validate_traceability(req: dict, plan: dict, struct: dict, mode: str | None = None) -> None:
    """Ověří schémata a úplné vazby bez změny vstupů; chyby jsou ContractError.

    Původní validátor A2 může volající použít po odstranění requirement_ids
    a architecture_item_ids z kopie files. Zde se žádné vazby nedoplňují.
    """
    if not isinstance(req, dict) or req.get("contract") not in (
        "A0R_REQUIREMENTS", "B0R_REQUIREMENTS"
    ):
        raise ContractError("Neznámý requirements kontrakt.")
    modifying = req["contract"] == "B0R_REQUIREMENTS"
    detected_mode = "MODIFY" if modifying else "GENERATE"
    if mode is not None and mode != detected_mode:
        raise ContractError("Režim snapshotu neodpovídá requirements kontraktu.")
    mode = detected_mode
    validate_plan(req, plan, mode)
    for value, factory in ((req, requirements_format), (plan, enriched_plan_format),
                           (struct, enriched_structure_format)):
        try:
            fmt = (enriched_structure_format(mode, implementation="implementation" in struct)
                   if factory is enriched_structure_format else factory(mode))
            jsonschema.validate(value, fmt["format"]["schema"])
        except jsonschema.ValidationError as exc:
            raise ContractError(f"Neplatná specifikace: {exc.message}") from exc
    requirement_keys = (
        ("explicit_change_requirements", "implicit_change_requirements") if modifying
        else ("explicit_requirements", "implicit_requirements")
    )
    requirements = [item for key in requirement_keys for item in req[key]]
    required = _ids([item["id"] for item in requirements], "Požadavky")
    if any(not item["description"].strip() for item in requirements):
        raise ContractError("Požadavek vyžaduje neprázdný popis.")
    architecture = plan["architecture_items"]
    architecture_ids = _ids([item["id"] for item in architecture], "Architektura")
    mappings = {}
    for item in architecture:
        refs = _ids(item["requirement_ids"], "Požadavky architektury")
        if not refs <= required or not item["responsibility"].strip():
            raise ContractError("Architektura obsahuje neznámý požadavek nebo prázdnou odpovědnost.")
        mappings[item["id"]] = refs
    if set().union(*mappings.values()) != required:
        raise ContractError("Plán nepokrývá všechny požadavky.")
    files = struct["touched_files" if modifying else "files"]
    preserved = struct["preserved_files"] if modifying else []
    validate_paths(files + preserved)
    paths = {item["path"] for item in files + preserved}
    interfaces = _ids([item["id"] for item in struct["interfaces"]], "Rozhraní")
    if any(not item["definition"].strip() for item in struct["interfaces"]):
        raise ContractError("Rozhraní vyžaduje neprázdnou definici.")
    providers = {key: set() for key in interfaces}
    for item in files + preserved:
        provided = _ids(item["provides"], "Poskytovaná rozhraní")
        if not provided <= interfaces:
            raise ContractError("Soubor poskytuje neznámé rozhraní.")
        for interface in provided:
            providers[interface].add(item["path"])
    covered = set()
    implemented = {key: set() for key in architecture_ids}
    for item in files + preserved:
        refs = _ids(item["requirement_ids"], "Požadavky souboru")
        links = _ids(item["architecture_item_ids"], "Architektura souboru")
        if not refs <= required or not links <= architecture_ids:
            raise ContractError("Soubor odkazuje na neznámý požadavek nebo architekturu.")
        if not refs <= set().union(*(mappings[key] for key in links)):
            raise ContractError("Požadavky souboru nemají vazbu přes uvedenou architekturu.")
        changed = item in files
        if (changed and (not links or not item["purpose"].strip())) or not item["behavior"].strip():
            raise ContractError("Soubor vyžaduje architekturu, účel a chování.")
        covered.update(refs)
        for key in links:
            implemented[key].update(refs & mappings[key])
        if not changed:
            continue
        dependencies = _ids(item["dependencies"], "Závislosti")
        validate_paths([{"path": path} for path in dependencies])
        if not dependencies <= paths or item["path"] in dependencies:
            raise ContractError("Soubor má neznámou závislost nebo závisí sám na sobě.")
        needed = _ids(item["requires"], "Vyžadovaná rozhraní")
        if not needed <= interfaces:
            raise ContractError("Soubor vyžaduje neznámé rozhraní.")
        for interface in needed:
            if not providers[interface] & (dependencies | {item["path"]}):
                raise ContractError(f"Rozhraní {interface} nemá dostupného poskytovatele.")
    if covered != required or any(implemented[key] != mappings[key] for key in mappings):
        raise ContractError("Struktura nepokrývá všechny požadavky a vazby plánu.")
    linked = {key for item in files + preserved for key in item["architecture_item_ids"]}
    if linked != architecture_ids:
        raise ContractError("Položka architektury nemá implementační soubor.")
    if modifying:
        change = plan["change_plan"]
        planned = change["files_to_modify"] + change["files_to_add"]
        validate_paths(planned)
        validate_paths(plan["diagnosis"]["evidence"])
        expected = {item["path"]: action for action, key in (
            ("modify", "files_to_modify"), ("add", "files_to_add")
        ) for item in change[key]}
        actual = {item["path"]: item["action"] for item in files}
        if any(actual.get(path) != action for path, action in expected.items()):
            raise ContractError("Struktura musí zachovat všechny soubory a akce plánu B1.")
        # Struktura i quality gate mohou doplnit implementaci stávajícího záměru.
        # Neprázdné vazby doplňků již výše prošly kontrolou známých ID a návaznosti.
        for item in files:
            if item["path"] not in expected and not item["requirement_ids"]:
                raise ContractError("Dodatečný soubor vyžaduje pokrytí požadavků a architektury.")


CORE_INSTRUCTIONS = """Jsi součást seniorního product-engineering a software-engineering týmu. Tvým cílem není pouze doslovně odškrtnout explicitní body uživatelského zadání, ale dovést zamýšlený výsledek do profesionálně úplného, skutečně použitelného a vnitřně konzistentního stavu.

Uživatelské zadání považuj za minimální explicitní kontrakt, nikoli za úplný výčet všeho, co musí kvalitní řešení obsahovat. Zachovej všechny výslovné požadavky, omezení a záměr uživatele, ale systematicky doplň implicitní požadavky, návaznosti a chování, které seniorní tým přirozeně očekává od produkčně připraveného řešení.

Každou funkci, ovládací prvek, datový tok, integraci a proces rozpracuj jako úplný životní cyklus, ne jako izolovanou větu nebo povrchní implementaci. Domysli relevantní stavy, přechody, validace, chyby, zotavení, persistenci, konzistenci, bezpečnost, použitelnost, zpětnou vazbu uživateli, okrajové případy a návaznosti na ostatní části systému vždy tam, kde jsou pro danou funkci relevantní.

Nezmenšuj scope jen proto, aby byl výstup kratší nebo snazší. Nevyměňuj skutečnou implementaci za demonstraci. Nevytvářej skeletony, placeholdery, TODO, stuby, falešná data ani produkční cestu založenou na mocku, pokud je uživatel výslovně nepožaduje. Nevytvářej funkci, která pouze vypadá funkčně. Stav, progress, potvrzení úspěchu a UI musí odpovídat skutečně provedené operaci.

Testy jsou důkazem implementace, nikoli její náhradou. Nesmí vzniknout řešení, jehož hlavním cílem je pouze projít testy bez reálně dokončeného chování.

Za HOTOVO považuj řešení až tehdy, když při odborné kontrole působí jako promyšlená a rozpracovaná práce seniorního týmu: explicitní zadání je splněno, implicitní profesionální očekávání jsou pokryta, jednotlivé části jsou dotažené do detailu, vazby mezi nimi jsou konzistentní a nejsou přítomna známá provizoria ani předstíraná funkčnost.

Doplňuj pouze takové implicitní požadavky, které logicky vyplývají ze zamýšleného produktu nebo jsou standardní podmínkou profesionální implementace. Nevymýšlej nesouvisející produktové funkce a neměň záměr uživatele. Při skutečné nejednoznačnosti zvol bezpečný a profesionální výchozí předpoklad a tento předpoklad explicitně zaznamenej.

Dodrž přesně kontrakt a strukturovaný výstup požadovaný aktuální fází. Text kontraktu nebo JSON schématu neopakuj v prose instrukcích, pokud je již technicky vynucen response formatem."""

_STAGE_INSTRUCTIONS = {
    "A0R_REQUIREMENTS": """Jednej jako principal requirements engineer a product architect.

Z uživatelského zadání vytvoř implementačně použitelnou specifikaci. Neomezuj se na přeformulování vět uživatele. U každého požadovaného výsledku rozviň, co musí být pravda, aby šlo o profesionálně dokončenou funkci v reálném produktu.

Identifikuj explicitní požadavky, implicitní profesionální požadavky, invarianty, předpoklady, uživatelské a systémové toky, stavy a životní cykly, vazby mezi funkcemi, validace, chybové a recovery scénáře, relevantní bezpečnostní a kvalitativní požadavky a měřitelná kritéria dokončení.

Každou významnou schopnost rozpracuj dostatečně hluboko, aby následující architekt a implementátor nemuseli hádat, co znamená „hotovo“.

Nedoplňuj nesouvisející produktový scope. Doplňuj pouze to, co je přirozenou součástí úplného profesionálního řešení zamýšleného uživatelem.

V této fázi ještě nevyráběj zdrojové soubory. Výstup je kanonická requirements specifikace pro A1.""",
    "A1_PLAN": """Jednej jako principal software architect.

Vycházej z kanonické A0R_REQUIREMENTS specifikace a navrhni úplný realizační plán systému. Zachovej každý závazný požadavek a každou relevantní vazbu. Přelož požadavky do architektury, komponent, odpovědností, rozhraní, datových toků a implementačních kroků tak, aby nic důležitého nezůstalo pouze implicitní.

Plán musí popsat skutečně dokončený produkt, nikoli minimální demonstraci. U každé významné části ověř, že existuje jasná cesta od požadavku přes architekturu až k implementaci a ověření.

Nevytvářej soubory. Výstup je závazný architektonický plán pro A2.""",
    "A2_STRUCTURE": """Jednej jako senior software engineer odpovědný za implementační kontrakty.

Převeď A1 plán a A0R požadavky do přesné implementační struktury. Pro každý výsledný soubor urč jeho účel, odpovědnosti, poskytovaná a vyžadovaná rozhraní, závislosti a vazbu na konkrétní požadavky.

Nenechávej důležité chování pouze v obecné poznámce. Rozděl odpovědnosti tak, aby následné samostatné generování souborů mohlo vytvořit konzistentní, navzájem kompatibilní a úplný systém.

Zkontroluj úplnost grafu závislostí a traceability: každý závazný požadavek musí mít konkrétní implementační pokrytí a každý vyžadovaný kontrakt musí mít poskytovatele.

Výstup je kanonická implementační specifikace pro generování souborů.""",
    "A2Q_QUALITY_GATE": """Jednej jako nezávislý principal engineer provádějící pre-implementation quality gate.

Dostáváš uživatelský záměr, A0R requirements, A1 plán a A2 implementační strukturu. Předpokládej, že jiný tým tuto specifikaci následně implementuje přesně tak, jak je napsaná.

Hledej zejména nedotažené funkce, chybějící stavy a přechody, nejasné kontrakty, rozbité vazby, neřešené chybové a recovery scénáře, skryté předpoklady, nekonzistence, bezpečnostní nebo datové mezery, povrchní UX chování a místa, kde by formální implementace mohla splnit zadání, ale výsledek by nepůsobil jako profesionálně dokončený produkt.

Neomezuj se na auditní seznam. Všechny zjištěné oprávněné nedostatky rovnou zapracuj a vrať opravenou, úplnou a kanonickou implementační specifikaci připravenou pro A3.

Nepřidávej nesouvisející funkce ani samoúčelný overengineering. Cílem je úplnost, konzistence a dotažení původního záměru.""",
    "A3_FILE": """Jednej jako senior implementační engineer.

Vytvoř právě jeden požadovaný soubor jako kompletní produkční součást systému. Implementuj celý rozsah odpovědností, které tomuto souboru přiděluje kanonická specifikace, a dodrž všechna sdílená rozhraní, datové kontrakty, verze, importy a návaznosti.

Nevracej skeleton, pseudokód, zkrácenou demonstraci, TODO, placeholder ani část implementace s tím, že zbytek doplní někdo později. Pokud soubor realizuje uživatelskou funkci, realizuj i všechny její relevantní stavy a chování určené specifikací.

Nevytvářej předstíranou funkčnost. Úspěch, stav a výstupy musí odpovídat skutečně provedené logice.

Vrať pouze úplný obsah souboru podle předepsaného strukturovaného kontraktu.""",
    "B0R_REQUIREMENTS": """Jednej jako principal requirements engineer pro změny existujícího produkčního systému.

Nejprve pochop aktuální chování projektu a poté uživatelskou požadovanou změnu převeď na úplnou change specification. Zachovej chování, které uživatel nemění, pokud změna logicky nevyžaduje jeho úpravu.

Neomezuj se na doslovnou editaci místa, které uživatel pojmenoval. Urči celý skutečný dopad změny: dotčené funkce, stavy, datové toky, kontrakty, validace, UI chování, persistenci, integrace, chyby, recovery scénáře a testovatelné podmínky dokončení.

Doplň implicitní profesionální požadavky nezbytné k tomu, aby výsledná změna byla dotažená a konzistentní s celým systémem. Nevymýšlej nesouvisející redesign ani další produktový scope.

Výstup je kanonická change requirements specifikace pro B1.""",
    "B1_PLAN": """Jednej jako principal software architect pro změny existujícího systému.

Z B0R requirements a skutečného stavu projektu vytvoř kompletní plán změny. Urči všechny dotčené komponenty a vazby a vysvětli, jak změna projde systémem od vstupu přes stav a logiku až po výstup a uživatelské chování.

Minimalizuj zbytečné zásahy, ale nikdy nesnižuj nutný rozsah jen proto, aby se měnilo méně souborů. Zachovej nedotčené chování a současně zajisti, že změna bude dokončena end-to-end.

Výstup je závazný change plan pro B2.""",
    "B2_STRUCTURE": """Jednej jako senior software engineer připravující přesné implementační kontrakty změny.

Převeď B1 plán a B0R requirements do konkrétního seznamu měněných souborů a jejich úplných odpovědností. U každého souboru urč, co se musí změnit, které existující chování musí zůstat zachováno, jaká rozhraní poskytuje a vyžaduje a jaké další soubory s ním musí zůstat konzistentní.

Každý požadavek změny musí mít dohledatelné implementační pokrytí. Nesmí vzniknout stav, kdy UI deklaruje změnu, kterou backend nebo persistence neimplementuje, nebo naopak.

Výstup je kanonická implementační specifikace změny pro B3.""",
    "B2Q_QUALITY_GATE": """Jednej jako nezávislý principal engineer provádějící pre-implementation quality gate změny existujícího systému.

Prověř B0R requirements, B1 plán a B2 implementační strukturu proti skutečnému stavu projektu. Hledej především neúplný end-to-end dopad, přehlédnuté vazby, regresní rizika, chybějící stavy, nekonzistentní UI a backend chování, neřešené chyby a recovery, nedotažené datové změny a místa, kde by změna formálně splnila zadání, ale v reálném používání by nebyla profesionálně dokončená.

Nevracej pouze seznam připomínek. Oprávněné nálezy rovnou zapracuj do opravené a úplné kanonické implementační specifikace.

Zachovej nedotčené chování a nepřidávej nesouvisející produktový scope.""",
    "B3_FILE": """Jednej jako senior implementační engineer upravující existující produkční soubor.

Vrať kompletní výslednou podobu právě jednoho měněného souboru. Zapracuj celý rozsah změny přidělený tomuto souboru a zároveň zachovej veškeré existující chování, které change specification nemění.

Nevracej diff, pseudokód, skeleton, zkrácenou ukázku, TODO ani placeholder. Nevytvářej implementaci zaměřenou pouze na průchod testem. Změna musí být skutečně funkční a konzistentní s ostatními kontrakty projektu.

Pokud změna ovlivňuje stav, validaci, chyby, persistenci, UI nebo navazující proces, musí soubor realizovat svou část tohoto chování přesně podle kanonické specifikace.

Vrať pouze úplný obsah výsledného souboru podle předepsaného strukturovaného kontraktu.""",
}
