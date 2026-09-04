# Cross-service tests

Tento adresář je připraven pro budoucí integrační a end-to-end testy napříč
službami. Základní automatické testy jsou zatím umístěné přímo u jednotlivých
částí monorepa:

- `backend/tests/`
- `worker/tests/`
- `frontend/src/*.test.tsx`

Všechny je spouští kořenový skript `scripts/test.ps1`.
