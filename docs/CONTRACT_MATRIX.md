# Vazby kontraktů přípravy a souborové výroby

Tato matice pokrývá níže uvedené cesty, nikoli dosud úplný inventář všech kontraktů aplikace. Kanonická specifikace je [SSOT](SSOT.md); posloupnost popisuje [procesní mapa](PROCESS_FLOWS.md).

| Cesta | Producent a wire kontrakt | Přijetí a další spotřebitel |
| --- | --- | --- |
| GENERATE requirements | `preparation._request`, `A0R_REQUIREMENTS_V2` | `validate_output`, `validate_requirements_v2`; vstup A1 a dalších fází |
| MODIFY requirements | `preparation._request`, `B0R_REQUIREMENTS_V2` | `_validate_modify_requirement_wrapper`; CHANGE/PRESERVE do B1 a DETAIL |
| GENERATE plán | `A1_PLAN_V2` | `validate_plan_v2`; vstup A2_SPINE |
| MODIFY plán | `B1_PLAN_V2` | `_validate_modify_plan_wrapper`; inventář a akce do B2_SPINE |
| GENERATE struktura | `A2_SPINE_V2` | `_prepare_spine`, `validate_spine_v1`; DETAIL, graf a dependency DAG |
| MODIFY struktura | `B2_SPINE_V2` | Stejné kontroly nad MODIFY akcemi a inventářem |
| GENERATE DETAIL | `A2_FILE_SPEC_V1` | `validate_file_spec_v1`; smlouva jediného souboru v grafu |
| MODIFY DETAIL | `B2_FILE_SPEC_V1` | Totéž včetně originálu a preserved behavior |
| GENERATE kontrola návrhu | `A2Q_QUALITY_GATE_V3` | `validate_graph` nad opraveným grafem |
| MODIFY kontrola návrhu | `B2Q_QUALITY_GATE_V3` | `validate_graph` a návaznost na CHANGE/PRESERVE |
| LIVE soubor | `runs.file_execution`, `FILE_CONTENT_V1` | Přesná maska požadavku, `allow_empty`, hash a staged artefakt |
| BATCH soubor | `generate_batch`, `FILE_CONTENT_V1` pro každý řádek | `custom_id` → neměnný cíl; přesná uložená maska, `allow_empty`, hash a staging |
| Obnova přípravné odpovědi | `ResponseJournal` a původní payload | Původní maska při jinak totožné práci; stejná odpověď bez nového submitu |

## Společná pravidla

- `structured_output` je vlastníkem konstrukce a kontroly strict schémat. Limit výčtových hodnot se počítá přes celé schéma, nikoli zvlášť pro každé pole.
- Maska souborového řádku SPINE spojuje `content_dependencies`, `dependency_content_mode` a `dependency_content_reason` do dvou přípustných variant. Stejná konstrukce se používá ve všech čtyřech SPINE/quality cestách.
- Maska neprokazuje správnost významu požadavku, existenci deklarované cesty ani funkčnost vygenerovaného programu. Vazby ke kanonickým vstupům kontrolují uvedené sémantické validátory.
- WorkOrder váže model, identitu práce, schema hash a payload hash. Cílovou cestu souborové odpovědi nevybírá model.
- Historické requesty, jejich masky a hashe se při obnově nepřepisují. Nová příprava používá aktuální wire masky; historická odpověď se posuzuje vůči skutečně odeslané masce a příslušným sémantickým pravidlům.
