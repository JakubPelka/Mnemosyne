# Historyczna specyfikacja projektu

> Ten dokument archiwizuje pierwotny brief i może zawierać wcześniejsze lub zastąpione założenia. Nie jest aktywną instrukcją dla Codex. Aktualny stan i reguły opisują `AGENTS.md`, `docs/architecture.md`, `docs/roadmap.md` oraz ADR-y w `docs/adr/`.

Poniżej znajduje się pierwotna instrukcja dla pierwszego etapu. Celowo ograniczała zakres, ale od początku prowadziła do działającego lokalnego webowego atlasu rozmów z siecią, czasem i dostępem do źródłowych fragmentów.

⸻

Instrukcja dla Codex — etap 1 projektu

Kontekst projektu

Chcę zbudować lokalną aplikację do analizy pełnego eksportu moich rozmów z ChatGPT.

Docelowo projekt ma stać się interaktywnym, czasowym grafem wiedzy pokazującym:

* jakie tematy pojawiały się w rozmowach,
* kiedy były aktywne,
* jak często do nich wracałem,
* które tematy występowały razem,
* jak projekty i zainteresowania rozwijały się w czasie,
* z jakich konkretnych fragmentów rozmów wynikają poszczególne węzły i relacje.

W przyszłości mogą dojść dane przestrzenne, zdjęcia, EXIF, historia lokalizacji, kalendarz, repozytoria i inne źródła. Nie implementuj ich teraz.

Pierwszy etap ma stworzyć solidny, bezpieczny fundament pod dalszy rozwój.

⸻

Najważniejsza zasada bezpieczeństwa

Eksport rozmów zawiera prywatne dane i nigdy nie może zostać zapisany w repozytorium GitHub ani umieszczony w historii Git.

Surowe dane, lokalna baza, cache, logi zawierające treść rozmów oraz wygenerowane fragmenty muszą być blokowane przez .gitignore.

Repozytorium może zawierać wyłącznie:

* kod źródłowy,
* dokumentację,
* testy,
* przykładowe dane syntetyczne,
* schematy danych,
* konfigurację bez sekretów.

Nie kopiuj prawdziwych fragmentów rozmów do testów, README, logów ani komunikatów błędów.

Projekt powinien być domyślnie traktowany jako PRIVATE przynajmniej do czasu pełnego audytu danych i kodu.

⸻

Cel pierwszej iteracji

Zbuduj działający lokalnie prototyp, który:

1. przyjmuje archiwum eksportu ChatGPT lub rozpakowany katalog,
2. automatycznie rozpoznaje strukturę eksportu,
3. odczytuje rozmowy i wiadomości,
4. zapisuje ich znormalizowany indeks w lokalnej bazie SQLite,
5. generuje podstawowe tematy i słowa kluczowe,
6. buduje graf współwystępowania tematów,
7. udostępnia lokalny webowy interfejs,
8. pozwala filtrować graf według czasu,
9. pozwala kliknąć węzeł i zobaczyć źródłowe fragmenty rozmów,
10. nie wysyła żadnych danych do zewnętrznych usług.

Webowy interfejs z grafem i czasem jest częścią MVP, a nie dodatkiem na później.

⸻

Zakres pierwszego zadania

1. Najpierw inspekcja danych

Po otrzymaniu eksportu:

* nie zakładaj z góry konkretnej nazwy pliku,
* przeanalizuj strukturę katalogów i plików,
* zidentyfikuj pliki zawierające rozmowy,
* sprawdź format dat, identyfikatorów, wiadomości i relacji między wiadomościami,
* przygotuj krótki raport techniczny w docs/export_format.md.

Raport powinien opisywać:

* jakie pliki znaleziono,
* które zawierają rozmowy,
* strukturę pojedynczej rozmowy,
* strukturę pojedynczej wiadomości,
* sposób odtwarzania kolejności wiadomości,
* nietypowe lub brakujące dane,
* elementy eksportu, które na razie zostaną pominięte.

Nie umieszczaj w raporcie prawdziwej treści rozmów. Używaj wyłącznie zanonimizowanych lub syntetycznych przykładów.

⸻

2. Parser eksportu

Stwórz parser, który:

* przyjmuje ścieżkę do ZIP-a albo rozpakowanego katalogu,
* nie modyfikuje plików źródłowych,
* obsługuje błędy i częściowo uszkodzone rekordy,
* zapisuje ostrzeżenia bez ujawniania treści wiadomości,
* jest przygotowany na niewielkie zmiany formatu eksportu.

Parser powinien tworzyć znormalizowane rekordy:

Conversation

* conversation_id
* title
* created_at
* updated_at
* source
* message_count

Message

* message_id
* conversation_id
* parent_message_id
* role
* created_at
* text
* sequence_number
* content_type

Zachowaj stabilne identyfikatory źródłowe, aby później można było aktualizować bazę kolejnym eksportem bez tworzenia duplikatów.

⸻

3. Lokalna baza danych

Użyj SQLite.

Minimalne tabele:

* conversations
* messages
* topics
* message_topics
* topic_edges
* import_runs

Baza ma umożliwiać:

* wyszukiwanie pełnotekstowe,
* filtrowanie po dacie,
* odczyt wiadomości wokół wskazanego fragmentu,
* ponowny import tego samego archiwum bez duplikowania rekordów,
* późniejsze dodanie embeddingów i kolejnych źródeł danych.

Rozważ SQLite FTS5 dla wyszukiwania tekstowego.

Plik bazy danych musi być ignorowany przez Git.

⸻

4. Pierwsze wydobywanie tematów

Na tym etapie nie korzystaj z płatnych API ani usług chmurowych.

Zastosuj prostą, lokalną metodę:

* normalizacja tekstu,
* usunięcie typowych słów pustych,
* n-gramy,
* częstotliwość słów,
* proste słowa kluczowe,
* opcjonalnie lokalne biblioteki typu KeyBERT lub spaCy, o ile nie komplikują znacząco instalacji.

Uwzględnij przynajmniej język polski, szwedzki i angielski.

Temat powinien mieć:

* nazwę,
* liczbę wiadomości,
* liczbę rozmów,
* pierwsze wystąpienie,
* ostatnie wystąpienie,
* intensywność w poszczególnych miesiącach.

Nie próbuj jeszcze budować idealnej ontologii ani „cyfrowego bliźniaka”. Potrzebny jest wystarczająco dobry indeks do pierwszej wizualizacji.

⸻

5. Graf relacji

Na potrzeby MVP:

* węzeł oznacza temat,
* wielkość węzła odpowiada intensywności,
* krawędź oznacza współwystępowanie tematów,
* grubość krawędzi odpowiada liczbie wspólnych rozmów lub wiadomości,
* graf można ograniczyć do wybranego przedziału czasu.

Zaimplementuj ochronę przed powstaniem nieczytelnego „kłębka przewodów”:

* minimalny próg liczby wystąpień,
* minimalny próg siły relacji,
* limit widocznych węzłów,
* filtrowanie kategorii,
* możliwość pokazania najbliższych sąsiadów wybranego węzła,
* możliwość ukrycia słabych połączeń.

⸻

6. Webowy interfejs MVP

Aplikacja ma działać lokalnie w przeglądarce.

Minimalny interfejs powinien zawierać:

Widok główny

* interaktywny graf,
* zoom i przesuwanie,
* wyszukiwarkę tematów,
* filtrowanie po liczbie wystąpień,
* filtrowanie po sile relacji,
* przełącznik ograniczający graf do sąsiadów wybranego węzła.

Oś czasu

* wybór zakresu dat,
* filtrowanie grafu według wybranego okresu,
* zmiana wielkości węzłów na podstawie intensywności w danym okresie,
* prosty tryb odtwarzania miesiąc po miesiącu może zostać dodany później, ale architektura powinna go umożliwiać.

Panel szczegółów

Po kliknięciu tematu pokaż:

* nazwę tematu,
* liczbę rozmów i wiadomości,
* pierwsze i ostatnie wystąpienie,
* wykres intensywności w czasie,
* najczęstsze tematy powiązane,
* listę rozmów i źródłowych fragmentów.

Po kliknięciu fragmentu pokaż:

* treść wskazanej wiadomości,
* kilka wiadomości przed nią,
* kilka wiadomości po niej,
* tytuł rozmowy,
* datę,
* stabilny wewnętrzny identyfikator źródła.

Nie pokazuj całej historii rozmowy automatycznie. Ładuj kontekst dopiero na żądanie.

⸻

Preferowany stos technologiczny

Dobierz możliwie prosty i trwały stos.

Propozycja:

Backend:
Python
FastAPI
SQLite
SQLAlchemy lub prosty sqlite3
Frontend:
React lub lekki TypeScript
Sigma.js + Graphology
biblioteka do osi czasu lub własny range slider
Analiza:
Python
proste NLP lokalne
bez zewnętrznych API
Uruchomienie:
Docker Compose
oraz możliwość uruchomienia lokalnie bez Dockera

Możesz zaproponować lepszy stos, ale przed dużą zmianą uzasadnij decyzję.

Nie buduj ciężkiej infrastruktury wymagającej Neo4j, Elasticsearch, Kubernetes ani osobnego serwera bazodanowego na tym etapie.

⸻

Proponowana struktura repozytorium

project-root/
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── pyproject.toml
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── importers/
│   │   ├── models/
│   │   ├── services/
│   │   └── main.py
│   └── tests/
├── frontend/
│   ├── src/
│   └── tests/
├── scripts/
│   ├── inspect_export.py
│   ├── import_export.py
│   └── build_topic_graph.py
├── docs/
│   ├── architecture.md
│   ├── export_format.md
│   ├── privacy.md
│   └── roadmap.md
├── sample_data/
│   ├── README.md
│   └── synthetic_conversations.json
├── data/
│   └── .gitkeep
└── output/
    └── .gitkeep

⸻

Wymagany .gitignore

Przygotuj .gitignore, który blokuje przynajmniej:

# Private source data
data/*
!data/.gitkeep
imports/
exports/
archive/
archives/
raw_data/
private_data/
# Chat exports
conversations.json
chat.html
*.zip
*.tar
*.tar.gz
*.7z
# Generated databases and indexes
*.db
*.sqlite
*.sqlite3
*.parquet
*.feather
*.pkl
# Generated analysis
output/*
!output/.gitkeep
cache/
.cache/
embeddings/
indexes/
logs/
# Secrets and local configuration
.env
.env.*
!.env.example
*.key
*.pem
*.token
secrets/
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
venv/
# Node
node_modules/
dist/
build/
.vite/
# IDE and OS
.vscode/
.idea/
.DS_Store
Thumbs.db

Sprawdź również, czy żaden prywatny plik nie jest już śledzony przez Git. Samo dodanie wpisu do .gitignore nie usuwa pliku z historii repozytorium.

⸻

Dokumentacja bezpieczeństwa

Utwórz docs/privacy.md opisujący:

* że wszystkie dane użytkownika są przetwarzane lokalnie,
* że projekt nie wysyła treści rozmów do zewnętrznych usług,
* które katalogi mogą zawierać prywatne dane,
* jak bezpiecznie usunąć lokalną bazę,
* jak sprawdzić repozytorium przed publikacją,
* jak używać wyłącznie syntetycznych danych w testach.

README powinno zawierać wyraźne ostrzeżenie:

Nigdy nie dodawaj eksportu ChatGPT, lokalnej bazy ani wygenerowanych indeksów do repozytorium.

⸻

Testy

Przygotuj syntetyczny zestaw testowy zawierający:

* kilka rozmów,
* różne daty,
* rozgałęzione wiadomości,
* brakujące daty,
* pustą treść,
* kilka języków,
* powtarzające się tematy.

Testy powinny sprawdzać:

* poprawność parsera,
* zachowanie kolejności wiadomości,
* deduplikację importów,
* wyszukiwanie pełnotekstowe,
* filtrowanie po dacie,
* budowę relacji między tematami,
* brak zapisywania prywatnej treści w logach.

⸻

Kolejność wykonania

Pracuj małymi etapami i commitami:

1. struktura repozytorium i dokumentacja,
2. .gitignore i zasady prywatności,
3. inspektor eksportu,
4. parser,
5. SQLite i import,
6. testy danych syntetycznych,
7. podstawowe wydobywanie tematów,
8. generowanie grafu,
9. API,
10. frontend z grafem,
11. oś czasu,
12. panel źródłowych fragmentów.

Po każdym większym kroku:

* uruchom testy,
* pokaż zmienione pliki,
* krótko opisz decyzje techniczne,
* nie wykonuj dużej przebudowy bez wcześniejszego uzasadnienia.

⸻

Kryteria ukończenia pierwszej wersji

Pierwszy etap uznajemy za ukończony, gdy użytkownik może:

1. wskazać lokalne archiwum eksportu,
2. zaimportować rozmowy,
3. uruchomić aplikację lokalnie,
4. zobaczyć sieć tematów,
5. ograniczyć ją do wybranego okresu,
6. wyszukać temat,
7. kliknąć w węzeł,
8. zobaczyć rozmowy i fragmenty źródłowe,
9. rozwinąć kontekst wybranego fragmentu,
10. zrobić to bez przesyłania danych poza własny komputer.

⸻

Poza zakresem pierwszej wersji

Na razie nie implementuj:

* danych z Google Maps Timeline,
* EXIF i zdjęć,
* kalendarza,
* Gmaila,
* GitHuba,
* Home Assistanta,
* analizy emocji,
* predykcji decyzji,
* cyfrowego bliźniaka,
* automatycznej narracji historii projektu,
* pełnego grafu poznawczego,
* chmurowych modeli embeddingowych,
* publicznego hostingu.

Zaprojektuj kod tak, aby można było dodać te elementy później, ale nie komplikuj przez nie MVP.

⸻

Pierwsza odpowiedź Codexa powinna zawierać

Zanim zacznie pisać kod, niech:

1. sprawdzi zawartość repozytorium,
2. sprawdzi status Git,
3. oceni ryzyko przypadkowego dodania danych prywatnych,
4. zaproponuje minimalną architekturę,
5. przedstawi plan pierwszych 3–5 małych commitów,
6. wskaże decyzje, które wymagają Twojej akceptacji.

Najważniejsza reguła projektu:

Najpierw bezpieczny import i działający wizualny atlas. Dopiero później zaawansowana inteligencja.

Tak — warto to dopisać teraz, ale tylko jako wymóg architektoniczny, bez implementowania kolejnych importerów.

Dodaj do instrukcji Codexa poniższy fragment.

⸻

Wymóg rozszerzalności źródeł danych

Eksport ChatGPT jest pierwszym źródłem danych, ale nie może być traktowany jako jedyny ani centralny format całego systemu.

Projekt powinien od początku rozdzielać:

* wspólny model danych,
* logikę importu,
* źródło pochodzenia danych,
* analizę tematów,
* warstwę wizualizacji.

Parser eksportu ChatGPT powinien być jednym z adapterów źródłowych, a nie fundamentem, od którego zależy cała aplikacja.

Docelowe przyszłe źródła

W kolejnych etapach mogą zostać dodane między innymi:

* dziennik w formacie HTML,
* historia wyszukiwań Google,
* Google Maps Timeline,
* zdjęcia i metadane EXIF,
* kalendarz,
* dane z LinkedIn,
* dane z Facebooka,
* repozytoria GitHub,
* inne lokalne archiwa i dokumenty.

Nie implementuj tych źródeł teraz. Zaprojektuj jednak architekturę tak, aby dodanie kolejnego importera nie wymagało przebudowy bazy danych, API ani interfejsu.

⸻

Wspólny model danych

Zamiast opierać całą bazę wyłącznie na tabelach conversations i messages, wprowadź wspólny model zdarzeń.

Minimalny rdzeń powinien obejmować:

Source

* source_id
* source_type
* name
* imported_at
* source_version
* original_path_hash
* metadata

Event

* event_id
* source_id
* source_record_id
* event_type
* timestamp_start
* timestamp_end
* title
* text
* url
* location_id
* privacy_level
* raw_payload_reference
* created_at
* updated_at

Entity

* entity_id
* entity_type
* name
* normalized_name

Topic

* topic_id
* name
* category
* first_seen_at
* last_seen_at

Relation

* relation_id
* source_event_id
* target_event_id
* relation_type
* weight

EventTopic

* event_id
* topic_id
* weight

EventEntity

* event_id
* entity_id
* relation_type

Tabele specyficzne dla poszczególnych źródeł mogą rozszerzać wspólny model.

Przykładowo:

chatgpt_conversations
chatgpt_messages
journal_entries
search_queries
social_posts
photos
location_records

Każdy rekord specyficzny dla źródła powinien wskazywać odpowiadający mu rekord w tabeli events.

⸻

Interfejs adaptera źródłowego

Zdefiniuj wspólny interfejs, np. SourceAdapter.

Każdy adapter powinien implementować przynajmniej:

class SourceAdapter:
    def inspect(self, input_path): ...
    def validate(self, input_path): ...
    def parse(self, input_path): ...
    def normalize(self, raw_record): ...
    def get_source_metadata(self): ...

Pierwszą implementacją będzie:

ChatGPTExportAdapter

W przyszłości mogą powstać:

JournalHtmlAdapter
GoogleSearchHistoryAdapter
GoogleTimelineAdapter
PhotoExifAdapter
LinkedInExportAdapter
FacebookExportAdapter

Kod analizy tematów, grafu i osi czasu nie może zależeć bezpośrednio od formatu eksportu ChatGPT.

⸻

Aktualizacja schematu MVP

W pierwszej wersji nadal implementuj wyłącznie import ChatGPT.

Przepływ powinien wyglądać tak:

ChatGPT export
    ↓
ChatGPTExportAdapter
    ↓
normalized Events
    ↓
Topics / Entities / Relations
    ↓
API
    ↓
Graph + Timeline + Source Browser

Interfejs użytkownika powinien już rozpoznawać pole source_type, nawet jeżeli na początku dostępna będzie tylko wartość:

chatgpt

Dzięki temu później będzie można dodać filtrowanie:

ChatGPT
Dziennik
Google Search
Zdjęcia
Lokalizacja
LinkedIn
Facebook

bez przebudowy frontendu.

⸻

Zasada dotycząca prywatności

Każde źródło może mieć inny poziom wrażliwości.

Dodaj pole:

privacy_level

z wartościami roboczymi:

private
sensitive
personal
public

Domyślnie wszystkie rekordy importowane z ChatGPT, dziennika, historii wyszukiwań, lokalizacji i mediów społecznościowych powinny otrzymywać poziom:

private

Dane osób trzecich powinny być traktowane jako szczególnie wrażliwe.

⸻

Kryterium architektoniczne

Pierwszy etap jest poprawnie zaprojektowany tylko wtedy, gdy można później dodać importer dziennika HTML poprzez:

1. utworzenie nowego adaptera,
2. mapowanie wpisów na wspólny model Event,
3. dodanie testów,
4. opcjonalne rozszerzenie panelu szczegółów,

bez przebudowy:

* grafu,
* osi czasu,
* systemu tematów,
* API filtrowania,
* mechanizmu wyszukiwania.

⸻

W poprzedniej instrukcji zmieniłbym także schemat:

conversations
messages
topics
message_topics
topic_edges
import_runs

na:

sources
events
entities
topics
event_topics
event_entities
event_relations
import_runs
chatgpt_conversations
chatgpt_messages

To niewielkie rozszerzenie teraz oszczędzi później dużej migracji całego projektu. Nie trzeba jednak budować od razu uniwersalnego kombajnu — wystarczy wspólny rdzeń i pierwszy adapter ChatGPTExportAdapter.

⸻

Raportowanie postępu

Nie twórz osobnego raportu po każdym małym zadaniu lub commicie.

Po każdym logicznym bloku prac dopisz zwięzłą sekcję do `docs/progress.md`, zawierającą:

* datę i nazwę bloku,
* wykonane zadania,
* zmienione pliki,
* uruchomione testy i ich wyniki,
* decyzje techniczne,
* znane ograniczenia,
* otwarte kwestie,
* proponowany następny krok,
* identyfikatory commitów.

Osobny raport w `docs/reports/` twórz wyłącznie po ukończeniu kamienia milowego, większej migracji, istotnej zmianie architektury lub audycie bezpieczeństwa. Nazwa raportu ma format `YYYY-MM-DD-short-milestone-name.md`.

Raport kamienia milowego powinien zawierać również aktualny stan funkcjonalny, kryteria ukończenia, wyniki testów, znane błędy, ryzyka prywatności i bezpieczeństwa, status danych prywatnych względem Git, instrukcję uruchomienia oraz rekomendowany następny etap.

Ważne decyzje architektoniczne dokumentuj jako ADR w `docs/adr/`.

Nie uznawaj zadania za ukończone wyłącznie na podstawie deklaracji. Ukończenie musi być poparte testami, stanem repozytorium i możliwymi do sprawdzenia rezultatami.
