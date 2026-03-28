# KDGS Implementační Standard pro KajovoSpendNG

## Cíl

Tento dokument mapuje KDGS na desktopovou Qt implementaci projektu KajovoSpendNG.

## Pravidla implementace

1. Každé hlavní view a každý dialog musí být `brand host`.
2. Každé hlavní view musí používat `StateHost`.
3. Povolené stavy view jsou `default`, `loading`, `empty`, `error`, `offline`, `maintenance`, `fallback`.
4. Nové barvy, spacing, radius, elevation a z-index se přidávají pouze přes `kajovospend/ui/tokens.py`.
5. Breakpointy a názvy stavů se definují pouze v `kajovospend/ui/design_contract.py`.
6. Systémové overlaye se řeší přes brandované dialogy, ne přes nebrandovaný systémový dialog.
7. Preview widgety nesmí vynucovat desktop-only minimální rozměry.
8. Assety v `kajovospend/branding` a `brand/` musí projít validátorem SVG.

## Mapování KDGS -> Qt

- NORMA G: `BrandLockup`, `BaseDialog`, `StateHost`
- NORMA I: testy v `tests/`
- NORMA J: tokeny a `stylesheet()`
- NORMA K: breakpoint kontrakt a smoke testy
- NORMA N: samostatné state varianty a zákaz nebrandovaných fallbacků
- NORMA O: geometry a breakpoint gate

## Pravidla pro nový view

1. Vytvořit obsah view.
2. Zabalit ho do `StateHost`.
3. Registrovat view přes `_register_state_page()`.
4. V `reload_*` nebo `_refresh_*` explicitně nastavovat stav view.
5. Dopsat test na brand host a state host.
