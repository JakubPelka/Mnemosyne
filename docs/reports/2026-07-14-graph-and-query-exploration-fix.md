# Graf terminów i zbiorcza eksploracja zapytań

Data: 2026-07-14

## Cel i przyczyna

Pusty graf terminów wynikał z braku osobnej, materializowanej warstwy relacji kandydatów i nieuczciwego powiązania prezentacji terminów ze stanem konserwatywnej warstwy tematów beta. Wyszukiwanie działało ponadto jak selektor jednego rekordu i pozostawiało poprzedni wybór po zmianie zapytania.

## Aktualny stan funkcjonalny

- graf terminów korzysta wyłącznie z `CandidateTerm`, `EventCandidateTerm` i `CandidateTermRelation` aktywnego przebiegu;
- graf tematów korzysta z `Topic`, `EventTopic` i relacji tematów aktywnego przebiegu;
- filtrowanie słabych krawędzi nie usuwa węzłów izolowanych;
- metadane rozdzielają liczniki obu warstw;
- zapytanie może być eksplorowane zbiorczo, a konkretne rekordy służą do opcjonalnego zawężenia;
- zdarzenia, konteksty i fragmenty wyniku zbiorczego są deduplikowane;
- zmiana zapytania lub warstwy czyści wcześniejszy wybór;
- kod i logi pozostają poza domyślną eksploracją prozy.

## Kryteria i wyniki

Lokalny aktywny przebieg zawiera 4742 aktywne terminy, 22143 relacje terminów, 2 właściwe tematy beta i 1 relację tematów. Domyślny graf zwrócił 30 terminów i 7 relacji, zachowując 22 węzły izolowane. Graf tematów zwrócił 2 węzły i 1 relację.

Test kontraktowy `GIS` potwierdził aktywny zaakceptowany termin, wystąpienia w prozie i FTS oraz odpowiedź resolvera HTTP 200. Zbiorcza walidacja lokalna znalazła 610 unikalnych zdarzeń w 189 kontekstach; strona fragmentów nie zawierała duplikatów. Nazwy lokalnych tematów i treści nie zostały zapisane w raporcie.

## Testy

- `pytest -q`: 47 zaliczonych;
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: zaliczone;
- `alembic check`: zaliczone po migracji;
- frontend: 16 testów zaliczonych;
- `tsc --noEmit`: zaliczone;
- produkcyjny build Vite: zaliczony do czystego katalogu tymczasowego;
- `docker-compose config --quiet`: zaliczone.

Pełnego stosu Compose nie uruchomiono ponownie, ponieważ bieżąca sesja nie ma dostępu do demona Docker. Konfiguracja Compose nie była zmieniana.

## Migracja i dane prywatne

Migracja dodaje wyłącznie regenerowalną tabelę `candidate_term_relations`. Pełna przebudowa zachowała źródła, oryginalny tekst zdarzeń i przebiegi importu. Dokładnie jeden ukończony przebieg jest aktywny; nie wykryto osieroconych relacji ani danych pochodnych bez identyfikatora przebiegu.

Lokalna baza, źródła, override tematów, `node_modules` i wyniki builda pozostają ignorowane. Żaden z tych plików nie jest kandydatem do commitu.

## Ograniczenia i ryzyka

- jakość sąsiadów wyniku zbiorczego wymaga ręcznej oceny właściciela;
- wynik zbiorczy opiera się na wyjaśnialnym dopasowaniu tokenów i FTS, bez grupowania semantycznego;
- build w standardowym `frontend/dist` blokują stare ignorowane pliki należące do roota, dlatego walidację wykonano w katalogu tymczasowym;
- gałąź pozostaje niescalona do ręcznej oceny.

## Uruchomienie i następny etap

Po udostępnieniu demona Docker uruchomić `docker-compose up --build`, a następnie ręcznie sprawdzić oba widoki, kontrakt `GIS`, wynik zbiorczy, fragmenty, kontekst i czyszczenie panelu po zmianie zapytania. Dopiero pozytywna ocena właściciela powinna otworzyć decyzję o merge; embeddingi i kolejne warstwy ontologii pozostają poza zakresem.

## Commity

- `da7ddc1` — rozdzielenie grafów i zbiorczy kontrakt danych;
- `285122b` — zbiorcza eksploracja oraz poprawny stan wyboru w UI.
- `a233169` — jawne użycie relacji tematów aktywnego przebiegu.
