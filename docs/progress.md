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
- normalizacja instrukcji: `2b42516`.

## 2026-07-13 — Typowany kontrakt API Visual MVP

### Wykonane zadania

- dodano jawne modele odpowiedzi Pydantic i wersję API `0.2.0`;
- dodano metadane aplikacji, wyszukiwanie tematów, szczegóły tematu i paginowane wystąpienia;
- udostępniono FTS5 z paginacją oraz filtrami czasu, źródła i prywatności;
- rozszerzono graf i intensywność o jawny filtr `privacy_level=private`;
- ograniczono wyniki list do krótkich fragmentów, pozostawiając kontekst osobnemu endpointowi;
- dodano testy kontraktu OpenAPI, filtrów, 404, paginacji i granicy prywatności.

### Zmienione pliki

- `backend/app/api/routes.py`;
- `backend/app/api/schemas.py`;
- `backend/app/main.py` i `backend/app/main_version.py`;
- `backend/app/services/catalog.py`, `graph.py` i `topics.py`;
- `backend/tests/test_api.py`;
- `docs/architecture.md`, `docs/roadmap.md` i `docs/adr/0004-explicit-excerpt-api-boundary.md`.

### Testy i wyniki

- `pytest -q`: 22 testy zaliczone;
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: 33 pliki poprawnie sformatowane;
- `alembic check`: brak nowych operacji migracyjnych;
- ostrzeżenia dotyczą wyłącznie deprecjacji `TestClient` oraz adaptera dat SQLite w Pythonie 3.12.

### Decyzje techniczne

- OpenAPI jest źródłem prawdy dla klienta frontendowego;
- FTS składa zapytanie wyłącznie z bezpiecznie cytowanych tokenów;
- statystyki szczegółów i sąsiedztwo są liczone na aktywnych zdarzeniach zgodnych z prywatnością;
- pełna treść nie jest automatycznie zwracana przez wyszukiwanie ani wystąpienia.

### Znane ograniczenia i otwarte kwestie

- rola jest obecnie dostępna tylko dla zdarzeń ChatGPT, a dla przyszłych adapterów może być pusta;
- wyszukiwanie tokenów FTS używa semantyki `AND`, bez rankingu semantycznego;
- frontend nie jest jeszcze zaimplementowany.

### Następny krok

Zbudować pojedynczy widok React/Sigma z filtrem czasu, wyborem tematu, wystąpieniami i kontekstem.

### Commity

- baza bloku: `2b42516`;
- typowany kontrakt API: `47366ec`.

## 2026-07-13 — Wizualny pionowy wycinek atlasu

### Wykonane zadania

- zbudowano responsywny, trzykolumnowy widok React z grafem Sigma/Graphology;
- dodano wyszukiwanie tematów, filtry źródła, kategorii, progów, limitu i sąsiedztwa;
- dodano wybór miesięcznego zakresu czasu i aktualizację intensywności grafu;
- zaimplementowano wybór węzła, statystyki, wykres miesięczny, sąsiadów i paginowane fragmenty;
- dodano ładowanie ograniczonego kontekstu po świadomym wyborze fragmentu;
- obsłużono ładowanie, pusty wynik, błąd backendu, brak daty, brak fragmentów i długi tekst;
- dodano stabilne pozycje, logarytmiczny rozmiar węzłów i adaptacyjny ForceAtlas2;
- zweryfikowano render oraz pionowy przepływ na rzeczywistej lokalnej bazie bez raportowania treści.

### Zmienione pliki

- `frontend/src/App.tsx`, `api.ts`, `filters.ts`, `graph.ts`, `types.ts` i `styles.css`;
- `frontend/src/components/`;
- `frontend/tests/`;
- `frontend/index.html`, `package.json`, `package-lock.json`, `tsconfig.json` i `vite.config.ts`;
- `README.md`;
- `docs/roadmap.md`, `docs/progress.md` i `docs/adr/0005-react-sigma-local-visualization.md`.

### Testy i wyniki

- `npm test`: 9 testów zaliczonych w 4 plikach;
- `npm run build`: produkcyjny bundle zbudowany, 61 modułów;
- integracja Vite proxy → rzeczywisty backend: HTTP 200 dla metadanych, grafu, szczegółów i wystąpień;
- test wizualny headless przy 1440×900: układ i graf wyrenderowane poprawnie;
- na realnych danych domyślny widok 100 węzłów przy progu `0.15` zawiera 99 relacji.

### Decyzje techniczne

- domyślny próg relacji `0.15` chroni pierwszy widok przed gęstym grafem, pozostając regulowany;
- logarytmiczna skala wielkości ogranicza dominację największych tematów;
- pozycje są deterministyczne i buforowane w sesji, a układ dużych grafów redukuje liczbę iteracji;
- klient używa wyłącznie względnych adresów `/api`, bez CORS, zewnętrznych fontów, CDN i telemetrii.

### Znane ograniczenia i otwarte kwestie

- jakość 494 tematów potwierdza użyteczność grafu do oceny, ale widoczne są nadal ogólne słowa i tematy wielojęzyczne wymagające późniejszego strojenia stopwords;
- przy ręcznym obniżeniu progu do zera 100 węzłów może utworzyć kilka tysięcy krawędzi;
- Node.js nie jest zainstalowany na hoście testowym, dlatego testy uruchomiono w izolowanym kontenerze bez montowania `sources/` i `data/`;
- uruchamianie całego stosu przez Compose pozostaje następnym blokiem.

### Następny krok

Dodać produkcyjne obrazy backendu i frontendu oraz lokalny Docker Compose z kontrolą gotowości.

### Commity

- baza bloku: `47366ec`;
- wizualny pionowy wycinek: `1ea6133`.

## 2026-07-13 — Lokalne uruchamianie Compose i walidacja Visual MVP

### Wykonane zadania

- dodano wieloetapowy obraz frontendu i produkcyjne serwowanie przez Nginx;
- backend automatycznie wykonuje migracje przed startem i nie zapisuje access logów;
- połączono frontend z backendem wewnętrznym proxy `/api`;
- dodano healthchecki, zależność od gotowego backendu i publikację wyłącznie na `127.0.0.1`;
- wykluczono prywatne dane, archiwa, bazy, środowiska i wyniki z kontekstu Docker;
- zweryfikowano pełny stos na rzeczywistej lokalnej bazie, raportując wyłącznie liczniki;
- wykonano końcowy audyt bieżącego drzewa i całej historii Git.
- utworzono pojedynczy raport ukończonego kamienia milowego Visual MVP.

### Zmienione pliki

- `.dockerignore`;
- `backend/Dockerfile`;
- `frontend/Dockerfile` i `frontend/nginx.conf`;
- `docker-compose.yml`;
- `README.md`;
- `docs/roadmap.md` i `docs/progress.md`.
- `docs/reports/2026-07-13-visual-mvp.md`.

### Testy i wyniki

- `pytest -q`: 22 testy zaliczone;
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: 33 pliki poprawnie sformatowane;
- `alembic check`: brak nowych operacji migracyjnych;
- `npm test`: 9 testów zaliczonych;
- `npm run build`: produkcyjny bundle zbudowany;
- `npm audit --audit-level=high`: zero podatności;
- `docker-compose config --quiet`: zaliczone;
- `docker-compose build --pull`: oba obrazy zbudowane, kontekst 429,1 KiB;
- healthcheck i integracja HTTP Compose: frontend i backend zdrowe, HTML, metadane i graf odpowiadają 200;
- inspekcja obrazów: brak `sources/`, `data/` i `.env`;
- inspekcja montowań: `sources/` tylko do odczytu, `data/` zapisywalne lokalnie;
- audyt Git: zero prywatnych artefaktów, sekretów, e-maili i lokalnych ścieżek w śledzonej historii.

### Decyzje techniczne

- frontend produkcyjny używa nieuprzywilejowanego Nginx na porcie 8080;
- kontener backendu działa jako UID 1000, stosuje migracje i publikuje port wyłącznie lokalnie;
- bind mount `data/` zachowuje istniejącą lokalną bazę, zamiast tworzyć pusty nazwany wolumen;
- `.dockerignore` chroni dane już na granicy kontekstu buildu.

### Znane ograniczenia i otwarte kwestie

- lokalne środowisko ma starsze `docker-compose` 1.29 i legacy builder; konfiguracja jest zgodna, ale warto później przejść na Compose v2/BuildKit;
- ostrzeżenia testów Pythona dotyczą deprecjacji zależności, nie błędów aplikacji;
- licencja pozostaje celowo nieustalona.

### Następny krok

Po użytkowej ocenie atlasu dostroić stopwords i progi tematów na podstawie obserwacji wizualnych, bez dodawania embeddingów przed tą oceną.

### Commity

- baza bloku: `1ea6133`;
- Compose, walidacja i audyt: `2088608`.
