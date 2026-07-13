# Postęp prac

Ten dokument jest zwięzłym dziennikiem logicznych bloków prac. Szczegółowe raporty powstają tylko dla kamieni milowych, większych migracji, zmian architektury i audytów bezpieczeństwa.

## 2026-07-13 — Lokalny graf tematów i pierwszy pionowy wycinek API

### Wykonane zadania

- dodano dezaktywację zdarzeń nieobecnych w kolejnym eksporcie;
- przeprowadzono drugą migrację istniejącej bazy;
- dodano neutralne `context_id`, `is_active` i `analysis_enabled` do wspólnego modelu zdarzeń;
- zaimplementowano lokalne wydobywanie tematów PL/SV/EN z unigramami, bigramami i TF-IDF;
- zbudowano relacje współwystępowania z licznikami wiadomości i kontekstów;
- dodano filtrowanie grafu po czasie, źródle, kategorii, progach i sąsiadach;
- dodano ograniczony kontekst wiadomości na aktywnej gałęzi;
- udostępniono endpointy grafu, intensywności i kontekstu.

### Zmienione pliki

- modele i import: `backend/app/models/`, `backend/app/services/import_chatgpt.py`;
- analiza i zapytania: `backend/app/services/topics.py`, `graph.py`, `context.py`;
- API: `backend/app/api/dependencies.py`, `routes.py`, `backend/app/main.py`;
- migracja: `backend/migrations/versions/f9a405ee6284_add_topic_graph_and_event_activity.py`;
- testy: `backend/tests/test_database_import.py`, `backend/tests/test_api.py`;
- uruchamianie i dokumentacja: `scripts/build_topic_graph.py`, `README.md`, `scripts/README.md`, `docs/architecture.md`, `docs/roadmap.md`.

### Testy i wyniki

- `pytest -q`: 16 testów zaliczonych, jedno ostrzeżenie zależności `TestClient`;
- `ruff check backend scripts`: bez błędów;
- `ruff format --check backend scripts`: 30 plików poprawnie sformatowanych;
- `alembic check`: brak nowych operacji i dryfu schematu;
- pełna baza: `PRAGMA integrity_check=ok`, brak błędów kluczy obcych;
- rzeczywiste API grafu: HTTP 200, limit 100 węzłów respektowany;
- audyt zmian: brak sekretów, e-maili i fingerprintów prywatnego eksportu.

### Decyzje techniczne

- NLP operuje na wspólnym `Event`, nie na tabelach ChatGPT;
- `thoughts` i `reasoning_recap` są przechowywane, ale wyłączone z analizy;
- współwystępowanie przechowuje liczbę wiadomości i kontekstów, a wagę normalizuje cosinusowo;
- kontekst źródłowy ma twardy limit dziesięciu wiadomości w każdą stronę.

### Znane ograniczenia i otwarte kwestie

- tematy są słowami kluczowymi, nie pełną ontologią;
- brak zbiorczego endpointu szczegółów tematu i wyszukiwania FTS w API;
- brak frontendu grafu i osi czasu;
- ostrzeżenie deprecacyjne pochodzi z FastAPI/Starlette `TestClient`;
- jakość stopwords i progi grafu wymagają oceny wizualnej na UI.

### Następny krok

Rozszerzyć API o wyszukiwanie i szczegóły tematów, a następnie zbudować frontend grafu z filtrem czasu.

### Commity

- baza bloku: `1c15682`;
- kamień milowy: `6e6e7b7`.

## 2026-07-13 — Normalizacja instrukcji i statusu projektu

### Wykonane zadania

- zastąpiono historyczny `agents.txt` zwięzłym `AGENTS.md`;
- zarchiwizowano pełny pierwotny brief bez utrzymywania konkurencyjnych instrukcji;
- zsynchronizowano roadmapę, raporty i status repozytorium z commitem `6e6e7b7`;
- potwierdzono status GitHub jako `TEMP PUBLIC`;
- wykonano audyt bieżącego drzewa i nazw plików historii Git.

### Zmienione pliki

- `AGENTS.md`;
- `docs/project-specification-history.md`;
- `README.md`;
- `docs/privacy.md`;
- `docs/roadmap.md`;
- `docs/progress.md`;
- `docs/reports/2026-07-13-local-topic-graph-api.md`.

### Testy i wyniki

- audyt śledzonych i historycznych nazw prywatnych artefaktów: zero wyników;
- wzorce sekretów, adresów e-mail i lokalnych ścieżek w śledzonych plikach: zero wyników;
- reguły ignorowania `sources/`, SQLite, logów i wyników: potwierdzone;
- `git diff --check`: bez błędów.
- `pytest -q`: 16 testów zaliczonych (1 ostrzeżenie deprecacyjne zależności);
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: 30 plików poprawnie sformatowanych;
- `alembic check`: brak nowych operacji migracyjnych.

### Decyzje, ograniczenia i następny krok

- `AGENTS.md` zawiera wyłącznie trwałe reguły, a szczegóły pozostają w dokumentacji i ADR-ach;
- repo pozostaje publiczne tymczasowo, dlatego audyt przed każdym push jest obowiązkowy;
- brak zmian licencyjnych;
- następny krok: typowany kontrakt API dla Visual MVP.

### Commity

- baza bloku: `6e6e7b7`;
- bieżący blok: oczekuje na commit.
