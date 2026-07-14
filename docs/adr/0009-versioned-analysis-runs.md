# ADR 0009: Wersjonowane i atomowo aktywowane przebiegi analizy

## Status

Przyjęta — 2026-07-14.

## Kontekst

Kolejne wersje ekstrakcji aktualizowały kandydatów i tematy w miejscu. W lokalnej bazie pozostało 1171 historycznych rekordów `Topic`, mimo że ostatnia przebudowa raportowała 200 aktywnych pozycji. Nie dało się jednoznacznie wskazać wersji algorytmu ani zagwarantować, że API nie miesza wyników.

## Decyzja

- Każda pełna przebudowa tworzy `analysis_run` z wersją algorytmu i hashem konfiguracji.
- Segmenty, kandydaci, przypisania, tematy, aliasy i relacje wskazują przebieg analizy.
- Dane pochodne są wymieniane w jednej transakcji; nowy przebieg zostaje aktywowany dopiero po zbudowaniu i walidacji warstw.
- Błąd powoduje rollback, dzięki czemu poprzedni aktywny przebieg oraz dane źródłowe pozostają nienaruszone.
- API filtruje warstwę tematów i terminów identyfikatorem jednego aktywnego, ukończonego przebiegu.
- `--fresh` usuwa wyłącznie regenerowalne dane analityczne. `Source`, `Event`, rekordy ChatGPT, tekst źródłowy i przebiegi importu nie są usuwane.

## Konsekwencje

Historia metadanych przebiegów pozostaje dostępna diagnostycznie, natomiast tylko jeden komplet danych pochodnych zajmuje aktywne tabele. Jest to świadomy kompromis dla lokalnego SQLite: zapobiega mieszaniu wersji bez podwajania dużych indeksów tekstowych. Pełne porównywanie dwóch materializowanych analiz będzie wymagało w przyszłości osobnych tabel lub bazy eksperymentalnej.
