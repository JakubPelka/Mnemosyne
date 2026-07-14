# Roadmap i lista kontrolna

Ta lista odzwierciedla aktualne wymagania produktu. Trwałe instrukcje znajdują się w `AGENTS.md`, a pierwotny brief w `docs/project-specification-history.md`. Zrealizowane zadania pozostają na liście i są oznaczone `[x]`. Elementy rozpoczęte, ale jeszcze nieukończone, pozostają nieodhaczone z adnotacją **częściowo**.

## 0. Fundament i bezpieczeństwo repozytorium

- [x] Sprawdzić początkową zawartość repozytorium i status Git.
- [x] Ocenić ryzyko przypadkowego zapisania prywatnych danych.
- [x] Ustalić lokalny charakter aplikacji i brak zewnętrznych API oraz telemetrii.
- [x] Przygotować podstawową strukturę `backend/`, `frontend/`, `scripts/`, `docs/`, `sample_data/`, `data/` i `output/`.
- [x] Dodać `sources/` do `.gitignore`; katalog pozostaje lokalny i może nie istnieć w świeżym klonie.
- [x] Ignorować źródła, archiwa, bazy, indeksy, cache, logi, embeddingi, wyniki i sekrety.
- [x] Pozostawić w Git wyłącznie placeholdery `data/.gitkeep` i `output/.gitkeep`.
- [x] Usunąć testowy `sources/list.txt` z całej osiągalnej historii lokalnej i `origin/main`.
- [x] Dodać ostrzeżenie w README, aby nigdy nie commitować eksportów, baz ani indeksów.
- [x] Opisać lokalne przetwarzanie, katalogi prywatne, usuwanie danych i audyt przed publikacją w `docs/privacy.md`.
- [x] Ustalić, że surowe dane pozostają lokalnie w `sources/` i są otwierane tylko do odczytu.
- [x] Ustalić domyślny `privacy_level=private` dla danych importowanych.
- [x] Przeprowadzić pełny audyt kodu, bieżących plików i historii przed ewentualnym upublicznieniem projektu.
- [x] Oznaczyć faktyczny status repozytorium jako `TEMP PUBLIC` i wymagać audytu przed każdym push.
- [ ] Dodać licencję, kiedy zostanie podjęta decyzja o sposobie dystrybucji.

## 1. Architektura i uruchamianie

- [x] Wybrać Python, FastAPI, SQLite, React, TypeScript, Sigma.js i Graphology.
- [x] Wybrać SQLAlchemy 2.x do persystencji i Alembic do migracji schematu.
- [x] Opisać przepływ `SourceAdapter → Events → Topics/Entities/Relations → API → UI`.
- [x] Oddzielić wspólny model danych od formatu eksportu ChatGPT.
- [x] Zdefiniować protokół `SourceAdapter` z metodami `inspect`, `validate`, `parse`, `normalize` i `get_source_metadata`.
- [x] Dodać minimalną aplikację FastAPI z endpointem `/health`.
- [x] Przygotować `.env.example`, `pyproject.toml`, Dockerfile i `docker-compose.yml`.
- [x] Uruchomić i zweryfikować backend w czystym środowisku lokalnym.
- [x] Uruchomić i zweryfikować backend przez Docker Compose.
- [x] Dodać właściwy szkielet aplikacji Vite/React i punkt wejścia UI.
- [ ] Dodać wspólną obsługę konfiguracji, bez zapisywania sekretów w repozytorium.
- [ ] Dodać bezpieczne logowanie strukturalne bez tekstu wiadomości i prywatnych ścieżek.

## 2. Inspekcja pierwszego eksportu OpenAI / ChatGPT

- [x] Poczekać na pierwsze źródło w lokalnym katalogu `sources/`.
- [x] Obsłużyć zarówno archiwum ZIP, jak i rozpakowany katalog.
- [x] Nie zakładać konkretnej nazwy pliku zawierającego rozmowy.
- [x] Przejrzeć strukturę katalogów i plików bez modyfikowania źródła.
- [x] Zidentyfikować pliki zawierające rozmowy i wiadomości.
- [x] Ustalić format dat, identyfikatorów i typów treści.
- [x] Ustalić relacje rodzic–dziecko, kolejność wiadomości i znaczenie aktywnej gałęzi.
- [x] Zidentyfikować brakujące, nietypowe, uszkodzone i pomijane dane.
- [x] Uzupełnić `docs/export_format.md` bez prawdziwej treści, danych osobowych i jawnych lokalnych ścieżek.
- [x] Używać w raporcie wyłącznie opisów strukturalnych i syntetycznych przykładów.

## 3. Wspólny model danych i SQLite

- [x] Utworzyć migracje Alembic i schemat tabeli `sources`.
- [x] Utworzyć tabelę `events` z identyfikatorem źródłowym, czasem, tekstem, URL-em, prywatnością i lokalnym odwołaniem do surowego rekordu.
- [x] Utworzyć tabele `entities`, `topics`, `event_topics`, `event_entities`, `event_relations` i `import_runs`.
- [x] Utworzyć źródłowe tabele `chatgpt_conversations` oraz `chatgpt_messages` wskazujące odpowiadające rekordy `events`.
- [x] Zachować stabilne identyfikatory źródłowe.
- [x] Wymusić unikalność co najmniej pary `(source_id, source_record_id)`.
- [x] Zapisywać hash oryginalnej ścieżki zamiast jawnej ścieżki.
- [x] Zapewnić transakcyjny, idempotentny import bez duplikatów.
- [x] Przygotować aktualizację danych przy imporcie kolejnego eksportu; rekordy nieobecne w nowszym imporcie są oznaczane jako nieaktywne.
- [x] Dodać indeksy do filtrowania po dacie, źródle i typie zdarzenia.
- [x] Dodać SQLite FTS5 do lokalnego wyszukiwania pełnotekstowego.
- [x] Zapewnić odczyt kilku wiadomości przed i po wskazanym fragmencie.
- [ ] Pozostawić możliwość późniejszego dodania lokalnych embeddingów bez wymagania ich w MVP.
- [x] Zweryfikować migracje w nowej i istniejącej bazie oraz brak dryfu schematu.

## 4. `ChatGPTExportAdapter`

- [x] Zaimplementować `inspect()` zwracające wyłącznie bezpieczne metadane strukturalne.
- [x] Zaimplementować `validate()` z czytelnymi błędami bez ujawniania treści.
- [x] Zaimplementować strumieniowy lub pamięciowo bezpieczny `parse()` dla ZIP-a i katalogu.
- [x] Zaimplementować `normalize()` mapujące rekordy ChatGPT na wspólne zdarzenia.
- [x] Zaimplementować `get_source_metadata()` z `source_type=chatgpt`.
- [x] Normalizować pola rozmowy: ID, tytuł, daty, źródło i liczbę wiadomości.
- [x] Normalizować pola wiadomości: ID, rozmowę, rodzica, rolę, datę, tekst, kolejność i typ treści.
- [x] Poprawnie odtwarzać kolejność i rozgałęzienia wiadomości.
- [x] Obsługiwać brakujące daty, pustą treść, nieznane typy i częściowo uszkodzone rekordy.
- [x] Nie modyfikować ani automatycznie nie usuwać surowego eksportu.
- [x] Nie umieszczać treści wiadomości w ostrzeżeniach, wyjątkach ani logach.

## 5. Syntetyczne dane i testy importu

- [x] Dodać wyraźnie oznaczone miejsce na dane syntetyczne.
- [x] Przygotować właściwy syntetyczny eksport.
- [x] Uwzględnić kilka rozmów i różne daty.
- [x] Uwzględnić rozgałęzione wiadomości.
- [x] Uwzględnić brakujące daty i pustą treść.
- [x] Uwzględnić język polski, szwedzki i angielski.
- [x] Uwzględnić powtarzające się tematy.
- [x] Przetestować wykrywanie formatu i parser.
- [x] Przetestować kolejność oraz rozgałęzienia wiadomości.
- [x] Przetestować ponowny import i brak duplikatów na danych syntetycznych oraz rzeczywistym eksporcie.
- [x] Przetestować FTS5 oraz filtrowanie po dacie.
- [x] Przetestować odczyt kontekstu wokół wiadomości.
- [x] Przetestować, że logi i błędy nie zawierają prywatnej treści.
- [x] Uruchamiać testy po każdym większym kroku.

## 6. Lokalne wydobywanie tematów

- [x] Znormalizować tekst bez korzystania z usług zewnętrznych.
- [x] Dodać listy słów pustych dla języka polskiego, szwedzkiego i angielskiego.
- [x] Obliczać częstotliwości słów i n-gramów.
- [x] Wyznaczać proste lokalne słowa kluczowe i tematy.
- [x] Ocenić KeyBERT lub spaCy; na tym etapie prosty TF-IDF nie wymaga cięższych zależności.
- [x] Zapisać nazwę tematu oraz liczbę wiadomości i rozmów.
- [x] Zapisać pierwsze i ostatnie wystąpienie tematu.
- [x] Obliczać intensywność tematu w poszczególnych miesiącach.
- [x] Zachować analizę niezależną od formatu ChatGPT.
- [x] Nie budować na tym etapie pełnej ontologii ani „cyfrowego bliźniaka”.
- [x] Oddzielić trwałe surowe `CandidateTerm` od prezentacyjnych `Topic`.
- [x] Zachowywać ocenę jakości i powód odrzucenia bez kasowania kandydatów.
- [x] Odfiltrować stopwords PL/SV/EN oraz artefakty eksportu i cytowań.
- [x] Preferować informacyjne frazy nad ich ogólnymi unigramami.
- [x] Dodać stabilne identyfikatory, aliasy i lokalne ręczne nadpisania.
- [x] Zweryfikować idempotentną przebudowę i migrację istniejącej lokalnej bazy.
- [x] Podzielić mieszane wiadomości na uporządkowane `EventSegment` bez utraty `Event.text`.
- [x] Wyłączyć kod, komendy i logi z głównej analizy tematów.
- [x] Zachować wyszukiwanie kodu, komend i logów przez osobny FTS segmentów.
- [x] Zwracać typ dopasowanego segmentu i zachować otwieranie kontekstu wiadomości.
- [x] Zweryfikować idempotentną segmentację oraz migrację istniejącej bazy.
- [x] Dodać wersjonowane `analysis_runs` i przypisać do nich wszystkie dane pochodne.
- [x] Dodać atomową aktywację ukończonego przebiegu oraz rollback przy błędzie.
- [x] Dodać bezpieczne `diagnose_analysis.py` i pełną przebudowę `rebuild_analysis.py`.
- [x] Dodać osobne aliasy tematów i priorytetowy resolver nazwa–alias–termin.

## 7. Graf relacji tematów

- [x] Reprezentować temat jako węzeł, a jego intensywność jako wielkość węzła.
- [x] Reprezentować współwystępowanie tematów jako ważoną krawędź.
- [x] Ustalić jednostki współwystępowania: zapisywać zarówno liczbę wiadomości, jak i kontekstów/rozmów.
- [x] Filtrować graf według zakresu czasu.
- [x] Dodać minimalny próg wystąpień tematów.
- [x] Dodać minimalny próg siły relacji.
- [x] Dodać limit widocznych węzłów.
- [x] Dodać filtrowanie kategorii i `source_type`.
- [x] Pokazywać najbliższych sąsiadów wybranego węzła.
- [x] Umożliwić ukrycie słabych połączeń.
- [x] Przetestować budowę i filtrowanie relacji.
- [x] Liczyć relacje tematów z przypisań do wydarzeń i niezależnych kontekstów, nie z sąsiedztwa tokenów.
- [x] Wymagać co najmniej dwóch niezależnych kontekstów dla relacji automatycznej.
- [x] Rozdzielić zapisane relacje terminów od relacji właściwych tematów.
- [x] Zachować najważniejsze węzły po odfiltrowaniu słabych krawędzi.

## 8. API

- [ ] Udostępnić podsumowanie źródeł i przebieg importów.
- [x] Udostępnić graf tematów filtrowany po czasie, źródle, progach i limitach.
- [x] Udostępnić wyszukiwanie tematów i pełnotekstowe wyszukiwanie zdarzeń.
- [x] Udostępnić szczegóły tematu, intensywność w czasie i najbliższe relacje.
- [x] Udostępnić paginowaną listę fragmentów źródłowych powiązanych z tematem.
- [x] Udostępnić kontekst kilku wiadomości przed i po fragmencie wyłącznie na żądanie.
- [x] Zwracać stabilny wewnętrzny identyfikator źródła.
- [x] Nie zwracać automatycznie całej historii rozmowy.
- [x] Uwzględnić `source_type` oraz `privacy_level` w kontraktach API.
- [x] Udostępnić listę aktywnych tematów i przypisane im terminy.
- [x] Dodać jawne warstwy `terms` i `topics`, z użyteczną eksploracją terminów jako widokiem domyślnym.
- [x] Dodać zakres treści `all|prose|code|commands|logs` do wyszukiwania zdarzeń.
- [x] Dodać zbiorczą eksplorację wariantów zapytania z deduplikacją wiadomości i kontekstów.
- [x] Rozdzielić liczniki terminów/relacji terminów od tematów/relacji tematów w metadanych.

## 9. Webowy interfejs MVP

- [x] Wyświetlić interaktywny graf z zoomem i przesuwaniem.
- [x] Dodać wyszukiwarkę tematów.
- [x] Dodać filtrowanie po liczbie wystąpień i sile relacji.
- [x] Dodać przełącznik ograniczający graf do sąsiadów wybranego węzła.
- [x] Dodać wybór zakresu dat.
- [x] Aktualizować graf i wielkość węzłów według wybranego okresu.
- [x] Przygotować architekturę pod późniejsze odtwarzanie miesiąc po miesiącu.
- [x] Dodać filtrowanie po `source_type`, początkowo z wartością `chatgpt`.
- [x] Po kliknięciu tematu pokazać liczbę rozmów i wiadomości oraz pierwsze i ostatnie wystąpienie.
- [x] Pokazać wykres intensywności tematu w czasie.
- [x] Pokazać najczęstsze tematy powiązane.
- [x] Pokazać listę rozmów i fragmentów źródłowych.
- [x] Po wybraniu fragmentu załadować jego treść, datę, tytuł rozmowy, stabilny identyfikator i ograniczony kontekst.
- [x] Nie ładować całych rozmów automatycznie.
- [x] Pokazywać domyślnie maksymalnie 30 aktywnych terminów.
- [x] Dodać przełącznik „Terminy / Tematy (beta)” bez sztucznego wypełniania warstwy tematów.
- [x] Dodać wyszukiwanie treści z etykietami typu segmentu i ładowaniem kontekstu.
- [x] Uruchamiać zbiorczą eksplorację klawiszem Enter i pozostawić konkretne rekordy jako zawężenie.
- [x] Czyścić poprzedni wybór po zmianie zapytania lub warstwy grafu.

## 10. Kryteria ukończenia MVP

- [x] Użytkownik może wskazać lokalne archiwum lub rozpakowany eksport.
- [x] Użytkownik może zaimportować rozmowy bez duplikatów.
- [x] Użytkownik może uruchomić aplikację lokalnie bez Dockera.
- [x] Użytkownik może uruchomić aplikację przez Docker Compose.
- [x] Użytkownik widzi sieć tematów.
- [x] Użytkownik ogranicza sieć do wybranego okresu.
- [x] Użytkownik wyszukuje temat.
- [x] Użytkownik wybiera węzeł i widzi powiązane rozmowy oraz fragmenty.
- [x] Użytkownik rozwija ograniczony kontekst wybranego fragmentu.
- [x] Cały przepływ działa bez wysyłania danych poza komputer użytkownika.

## 11. Rozszerzalność po MVP

- [ ] Zweryfikować na przykładzie projektowym, że nowy adapter mapujący wpisy dziennika na `Event` nie wymaga przebudowy grafu, osi czasu, tematów, wyszukiwania ani API filtrowania.
- [ ] Pozostawić możliwość dodania adapterów dla dziennika HTML, historii wyszukiwań, Google Timeline, zdjęć/EXIF, kalendarza, LinkedIn, Facebooka, GitHuba i innych lokalnych archiwów.
- [ ] Traktować dane osób trzecich jako szczególnie wrażliwe.

Poniższe funkcje pozostają celowo poza pierwszą wersją: import źródeł innych niż ChatGPT, dane przestrzenne, zdjęcia i EXIF, kalendarz, Gmail, GitHub, Home Assistant, analiza emocji, predykcja decyzji, cyfrowy bliźniak, automatyczna narracja, pełny graf poznawczy, chmurowe embeddingi oraz publiczny hosting.

## Proponowana kolejność małych commitów

- [x] Commit 1: bezpieczny szkielet repozytorium, dokumentacja i reguły ignorowania.
- [x] Commit 2: inspektor formatu oraz raport z pierwszego eksportu bez ujawniania treści — zrealizowane w zbiorczym commicie `1c15682`.
- [x] Commit 3: wspólny schemat SQLite, modele SQLAlchemy i migracje Alembic — zrealizowane w zbiorczym commicie `1c15682`.
- [x] Commit 4: `ChatGPTExportAdapter`, idempotentny import i komplet danych syntetycznych — zrealizowane w zbiorczym commicie `1c15682`.
- [x] Commit 5: FTS5, lokalne tematy i pierwszy pionowy wycinek API → graf — commit `6e6e7b7`.
- [x] Normalizacja instrukcji i statusu projektu — commit `2b42516`.
- [x] Typowane wyszukiwanie i szczegóły tematów — commit `47366ec`.
- [x] Czasowy graf, panel szczegółów i kontekst — commit `1ea6133`.
- [x] Compose, pełna walidacja i audyt Visual MVP — commit `2088608`.
