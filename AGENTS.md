# Mnemosyne — trwałe instrukcje repozytorium

Przed rozpoczęciem każdego większego zadania przeczytaj w całości:

- `README.md`;
- `docs/architecture.md`;
- `docs/privacy.md`;
- `docs/roadmap.md`;
- `docs/progress.md`.

## Zasady produktu

- Aplikacja działa lokalnie. Nie używaj zewnętrznych API, telemetrii, CDN-ów ani mechanizmów przesyłających treść lub metadane użytkownika.
- Prawdziwe źródła, bazy, logi, cache, wygenerowane indeksy i wyniki nigdy nie trafiają do Git.
- Testy i dokumentacja korzystają wyłącznie z jawnie syntetycznych danych. Nie kopiuj prawdziwych fragmentów nawet po anonimizacji.
- Analiza, graf, czas i API operują na wspólnym `Event`, nie bezpośrednio na tabelach ChatGPT.
- Nowe źródła są adapterami mapującymi dane na wspólny model. W bieżącym MVP implementowany jest wyłącznie ChatGPT.
- Webowy graf z filtrowaniem czasu, szczegółami tematu i ograniczonym kontekstem źródłowym jest częścią MVP.

## Bezpieczeństwo i weryfikacja

- Nie modyfikuj surowych źródeł; montuj lub otwieraj je tylko do odczytu.
- Nie umieszczaj treści wiadomości, tytułów, identyfikatorów użytkownika ani lokalnych ścieżek w logach, błędach i raportach.
- Przed ukończeniem bloku uruchom odpowiednie testy, Ruff, kontrolę formatowania, `alembic check` oraz audyt kandydatów do commitu i historii Git.
- Jeżeli prywatne dane znajdą się w Git, przerwij dalszą pracę i zgłoś ścieżkę, commit oraz rodzaj problemu bez cytowania treści.
- Nie przepisuj historii ani nie wykonuj force push bez wyraźnej zgody właściciela.

## Sposób pracy

- Pracuj małymi, sprawdzalnymi etapami i commitami. Nie uznawaj zadania za ukończone bez testów i możliwego do sprawdzenia rezultatu.
- Po każdym logicznym etapie aktualizuj jedną sekcję w `docs/progress.md`.
- Raport w `docs/reports/` twórz wyłącznie po ukończeniu całego kamienia milowego, większej migracji, istotnej zmianie architektury lub audycie bezpieczeństwa.
- Ważne decyzje architektoniczne dokumentuj jako ADR w `docs/adr/`.
- Dla Visual MVP raport kamienia milowego powstaje dopiero po działającym frontendzie, pełnej walidacji i lokalnym uruchomieniu całego stosu.

## Polecenia kontrolne

```bash
pytest -q
ruff check backend scripts
ruff format --check backend scripts
alembic check
cd frontend && npm test && npm run build
```
