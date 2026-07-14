# Naprawa regresji Topic Quality v1

Data: 2026-07-14.

## Cel i przyczyna regresji

Pierwsza implementacja utożsamiała zaakceptowany `CandidateTerm` z `Topic` niemal 1:1. Odfiltrowanie części słów ogólnych odsłoniło tokeny kodowe, a domyślne przełączenie na niedojrzałą warstwę tematów osłabiło działającą eksplorację terminów. Dodatkowo analiza jednorodnego `Event.text` pozwalała kodowi i logom wpływać na statystyki języka naturalnego.

Naprawa przywraca „Terminy” jako domyślną warstwę eksploracyjną, ogranicza „Tematy (beta)” do jednostek rzeczywiście zgrupowanych lub zatwierdzonych oraz rozdziela analizę prozy od wyszukiwania treści technicznej.

## Aktualny stan funkcjonalny

- `layer=terms` zachowuje wyszukiwanie, intensywność, sąsiedztwo, fragmenty i ograniczony kontekst;
- `layer=topics` pokazuje warstwę beta bez automatycznej promocji singletonów i bez sztucznego dopełniania limitu;
- temat może łączyć wiele terminów i aliasów, w tym lokalne nadpisania ignorowane przez Git;
- wyszukiwanie akronimów jest niewrażliwe na wielkość liter, a krótkie informacyjne skróty nie są odrzucane wyłącznie przez długość;
- wiadomości są dzielone na prozę, kod, kod inline, komendy, logi, cytaty, tabele, linki i artefakty narzędziowe;
- kod i logi są dostępne w FTS przez `content_scope`, zwracają `match_type` i otwierają kontekst pełnej wiadomości, ale nie zasilają głównego grafu.

## Migracje i dane

Migracja CandidateTerm/Topic zachowuje istniejące rekordy i pozwala na ponowną budowę analizy. Kolejna migracja dodaje `event_segments` z unikalnym `(event_id, segment_index)` oraz osobny indeks FTS5 utrzymywany triggerami. `Event.text` nie jest usuwany ani zastępowany; segmenty są deterministycznie regenerowalne.

Migrację sprawdzono najpierw na kopii, a następnie na lokalnej bazie. Integralność SQLite wyniosła `ok`, liczba naruszeń kluczy obcych: 0. Ponowna segmentacja nie tworzy duplikatów.

## Bezpieczne liczniki przed i po

Wyniki nie zawierają nazw lokalnych tematów ani fragmentów rozmów.

| Miara po segmentacji | Wartość |
| --- | ---: |
| wszystkie segmenty / wpisy FTS | 115 470 |
| proza | 67 668 |
| kod blokowy | 7 779 |
| kod inline | 28 478 |
| komendy | 3 834 |
| logi | 2 114 |
| cytaty | 854 |
| tabele | 828 |
| linki | 353 |
| artefakty narzędziowe | 3 562 |
| dokumenty użyte w analizie | 18 635 |
| CandidateTerm przed filtracją | 6 540 |
| aktywne CandidateTerm | 4 697 |
| odrzucone CandidateTerm | 1 843 |
| Topic beta | 200 |
| powiązania topic–term | 405 |
| tematy beta zawierające tylko jeden termin | 0 |
| przypisania zdarzenie–temat | 25 605 |
| relacje tematów | 7 086 |

Powody odrzucenia: `code_token` 29, `export_artifact` 10, `invalid_token` 125, `low_information` 798, `stopword` 595, `too_common` 2 i `too_short` 284. Żaden kontrolny token kodowy nie pozostał aktywnym tematem. Kontrolne wyszukiwanie akronimu zwróciło dopasowanie bez względu na wielkość liter; lokalny akronim miał 662 wystąpienia, bez zapisywania jego kontekstu w raporcie.

Przebudowa trwała 47,42 s, a zmierzona pamięć szczytowa wyniosła około 1,57 GiB.

## Testy i uruchomienie

- `pytest -q`: 43 zaliczone;
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: zaliczone;
- `alembic check`: brak nowych operacji;
- `npm test`: 13 zaliczonych;
- `npm run build`: zaliczone;
- Compose na alternatywnych portach: backend i frontend zdrowe; sprawdzono API, typy dopasowań, kontekst, lokalne montowanie `data/` i montowanie `sources/` tylko do odczytu.

Uruchomienie pozostaje bez zmian:

```bash
alembic upgrade head
python scripts/build_topic_graph.py
docker-compose up --build
```

Po migracji istniejącej bazy należy przebudować analizę, aby utworzyć segmenty i powiązane indeksy.

## Prywatność i bezpieczeństwo

Audyt bieżącego drzewa i osiągalnej historii nie wykazał śledzonych eksportów, baz, archiwów, logów, sekretów ani jawnych lokalnych ścieżek. `sources/`, bazy, lokalne nadpisania i wyniki pozostają ignorowane. Raport używa wyłącznie agregatów, a kod nie wysyła treści do usług zewnętrznych.

## Ograniczenia i ryzyka

- klasyfikacja segmentów jest heurystyczna i może wymagać rozszerzeń dla nietypowego Markdownu lub nieoznaczonych logów;
- liczba 200 tematów beta osiąga skonfigurowany limit; brak singletonów potwierdza regułę strukturalną, ale jakość nazw i grup nadal wymaga oceny wizualnej właściciela;
- pełna przebudowa ma zauważalny koszt pamięci;
- ewentualny graf techniczny pozostaje poza zakresem i powinien być osobną warstwą.

## Kryteria naprawy

Kryteria funkcjonalne naprawy zostały potwierdzone testami, migracją lokalnej bazy i uruchomieniem Compose: domyślne terminy działają, tematy beta nie są promocją 1:1, akronimy pozostają znajdowalne, tokeny kodowe nie są tematami, a kod i logi są osobno wyszukiwalne wraz z kontekstem.

## Commity

- `fdf31a1` — przywrócenie eksploracji terminów i uczciwej warstwy tematów beta;
- `b8898cb` — wymaganie zgrupowanych dowodów dla automatycznych tematów;
- `bcd35d4` — optymalizacja grafu terminów i lokalnego Compose;
- `4fb8e7b` — segmentacja prozy, kodu, komend i logów;
- końcowa walidacja i dokumentacja: commit zawierający niniejszy raport.

## Rekomendowany następny etap

Przeprowadzić wizualną ocenę małej próbki warstwy „Tematy (beta)” bez utrwalania prywatnych etykiet, następnie dostroić grupowanie i zużycie pamięci. Nie zastępować domyślnej eksploracji terminów, dopóki jakość tematów nie zostanie potwierdzona przez użytkownika.
