# Kamień milowy: lokalny graf tematów i API

Data: 2026-07-13  
Status: funkcjonalność ukończona i zweryfikowana; zmiany oczekują na commit.

## Aktualny stan funkcjonalny

Lokalna baza przyjmuje kolejne eksporty bez duplikowania rekordów i oznacza rekordy nieobecne w nowszym eksporcie jako nieaktywne. Widoczne wiadomości są lokalnie analizowane metodą TF-IDF z obsługą polskiego, szwedzkiego i angielskiego. Wyniki tworzą graf tematów filtrowany po czasie, źródle, kategorii, progach i sąsiadach. API udostępnia graf, miesięczną intensywność oraz ograniczony kontekst fragmentu.

Na pełnej lokalnej bazie utworzono 18 629 dokumentów analitycznych, 494 tematy, 57 153 przypisania i 24 361 relacji. Nazwy tematów ani treści wiadomości nie zostały zapisane w raportach lub logach.

## Kryteria ukończenia

- [x] druga migracja działa na istniejącej bazie;
- [x] analiza pozostaje niezależna od formatu ChatGPT;
- [x] treści wewnętrznego rozumowania nie uczestniczą w NLP;
- [x] graf obsługuje czas, źródło, kategorie, progi, limit i sąsiadów;
- [x] API zwraca graf, intensywność i ograniczony kontekst;
- [x] implementacja działa na danych syntetycznych i pełnej lokalnej bazie;
- [x] prywatne źródła i wygenerowana baza pozostają poza Git.

## Wyniki testów

- 16/16 testów zaliczonych;
- Ruff: bez błędów, formatowanie poprawne;
- Alembic: brak dryfu schematu;
- SQLite: integralność `ok`, zero błędów kluczy obcych;
- rzeczywisty endpoint grafu: HTTP 200 przy limicie 100 węzłów;
- audyt kandydatów do commitu: zero wzorców sekretów, e-maili i fingerprintów eksportu.

## Znane błędy i ograniczenia

- `TestClient` emituje ostrzeżenie deprecacyjne z zależności FastAPI/Starlette;
- prosty TF-IDF nie łączy jeszcze synonimów ani tematów wielojęzycznych;
- zapisane relacje są agregatem globalnym, natomiast filtrowany graf jest przeliczany na żądanie;
- brak endpointu wyszukiwania, pełnych szczegółów tematu i frontendu;
- progi domyślne wymagają strojenia na podstawie czytelności wizualnej.

## Prywatność i bezpieczeństwo

Analiza odbywa się lokalnie. API nie jest wystawione publicznie przez konfigurację projektu. `sources/`, archiwum, wypakowane pliki, baza SQLite oraz wygenerowane indeksy są ignorowane. Zmiany kodu i dokumentacji nie zawierają prawdziwych fragmentów, tytułów, identyfikatorów, adresów e-mail, sekretów ani nazw plików zasobów. Prywatne dane są obecne wyłącznie w lokalnej bazie i źródłach.

## Uruchomienie

```bash
source .venv/bin/activate
alembic upgrade head
python scripts/import_export.py sources/eksport.zip
python scripts/build_topic_graph.py
uvicorn backend.app.main:app --reload
```

Kontrola: `GET http://127.0.0.1:8000/health`. Graf: `GET http://127.0.0.1:8000/api/graph`.

## Rekomendowany następny etap

Najpierw dodać API wyszukiwania FTS i zbiorczy panel szczegółów tematu. Następnie zbudować minimalny frontend Sigma.js z suwakiem czasu i panelem kontekstu, aby ocenić jakość tematów oraz dobrać domyślne progi grafu.

## Commity

- poprzedni stan bazowy: `1c15682`;
- ten kamień milowy: oczekuje na commit.
