# Architektura

## Granice systemu

```text
prywatne źródło (tylko odczyt)
  -> SourceAdapter
  -> Source + Event + rekord źródłowy
  -> EventSegments (proza / kod / komendy / logi)
  -> CandidateTerms -> Topics / Entities / Relations
  -> API
  -> Graph + Timeline + Source Browser
```

Format ChatGPT nie jest modelem domenowym aplikacji. `ChatGPTExportAdapter` będzie pierwszym adapterem, a `chatgpt_conversations` i `chatgpt_messages` zachowają dane potrzebne do odtworzenia wątków. Każda wiadomość wskaże odpowiadające jej wspólne zdarzenie.

## Warstwy

- `backend/app/importers`: inspekcja, walidacja, odczyt i normalizacja źródeł;
- `backend/app/models`: wspólny schemat SQLite i rozszerzenia źródłowe;
- `backend/app/services`: wyszukiwanie, tematy i graf niezależne od adaptera;
- `backend/app/api`: filtrowanie po czasie, źródle i poziomie prywatności;
- `frontend`: graf, oś czasu i kontekst fragmentu.

Rdzeń bazy obejmuje: `sources`, `events`, `analysis_runs`, `event_segments`, `candidate_terms`, `topics`, `topic_aliases`, `topic_terms`, `event_candidate_terms`, `event_topics`, `entities`, `event_entities`, `event_relations` oraz `import_runs`. Rozszerzenie źródłowe obejmuje `chatgpt_conversations` i `chatgpt_messages`. Warstwa persystencji używa SQLAlchemy 2.x, a zmiany schematu są wersjonowane przez Alembic. Bazą pozostaje pojedynczy lokalny plik SQLite. Wyszukiwanie tekstu używa FTS5.

## Idempotencja

Źródło otrzyma hash ścieżki zamiast jawnej ścieżki. Rekordy będą identyfikowane przez parę `(source_id, source_record_id)`. Ponowny import ma aktualizować istniejące rekordy w transakcji, a nie tworzyć duplikaty.

Eksport pokazał, że `message_id` nie jest globalnie unikalne: ten sam identyfikator może wystąpić w różnych rozmowach. Wewnętrzny klucz wiadomości i jej zdarzenia jest więc deterministycznie wyprowadzany z pary `(conversation_id, message_id)`. Oryginalny identyfikator pozostaje zachowany jako pole źródłowe.

SQLite FTS5 jest utrzymywany przez triggery powiązane z `events`. Tabele wirtualne i cieniujące FTS są celowo wyłączone z porównywania schematu Alembic.

`Event.text` pozostaje pełną treścią źródłową. Regenerowalne `event_segments` zachowują kolejność prozy, kodu, komend, logów i innych fragmentów. Osobny FTS segmentów indeksuje treści użytkowo wyszukiwalne, natomiast analiza tematów pobiera wyłącznie segmenty z `analysis_enabled=true` i uwzględnia ich `topic_weight`. Kod techniczny pozostaje wyszukiwalny, ale nie zasila statystyk języka naturalnego.

Publiczny kontrakt lokalnego backendu jest opisany modelami Pydantic. Wyszukiwanie FTS5 i wystąpienia tematu zwracają wyłącznie krótkie fragmenty oraz stabilne identyfikatory; ograniczony kontekst rozmowy jest pobierany osobno po wyborze zdarzenia. Graf i wyszukiwanie jawnie przyjmują `privacy_level`, domyślnie `private`.

Każde zdarzenie ma neutralne `context_id`, `is_active` oraz `analysis_enabled`. `context_id` grupuje zdarzenia bez zależności od typu źródła, `is_active` pozwala zachować historię rekordów nieobecnych w nowszym imporcie, a `analysis_enabled` oddziela przechowywanie tekstu od zgody na udział w NLP. Dla ChatGPT analizowane są tylko widoczne `text` i `multimodal_text`.

Analiza najpierw zapisuje unigramy, bigramy i opcjonalne trigramy jako `CandidateTerm`. Deterministyczne reguły jakości zachowują odrzucone rekordy wraz z powodem, lecz wyłączają je z domyślnej eksploracji. Aktywne kandydaty tworzą domyślną warstwę „Terminy” z pełnymi statystykami i kontekstem.

`Topic` jest osobną warstwą beta. Powstaje tylko z ręcznego mapowania, wielu wariantów, wartościowej frazy albo rozpoznanego akronimu; zaakceptowany singleton nie jest automatycznie promowany. `topic_terms` zachowuje pochodzenie, aliasy i ręczne mapowania, `event_candidate_terms` wraz z `candidate_term_relations` zasila niezależny graf terminów, a `event_topics` wraz z `topic_relations` graf tematów beta. Lokalne nadpisania mogą być przechowywane wyłącznie w ignorowanym `data/local_topic_overrides.yaml`.

Zbiorcza eksploracja zapytania nie tworzy rekordu `Topic`. Łączy dokładne terminy, aliasy, frazy z granicami tokenów i FTS prozy, a następnie wylicza statystyki na zbiorze unikalnych `event_id` oraz neutralnych `context_id`. Konkretne rekordy pozostają opcjonalnym zawężeniem wyniku.

Każda pełna przebudowa danych pochodnych ma własny `analysis_run`. Nowy komplet segmentów, terminów, tematów, aliasów, przypisań i relacji jest budowany transakcyjnie, a następnie atomowo oznaczany jako jedyny aktywny przebieg. API filtruje dane po tym przebiegu. Dla dużego korpusu wersja regułowa automatycznie promuje wyłącznie bardzo konserwatywne klasy; pozostałe pojęcia wymagają lokalnego override. Pusty graf tematów beta jest poprawnym wynikiem.

## Decyzje odłożone do inspekcji

- dokładne mapowanie drzewa wiadomości i aktywnej gałęzi;
- sposób traktowania załączników oraz nietekstowych części wiadomości;
- wersjonowanie eksportu, gdy format nie zawiera jawnej wersji;
- strategia stabilnego identyfikatora dla rekordów bez ID.

## Surowe dane

Oryginalne eksporty pozostają lokalnie w ignorowanym katalogu `sources/` i są otwierane tylko do odczytu. Znormalizowana baza może zachowywać lokalne odwołania do surowych rekordów, ale nie kopiuje surowych payloadów do śledzonych plików, dokumentacji ani logów.
