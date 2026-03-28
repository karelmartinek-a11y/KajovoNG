# 25 Known limits

1. **Živý OpenAI request nebyl v této verifikaci spuštěn.**
   - Důvod: chybí řízený produkční klíč a externí test sandbox.
   - Stav: boundary verifikace a oficiální URL jsou doložené.

2. **Coverage celého repa je přibližně 48 %.**
   - Kritické nové cesty jsou otestované, ale repo jako celek ještě nemá plošné vysoké coverage.

3. **Část runtime textů je stále bez diakritiky.**
   - Nejde o encoding chybu ani mojibake.
   - Jde o textovou/stylistickou nekonzistenci, která může být předmětem dalšího hardening průchodu.
