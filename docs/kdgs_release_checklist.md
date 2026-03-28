# KDGS Release Checklist

- Ověřit, že v `kajovospend/ui` nezůstal nebrandovaný systémový dialog.
- Ověřit, že všechna hlavní view jsou registrovaná přes `_register_state_page`.
- Ověřit, že dialogy dědí z `BaseDialog`.
- Ověřit, že `theme.py` nepoužívá nepovolené HEX mimo tokeny.
- Ověřit, že `app_icon.svg` a další SVG projdou validátorem assetů.
- Spustit `pytest -q`.
- Spustit breakpoint a geometry testy.
- Ověřit focus ring, reduced motion a kritické CTA.
- Ručně projít dashboard, účty, dodavatele, provoz, karanténu, nerozpoznané a nastavení.
