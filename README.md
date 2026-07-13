# Mnemosyne

Lokalny, prywatny atlas rozmów i innych osobistych źródeł danych w czasie.

> **Nigdy nie dodawaj eksportu ChatGPT, lokalnej bazy ani wygenerowanych indeksów do repozytorium.**

Backend, import, FTS5, lokalne tematy, filtrowany graf i pierwsze API są działające. Bieżący etap buduje wizualny pionowy wycinek aplikacji. Dane źródłowe pozostają lokalnie w ignorowanym katalogu `sources/`, a analiza i interfejs korzystają ze wspólnego modelu zdarzeń niezależnego od źródła.

Status repozytorium: **TEMP PUBLIC**. Repozytorium jest tymczasowo publiczne, ale nie zostało jeszcze uznane za gotowe do trwałej publikacji ani objęte licencją. Prywatne dane pozostają wyłącznie lokalnie.

## Założenia MVP

- całe przetwarzanie odbywa się lokalnie;
- brak zewnętrznych API i telemetrii;
- SQLite jako lokalny indeks;
- FastAPI jako backend;
- React, TypeScript, Sigma.js i Graphology jako frontend;
- adaptery źródeł mapują dane na wspólny model `Source` / `Event`.

## Uruchamianie lokalne

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn backend.app.main:app --reload
```

W drugim terminalu uruchom frontend (Node.js 20 lub nowszy):

```bash
cd frontend
npm install
npm run dev
```

Otwórz `http://127.0.0.1:5173`. Vite przekazuje lokalne żądania `/api` do backendu na `127.0.0.1:8000`; aplikacja nie wymaga CORS ani połączeń zewnętrznych po instalacji zależności.

### Docker Compose

Cały stos można uruchomić jedną komendą:

```bash
docker-compose up --build
```

Następnie otwórz `http://127.0.0.1:5173`. Backend automatycznie stosuje migracje, `data/` jest lokalnym zapisywalnym montowaniem, a `sources/` montowaniem tylko do odczytu. Oba porty są publikowane wyłącznie na `127.0.0.1`. Zatrzymanie stosu:

```bash
docker-compose down
```

Endpoint kontrolny: `http://127.0.0.1:8000/health`.

Lokalne endpointy danych:

- `GET /api/meta` — bezpieczne metadane zakresu i liczników;
- `GET /api/graph` — graf z filtrami czasu, progów, źródła i sąsiadów;
- `GET /api/topics/search` — wyszukiwanie tematów;
- `GET /api/topics/{topic_id}` — statystyki, intensywność i relacje;
- `GET /api/topics/{topic_id}/occurrences` — paginowane fragmenty źródłowe;
- `GET /api/search/events` — lokalne wyszukiwanie FTS5;
- `GET /api/topics/{topic_id}/intensity` — miesięczna intensywność;
- `GET /api/messages/{event_id}/context` — ograniczony kontekst fragmentu.

Bezpieczna inspekcja i lokalny import:

```bash
python scripts/inspect_export.py sources/eksport.zip
alembic upgrade head
python scripts/import_export.py sources/eksport.zip
python scripts/build_topic_graph.py
```

Importer przyjmuje również rozpakowany katalog. Domyślna baza `data/mnemosyne.sqlite3` oraz całe `sources/` są ignorowane przez Git. Ponowny import aktualizuje istniejące rekordy i nie tworzy duplikatów.

Budowa tematów działa całkowicie lokalnie i wypisuje wyłącznie liczniki — nie nazwy tematów ani treść wiadomości.

Szczegóły projektu znajdują się w [`docs/architecture.md`](docs/architecture.md), a zasady bezpieczeństwa w [`docs/privacy.md`](docs/privacy.md).
