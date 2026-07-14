# Topic Quality v1 — raport kamienia milowego

Data: 2026-07-14.

## Cel i stan funkcjonalny

Sprint oddzielił surowe kandydaty od tematów prezentowanych w grafie. Aplikacja zachowuje wszystkie wykryte terminy, zapisuje ich jakość i powód odrzucenia, grupuje zaakceptowane warianty oraz preferuje informacyjne frazy. Domyślny graf pokazuje `Topic`; surowe terminy są dostępne wyłącznie przez jawny widok diagnostyczny. Dotychczasowe wyszukiwanie, fragmenty i ograniczony kontekst rozmowy nadal działają.

## Wykonane zmiany i migracja

- dodano `candidate_terms`, `topic_terms` oraz `event_candidate_terms`;
- rozszerzono `topics` o status, pochodzenie i aktywność;
- migracja zachowuje stare rekordy, przypisania i wagi;
- dodano lokalne filtry PL/SV/EN, artefakty eksportu oraz jawne powody odrzucenia;
- dodano aliasy, proste grupowanie wariantów i ignorowane lokalne nadpisania YAML;
- API 0.3.0 udostępnia listę tematów, skład tematu i diagnostyczny graf terminów;
- UI domyślnie pokazuje 30 tematów i ma mały przełącznik diagnostyczny.

Migracja kopii istniejącej bazy zachowała 494 stare rekordy, 494 powiązania temat–termin i 57 153 przypisania zdarzenie–termin. Kontrola integralności zwróciła `ok`, a kontrola kluczy obcych zero problemów. Następnie zmigrowano i przebudowano właściwą lokalną bazę.

## Porównanie jakości na lokalnej bazie

Raport zawiera wyłącznie agregaty.

| Miara | Przed | Po |
| --- | ---: | ---: |
| rekordy traktowane jako tematy / aktywne tematy | 494 | 180 |
| unigramy / przypisania unigramów do aktywnych tematów | 445 | 141 |
| bigramy / przypisania bigramów do aktywnych tematów | 49 | 41 |
| trigramy / przypisania trigramów do aktywnych tematów | 0 | 15 |
| węzły domyślnego grafu | 100 | 30 |
| relacje domyślnego grafu | 99 | 39 |

Przebudowa oceniła maksymalny skonfigurowany zbiór 5 000 kandydatów: 4 054 zaakceptowano, 946 odrzucono, a 213 terminów przypisano do 180 aktywnych tematów. Kategorie odrzucenia: 453 stopwords, 271 nieprawidłowych tokenów, 202 terminy zbyt krótkie, 16 artefaktów eksportu i 4 terminy zbyt częste. Żaden aktywny temat nie był oparty na kandydacie odrzuconym jako stopword lub artefakt.

Przebudowa trwała 33,11 s i osiągnęła około 2,18 GiB pamięci szczytowej. Druga przebudowa dała te same liczniki i nie utworzyła duplikatów.

## Testy i kryteria ukończenia

- `pytest -q`: 34 testy zaliczone;
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: zaliczone;
- `alembic check`: brak nowych operacji migracyjnych;
- migracja istniejącej kopii, integralność i klucze obce: zaliczone;
- `npm test`: 11 testów zaliczonych;
- `npm run build`: zaliczone;
- `npm audit --audit-level=high`: zero podatności;
- Docker Compose: oba obrazy zbudowane, obie usługi zdrowe, kontrola HTTP frontendu, metadanych i grafu zaliczona;
- montowania: `sources/` tylko do odczytu, `data/` lokalnie zapisywalne, porty wyłącznie na `127.0.0.1`.

Wszystkie dwanaście kryteriów sprintu zostało sprawdzonych. Strukturalna ocena wizualna wskazuje czytelniejszą gęstość: 30 węzłów i 39 relacji w widoku domyślnym, stabilny renderer oraz brak artefaktów w warstwie tematów. Nie zapisano ani nie ujawniono nazw z lokalnego korpusu.

## Prywatność, bezpieczeństwo i status Git

`sources/`, lokalna baza, lokalne nadpisania, cache, buildy i indeksy pozostają ignorowane. Historia nie zawiera eksportów, baz ani archiwów prywatnych. Jedynymi dopasowaniami audytu nazw były zamierzony `data/.gitkeep`, syntetyczny fixture i `.env.example`. Nie znaleziono jawnych lokalnych ścieżek ani śledzonych sekretów. Obrazy nie zawierają źródeł ani bazy.

Repozytorium nadal ma status `TEMP PUBLIC`; przed każdym push obowiązuje ponowny audyt.

## Instrukcja uruchomienia

```bash
source .venv/bin/activate
alembic upgrade head
python scripts/build_topic_graph.py
uvicorn backend.app.main:app --reload
```

W drugim terminalu uruchom frontend zgodnie z README albo cały stos:

```bash
docker-compose up --build
```

Interfejs: `http://127.0.0.1:5173`.

## Ograniczenia, ryzyka i znane błędy

- heurystyczne grupowanie nie jest pełną lematyzacją ani obsługą synonimów między językami;
- analiza ocenia obecnie najwyżej 5 000 kandydatów;
- zużycie pamięci przebudowy jest wysokie i powinno zostać zoptymalizowane przed znacznym wzrostem korpusu;
- lokalny Docker Compose 1.29 ma znany błąd odtwarzania starego kontenera (`ContainerConfig`); usunięcie wyłącznie starego kontenera rozwiązało problem, a świeży stos przeszedł testy;
- ostrzeżenia testów Pythona dotyczą deprecjacji zależności, nie błędów funkcjonalnych.

## Rekomendowany następny etap

Najpierw zoptymalizować pamięć ekstrakcji i przeprowadzić użytkową ocenę 180 tematów. Dopiero na podstawie tej oceny dostroić progi, listy lokalne i ręczne aliasy; embeddingi pozostają poza następnym małym krokiem.

## Commity

- baseline: `71db4b2`;
- filtry jakości: `7b9282c`;
- rozdzielenie kandydatów i tematów: `b691b21`;
- API i diagnostyka: `ce10529`;
- UI: `1703ab7`;
- końcowa walidacja i ten raport: commit zawierający niniejszy plik.
