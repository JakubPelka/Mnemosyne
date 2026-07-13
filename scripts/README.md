# Skrypty

Bezpieczna inspekcja eksportu jest dostępna po instalacji zależności projektu:

```bash
python scripts/inspect_export.py sources/nazwa-archiwum.zip
python scripts/inspect_export.py sources/rozpakowany-eksport
```

Skrypt wypisuje wyłącznie format, rodzaj wejścia, liczbę plików, standardowe nazwy shardów oraz bezpieczne kody ostrzeżeń. Nie wypisuje treści, tytułów ani identyfikatorów rozmów.

Po utworzeniu schematu można wykonać lokalny, idempotentny import:

```bash
alembic upgrade head
python scripts/import_export.py sources/rozpakowany-eksport
```

Wynik trafia domyślnie do ignorowanej bazy `data/mnemosyne.sqlite3`. Skrypt raportuje wyłącznie liczniki i losowy identyfikator przebiegu importu. Punkt wejścia przebudowy grafu zostanie dodany po implementacji tematów.
