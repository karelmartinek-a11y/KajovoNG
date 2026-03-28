# KDGS Auditní Matice UI Povrchů

## Přehled

| ID | Povrch | Typ | Normy | Aktuální stav po implementaci | Riziko |
| --- | --- | --- | --- | --- | --- |
| VIEW-001 | Dashboard | hlavní view | G, I, J, N, O | brand host + state host + breakpoint-aware layout | nízké |
| VIEW-002 | Výdaje | hlavní view | G, I, J, N, O | brand host + state host + breakpoint-aware layout | nízké |
| VIEW-003 | Účty | hlavní view | G, I, J, N, O | brand host + state host + breakpoint-aware layout | nízké |
| VIEW-004 | Dodavatelé | hlavní view | G, I, J, N, O | brand host + state host + breakpoint-aware layout | nízké |
| VIEW-005 | Provozní panel | hlavní view | G, I, J, N, O | brand host + state host + breakpoint-aware layout | nízké |
| VIEW-006 | Karanténa | hlavní view | G, I, J, N, O | brand host + state host + breakpoint-aware layout | nízké |
| VIEW-007 | Nerozpoznané | hlavní view | G, I, J, N, O | brand host + state host + breakpoint-aware layout | nízké |
| VIEW-008 | Nastavení | hlavní view | G, I, J, N, O | brand host + state host | nízké |
| DLG-001 | BaseDialog a potomci | dialogy | G, I, J, N, O | brandovaný shell | nízké |
| DLG-002 | Info/Warning/Error/Confirm | overlay | G, I, N, O | nahrazuje systémový nebrandovaný dialog | nízké |
| DLG-003 | Progress dialog | overlay | G, I, N, O | brandovaný shell | nízké |
| PRV-001 | DocumentPreviewWidget | preview | J, K, O | odstraněn desktop lock | nízké |
| PRV-002 | RegionSelectorWidget | preview | J, K, O | odstraněn desktop lock | nízké |
| AST-001 | brand/logo exports | assety | A, B, C, D | validní export pack | nízké |
| AST-002 | app_icon.svg | assety | A, E | přepsán na validní mark-like asset | nízké |
| TST-001 | UI contract testy | gate | I, J, G, O | rozšířené | nízké |

## Poznámky

- Vysoké riziko partial fixu bylo sníženo zavedením `StateHost`, brandovaných dialogů, breakpoint-aware splitter layoutu a centrálního design contractu.
- Průběžná údržba dál vyžaduje zachovat gate testy při každé větší změně UI, ale aktuální auditní stav je uzavřený jako nízkorizikový.
