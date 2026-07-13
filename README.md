# Mnemosyne

Lokalny, prywatny atlas rozmów i innych osobistych źródeł danych w czasie.

> **Nigdy nie dodawaj eksportu ChatGPT, lokalnej bazy ani wygenerowanych indeksów do repozytorium.**

Projekt jest na etapie przygotowania fundamentów. Dane źródłowe będą umieszczane lokalnie w ignorowanym katalogu `sources/`. Pierwszy importer obsłuży eksport ChatGPT, ale analiza i interfejs będą korzystać ze wspólnego modelu zdarzeń, niezależnego od źródła.

## Założenia MVP

- całe przetwarzanie odbywa się lokalnie;
- brak zewnętrznych API i telemetrii;
- SQLite jako lokalny indeks;
- FastAPI jako backend;
- React, TypeScript, Sigma.js i Graphology jako frontend;
- adaptery źródeł mapują dane na wspólny model `Source` / `Event`.

## Uruchamianie szkieletu backendu

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn backend.app.main:app --reload
```

Endpoint kontrolny: `http://127.0.0.1:8000/health`.

Bezpieczna inspekcja i lokalny import:

```bash
python scripts/inspect_export.py sources/eksport.zip
alembic upgrade head
python scripts/import_export.py sources/eksport.zip
```

Importer przyjmuje również rozpakowany katalog. Domyślna baza `data/mnemosyne.sqlite3` oraz całe `sources/` są ignorowane przez Git. Ponowny import aktualizuje istniejące rekordy i nie tworzy duplikatów.

Frontend zostanie uruchomiony po dodaniu pierwszego pionowego wycinka funkcjonalności. Szczegóły projektu znajdują się w [`docs/architecture.md`](docs/architecture.md), a zasady bezpieczeństwa w [`docs/privacy.md`](docs/privacy.md).
