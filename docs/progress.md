# Postęp prac

Ten dokument jest zwięzłym dziennikiem logicznych bloków prac. Szczegółowe raporty powstają tylko dla kamieni milowych, większych migracji, zmian architektury i audytów bezpieczeństwa.

## 2026-07-14 — Korekta modelu po regresji Topic Quality

### Wykonane zadania

- potwierdzono, że `main` pozostaje na stabilnym Visual MVP, bez merge'a regresyjnej gałęzi;
- zidentyfikowano automatyczną promocję singletonów jako główną przyczynę regresji;
- dodano odrzucanie tokenów kodowych i dokumentacyjnych bez wpływu na FTS;
- dodano wykrywanie akronimów z oryginalnej pisowni i ochronę ich przypisań przed limitem rankingu;
- zablokowano promocję 1:1: automatyczny temat wymaga grupy, frazy albo akronimu;
- przywrócono pełne szczegóły, intensywność, sąsiedztwo, fragmenty i kontekst dla terminów;
- dodano warstwowe, niewrażliwe na wielkość liter wyszukiwanie nazw, aliasów i kandydatów;
- ustawiono „Terminy” jako domyślną warstwę, a „Tematy (beta)” jako opcjonalną;
- zapisano korektę architektury w ADR 0007 i unieważniono błędny raport ukończenia.

### Zmienione pliki

- NLP i analiza: `backend/app/nlp/`, `backend/app/services/topics.py`;
- warstwy zapytań i API: `backend/app/services/catalog.py`, `graph.py`, `backend/app/api/`;
- frontend: `frontend/src/` oraz testy klienta i komponentów;
- testy regresyjne: `backend/tests/test_topic_quality.py`, `test_database_import.py`, `test_api.py`;
- dokumentacja: `docs/architecture.md`, ADR 0007 i historyczny raport Topic Quality v1.

### Testy i wyniki

- `pytest -q`: 39 testów zaliczonych;
- `ruff check backend scripts`: zaliczone;
- frontend: 11 testów i produkcyjny build zaliczone;
- syntetyczne `GIS/gis`: wyszukiwanie niewrażliwe na wielkość liter zaliczone;
- pięć wskazanych unigramów kodowych odrzuconych, zero odpowiadających tematów;
- trzy ręcznie zatwierdzone tematy dają dokładnie trzy węzły, bez dopełniania do 30;
- warstwa terminów zachowuje szczegóły, intensywność, sąsiedztwo, fragmenty i kontekst.

### Decyzje techniczne, ograniczenia i następny krok

- parametr `layer` jest nowym jawnym kontraktem; `view` grafu pozostaje kompatybilnym aliasem;
- rozpoznanie akronimu wymaga oryginalnej pisowni wersalikami albo ręcznego override;
- nieśledzony `start.sh` pozostaje nietknięty i poza zakresem commitów;
- rzeczywista baza nie została jeszcze przebudowana po korekcie;
- następny krok: przebudowa lokalna, bezpieczna kontrola wyszukiwania akronimu i liczników obu warstw.

### Commity

- baza naprawy: `7a65119`;
- przywrócenie eksploracji terminów i uczciwych warstw: `fdf31a1`.

## 2026-07-14 — Strojenie promocji na lokalnej bazie

### Wykonane zadania

- przebudowano lokalną bazę iteracyjnie, raportując wyłącznie agregaty;
- odrzucono samodzielną promocję akronimu do tematu bez grupy lub zatwierdzenia;
- uznano frazę za temat tylko wtedy, gdy informacyjnie dominuje co najmniej jeden składnik;
- zachowano akronimy jako aktywne, wyszukiwalne terminy niezależnie od limitu przypisań;
- uogólniono etykiety UI tak, aby nie nazywały terminów tematami.

### Testy i wyniki

- lokalny akronim kontrolny jest aktywny i ma 750 przypisań do zdarzeń;
- osiem wskazanych tokenów kodowych daje zero aktywnych tematów;
- 195 tematów beta ma 405 przypisanych terminów; zero tematów ma tylko jeden termin;
- aktywne tematy beta składają się wyłącznie z grup wariantów albo fraz z aliasem składnika;
- końcowy przebieg: 6 906 kandydatów, 4 485 aktywnych terminów, 2 421 odrzuconych kandydatów;
- 371 kandydatów odrzucono z powodu klasy `code_token`.

### Decyzje, ograniczenia i następny krok

- liczba 195 nie jest celem do wypełnienia: wynika z warunku wieloterminowego, a nie limitu grafu; domyślnie widoczne są terminy;
- jakość nazw grup nadal wymaga oceny użytkowej, dlatego warstwa pozostaje oznaczona jako beta;
- przebudowa nadal zużywa około 2,25 GiB pamięci i trwa około 35 s;
- następny krok: pełna walidacja API/UI, migracji, Compose i prywatności.

### Commity

- baza bloku: `fdf31a1`;
- strojenie promocji na lokalnej bazie: `b8898cb`.

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

## 2026-07-14 — Baseline jakości terminów

### Wykonane zadania

- utworzono gałąź `feat/topic-quality-v1` z czystego `main`;
- potwierdzono ignorowanie lokalnej bazy, źródeł i przyszłych lokalnych nadpisań;
- zmierzono strukturę 494 dotychczasowych rekordów bez ujawniania nazw;
- policzono klasy problemów, rozkłady wiadomości i kontekstów oraz bazowy graf;
- zapisano bezpieczny raport `docs/reports/2026-07-14-topic-quality-baseline.md`.

### Zmienione pliki

- `docs/reports/2026-07-14-topic-quality-baseline.md`;
- `docs/progress.md`.

### Testy i wyniki

- audyt ignorowania `sources/`, SQLite i `data/local_topic_overrides.yaml`: zaliczony;
- śledzone pliki ignorowane: 0;
- prywatne artefakty w nazwach plików historii: 0;
- baseline: 445 unigramów, 49 bigramów, 12 terminów z artefaktami i 97 bardzo krótkich;
- dotychczasowy graf domyślny: 100 węzłów i 99 relacji.

### Decyzje, ograniczenia i następny krok

- pomiar języka jest jawnie heurystyczny i służy wyłącznie klasyfikacji problemu;
- baseline nie zawiera nazw ani treści z lokalnej bazy;
- następny krok: trwały model `CandidateTerm → topic_terms → Topic` oraz wyjaśnialne reguły jakości.

### Commity

- baza sprintu: `16f6e86`;
- baseline jakości: `71db4b2`.

## 2026-07-14 — Lokalne filtry jakości kandydatów

### Wykonane zadania

- wydzielono wersjonowane leksykony stopwords dla polskiego, szwedzkiego i angielskiego;
- dodano listę artefaktów eksportu, cytowań i narzędzi wraz z wykrywaniem wariantów;
- zaimplementowano deterministyczny scoring i jawne powody odrzucenia;
- dodano reguły dla długości, częstości, niskiej informacji, identyfikatorów i tokenów technicznych;
- dodano wyjaśnialne preferowanie frazy nad pokrytym unigramem.

### Zmienione pliki

- `backend/app/nlp/lexicons.py`;
- `backend/app/nlp/quality.py`;
- `backend/tests/test_topic_quality.py`;
- `docs/progress.md`.

### Testy i wyniki

- `ruff check backend/app/nlp backend/tests/test_topic_quality.py`: zaliczone;
- `pytest -q backend/tests/test_topic_quality.py`: 6 testów zaliczonych.

### Decyzje, ograniczenia i następny krok

- klasyfikacja jest lokalna, deterministyczna i zapisuje pojedynczy główny powód odrzucenia;
- język jest wykrywany konserwatywnie na podstawie markerów leksykalnych, bez modelu zewnętrznego;
- reguła preferowania frazy wymaga zarówno przewagi wyniku, jak i co najmniej 50% pokrycia unigramu;
- następny krok: zapisać kandydatów i ich przypisania osobno od tematów.

### Commity

- baza bloku: `71db4b2`;
- filtry jakości kandydatów: `7b9282c`.

## 2026-07-14 — Rozdzielenie kandydatów i tematów

### Wykonane zadania

- dodano trwałe modele `CandidateTerm`, `TopicTerm` i `EventCandidateTerm`;
- rozszerzono `Topic` o status, pochodzenie i aktywność;
- przygotowano migrację zachowującą stare terminy i przypisania;
- przebudowano analizę na dwa etapy: kandydaci z oceną jakości oraz prezentacyjne tematy;
- dodano preferowanie fraz, proste grupowanie wariantów i idempotentną ponowną budowę;
- dodano bezpieczny format ręcznych tematów oraz ignorowane lokalne nadpisania;
- udokumentowano decyzję w ADR 0006.

### Zmienione pliki

- `backend/app/models/core.py`, `backend/app/models/__init__.py`;
- `backend/app/services/topics.py`, `backend/app/services/topic_overrides.py`;
- `backend/migrations/versions/703c9442f139_separate_candidate_terms_from_topics.py`;
- `scripts/build_topic_graph.py`, `sample_data/topic_overrides.example.yaml`;
- `backend/tests/test_database_import.py`, `backend/tests/test_topic_quality.py`, `backend/tests/test_api.py`;
- `docs/architecture.md`, `docs/adr/0006-candidate-terms-and-presented-topics.md`.

### Testy i wyniki

- `ruff check backend scripts`: zaliczone;
- testy jakości, migracji, przebudowy i dotychczasowego API: 24 zaliczone;
- migracja zachowuje syntetyczne rekordy starego schematu i ich przypisania;
- druga przebudowa nie tworzy duplikatów.

### Decyzje techniczne

- odrzucone terminy pozostają w bazie jako nieaktywne wraz z głównym powodem odrzucenia;
- stabilne identyfikatory powstają z postaci znormalizowanej lub jawnego identyfikatora ręcznego;
- ręczne aliasy mogą promować termin niezależnie od automatycznego filtra;
- plik `data/local_topic_overrides.yaml` pozostaje objęty ogólną regułą ignorowania `data/*`.

### Znane ograniczenia i otwarte kwestie

- grupowanie wariantów jest konserwatywną heurystyką, nie pełną lematyzacją;
- jakość progów wymaga jeszcze pomiaru na lokalnej bazie po domknięciu API i UI;
- rzeczywista baza nie została jeszcze zmigrowana ani przebudowana.

### Następny krok

Udostępnić terminy tematu oraz diagnostyczny graf kandydatów w typowanym API.

### Commity

- baza bloku: `7b9282c`;
- rozdzielenie kandydatów i tematów: `b691b21`.

## 2026-07-14 — API tematów i diagnostyka terminów

### Wykonane zadania

- dodano paginowaną listę aktywnych tematów w `GET /api/topics`;
- dodano skład tematu w `GET /api/topics/{topic_id}/terms`;
- rozszerzono graf o jawny parametr `view=topics|terms`;
- zachowano domyślny graf tematów i opcjonalny diagnostyczny wgląd w odrzucone terminy;
- wykluczono nieaktywne tematy z metadanych, wyszukiwania, szczegółów i grafu;
- podniesiono wersję kontraktu API do 0.3.0.

### Zmienione pliki

- `backend/app/api/routes.py`, `backend/app/api/schemas.py`;
- `backend/app/services/catalog.py`, `backend/app/services/graph.py`;
- `backend/app/main_version.py`, `backend/tests/test_api.py`.

### Testy i wyniki

- `ruff check backend`: zaliczone;
- testy API: 9 zaliczonych;
- OpenAPI zawiera jawne modele odpowiedzi nowych endpointów;
- odrzucone terminy nie są zwracane bez parametru diagnostycznego.

### Decyzje techniczne

- istniejący kształt odpowiedzi grafu jest współdzielony przez oba widoki, aby frontend nie potrzebował drugiego renderera;
- kategoria węzła diagnostycznego jawnie zawiera status kandydata;
- szczegóły i wystąpienia pozostają dostępne wyłącznie dla prezentacyjnych tematów.

### Znane ograniczenia i otwarte kwestie

- widok terminów jest narzędziem diagnostycznym i nie ma panelu szczegółów tematu;
- endpoint listy ma prostą paginację `limit/offset`, bez osobnego licznika całości.

### Następny krok

Dodać mały przełącznik widoku do istniejącego frontendu i ograniczyć domyślny graf do 30 węzłów.

### Commity

- baza bloku: `b691b21`;
- API tematów i diagnostyki: `ce10529`.

## 2026-07-14 — Domyślny graf tematów w UI

### Wykonane zadania

- dodano mały przełącznik „Tematy / Surowe terminy (diagnostyczne)”;
- ustawiono 30 węzłów jako domyślny limit grafu;
- zachowano wyszukiwanie, szczegóły, fragmenty i kontekst wyłącznie dla właściwych tematów;
- dodano czytelny stan prawego panelu dla widoku diagnostycznego;
- wyszukanie tematu automatycznie przywraca widok prezentacyjny.

### Zmienione pliki

- `frontend/src/types.ts`, `frontend/src/filters.ts`, `frontend/src/App.tsx`;
- `frontend/src/components/FilterPanel.tsx`;
- `frontend/tests/filters.test.ts`, `frontend/tests/api.test.ts`, `frontend/tests/components.test.tsx`.

### Testy i wyniki

- `npm test`: 11 testów zaliczonych;
- `npm run build`: TypeScript i produkcyjny bundle zbudowane poprawnie.

### Decyzje techniczne

- oba widoki korzystają z istniejącego komponentu Sigma i tego samego kontraktu grafu;
- filtr kategorii jest wyłączony dla surowych terminów, ponieważ kategorie należą do warstwy tematów;
- kliknięcie surowego terminu może go zaznaczyć na grafie, ale nie wywołuje endpointów szczegółów tematu.

### Znane ograniczenia i otwarte kwestie

- widok diagnostyczny celowo nie ma osobnego panelu metryk kandydata;
- walidacja została uruchomiona w lokalnym kontenerze Node, ponieważ host nie ma binariów Node.js.

### Następny krok

Przeprowadzić pełną walidację, migrację kopii i rzeczywistej lokalnej bazy, porównanie jakości oraz kontrolę Compose i Git.

### Commity

- baza bloku: `ce10529`;
- domyślny graf tematów w UI: `1703ab7`.

## 2026-07-14 — Walidacja kamienia milowego Topic Quality v1

### Wykonane zadania

- zmigrowano najpierw kopię, a następnie właściwą lokalną bazę;
- przebudowano analizę dwukrotnie i porównano bezpieczne agregaty;
- sprawdzono integralność SQLite, klucze obce, brak duplikatów i dryfu migracji;
- zweryfikowano oba widoki grafu na lokalnych danych bez ujawniania nazw;
- zbudowano i uruchomiono cały stos Compose, sprawdzono healthchecki, HTTP i montowania;
- wykonano audyt bieżącego drzewa i historii Git;
- utworzono raport `docs/reports/2026-07-14-topic-quality-v1.md` i zaktualizowano roadmapę.

### Zmienione pliki

- `README.md`;
- `docs/roadmap.md`, `docs/progress.md`;
- `docs/reports/2026-07-14-topic-quality-v1.md`.

### Testy i wyniki

- pełny `pytest -q`: 34 zaliczone;
- Ruff check i format: zaliczone;
- Alembic check, integralność SQLite i klucze obce: zaliczone;
- frontend: 11 testów, build i audit bez podatności;
- Compose: backend i frontend zdrowe, lokalne endpointy odpowiadają;
- audyt Git: brak prywatnych eksportów, baz, archiwów, sekretów i lokalnych ścieżek.

### Decyzje techniczne

- rzeczywiste wyniki dokumentowane są wyłącznie jako liczniki;
- strukturalna ocena grafu zastępuje zapisywanie zrzutu z prywatnymi etykietami;
- kopia bazy sprzed migracji znajduje się tymczasowo poza repozytorium w `/tmp` i nie jest śledzona.

### Znane ograniczenia i otwarte kwestie

- przebudowa trwa 33,11 s i osiąga około 2,18 GiB pamięci szczytowej;
- Compose 1.29 wymagał usunięcia starego kontenera po błędzie zgodności `ContainerConfig`;
- następna ocena jakości powinna być wykonana przez właściciela wizualnie, bez utrwalania prywatnych etykiet.

### Następny krok

Zoptymalizować pamięć ekstrakcji, a następnie dostroić heurystyki na podstawie lokalnej oceny 180 tematów.

### Commity

- baza bloku: `1703ab7`;
- końcowa walidacja i raport: `45d1097`.

## 2026-07-14 — Segmentacja prozy, kodu, komend i logów

### Wykonane zadania

- dodano regenerowalny model `EventSegment`, migrację i osobny indeks FTS5 segmentów;
- parser zachowuje kolejność prozy, kodu inline i blokowego, komend, logów, cytatów, tabel, linków oraz artefaktów narzędziowych;
- analiza tematów korzysta wyłącznie z segmentów dopuszczonych do analizy, podczas gdy kod i logi pozostają wyszukiwalne;
- dodano zakres wyszukiwania `all|prose|code|commands|logs`, typ dopasowania oraz etykiety w UI;
- import i przebudowa grafu odtwarzają segmenty deterministycznie bez zmiany pełnego `Event.text`;
- zmigrowano i przebudowano lokalną bazę, raportując wyłącznie agregaty.

### Zmienione pliki

- modele, migracje i usługi w `backend/app/` oraz `backend/migrations/`;
- kontrakty API i testy backendu;
- klient API, widok wyszukiwania treści i testy frontendu;
- `README.md`, `docs/architecture.md`, `docs/roadmap.md` i ADR 0008.

### Testy i wyniki

- backend: 43 testy zaliczone;
- frontend: 13 testów zaliczonych, produkcyjny build poprawny;
- migracja kopii i właściwej lokalnej bazy: integralność SQLite `ok`, 0 naruszeń kluczy obcych;
- 115 470 segmentów i odpowiadających wpisów FTS; przebudowa trwała 47,42 s i osiągnęła około 1,57 GiB pamięci szczytowej;
- Compose na alternatywnych portach: obie usługi zdrowe, wyszukiwanie każdego zakresu zwracało właściwy typ segmentu, montowanie źródeł pozostało tylko do odczytu;
- wyszukiwanie syntetycznego i lokalnego akronimu działa bez względu na wielkość liter; żaden z kontrolnych tokenów kodowych nie jest aktywnym tematem.

### Decyzje techniczne

- pełny tekst zdarzenia pozostaje źródłem kontekstu, a segmenty są odtwarzalną warstwą analityczną;
- proza ma wagę `1.0`, cytat `0.3`, a kod, komendy i logi `0.0` dla tematów;
- przyszły graf techniczny będzie osobną warstwą i nie zostanie połączony z głównym grafem rozmów.

### Znane ograniczenia i otwarte kwestie

- rozpoznawanie segmentów jest heurystyczne i będzie wymagało rozszerzeń dla nietypowego Markdownu oraz nieoznaczonych wyjść terminala;
- przebudowa całego lokalnego korpusu nadal ma zauważalny koszt pamięci;
- jakość 200 tematów beta wymaga wizualnej oceny właściciela; system nie promuje już singletonów tylko po to, aby wypełnić graf.

### Następny krok

Wykonać końcowy audyt Git i walidację pełnego zestawu poleceń, a następnie zamknąć naprawę regresji jednym raportem kamienia milowego.

### Commity

- baza bloku: `bcd35d4`;
- implementacja segmentacji: `4fb8e7b`.

## 2026-07-14 — Walidacja naprawy regresji Topic Quality

### Wykonane zadania

- wykonano kontrolę migracji, testów, formatowania, integralności lokalnej bazy i działania Compose;
- przeprowadzono audyt bieżącego drzewa oraz osiągalnej historii bez wypisywania prywatnej treści;
- zapisano raport `docs/reports/2026-07-14-topic-quality-regression-fix.md`;
- pozostawiono unieważniony raport pierwszej implementacji jako jawny zapis historyczny.

### Zmienione pliki

- `docs/progress.md`;
- `docs/reports/2026-07-14-topic-quality-regression-fix.md`.

### Testy i wyniki

- backend: 43 testy, Ruff i Alembic zaliczone;
- frontend: 13 testów i build zaliczone po implementacji;
- końcowa próba ponowienia testów frontendowych nie uruchomiła się z powodu braku Node na hoście i odmowy dostępu bieżącej sesji do demona Docker; kod frontendu nie zmienił się od poprzedniego zaliczonego przebiegu;
- audyt Git: brak śledzonych baz, eksportów, archiwów, logów, sekretów i jawnych lokalnych ścieżek.

### Decyzje techniczne

- naprawa pozostaje na `feat/topic-quality-v1`; nie jest scalana z `main` ani publikowana bez kolejnego polecenia użytkownika;
- raport ukończenia opiera się na wynikach testów, migracji, Compose i sprawdzalnych agregatach, nie na deklaracji.

### Znane ograniczenia i otwarte kwestie

- nieśledzony plik użytkownika `start.sh` pozostaje nietknięty i poza zakresem commitów;
- bieżące środowisko wykonawcze nie pozwoliło powtórzyć wcześniej zaliczonego przebiegu frontendowego przez Docker.

### Następny krok

Ocenić wizualnie jakość tematów beta, a po akceptacji zdecydować osobno o publikacji gałęzi.

### Commity

- segmentacja: `4fb8e7b`;
- końcowa walidacja i raport: commit zawierający niniejszy wpis.
