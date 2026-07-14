# Baseline jakości terminów

Data: 2026-07-14  
Stan: przed implementacją Topic Quality v1.  
Zakres: lokalna baza; raport zawiera wyłącznie agregaty i klasy problemów.

## Metoda

Pomiar objął aktywne, dopuszczone do analizy zdarzenia z tekstem oraz ówczesne rekordy tabeli `topics`, które w praktyce reprezentowały surowe unigramy i bigramy. Nazwy terminów zostały odczytane wyłącznie lokalnie i nie są zapisywane ani cytowane w tym raporcie.

Klasy językowe oszacowano konserwatywnie na podstawie małego, jawnego słownika ogólnych słów PL/SV/EN. Wynik jest wskaźnikiem problemu, nie pełnym rozpoznaniem języka.

## Liczniki wejściowe

- aktywne dokumenty tekstowe: 18 737;
- wszystkie zapisane terminy: 494;
- unigramy: 445;
- bigramy: 49;
- terminy o trzech lub większej liczbie tokenów: 0;
- terminy bardzo krótkie, poniżej czterech znaków po usunięciu spacji: 97;
- terminy zawierające znany artefakt eksportu lub cytowania: 12;
- terminy występujące w ponad 10% dokumentów: 0;
- terminy rozpoznane jako ogólne markery językowe: 9, w tym 5 PL, 4 SV i 0 EN;
- terminy z markerami więcej niż jednego języka: 0.

## Rozkład wystąpień

| Miara | minimum | p25 | mediana | p75 | p90 | maksimum |
|---|---:|---:|---:|---:|---:|---:|
| wiadomości | 1 | 25 | 57 | 138 | 331 | 1 632 |
| konteksty | 1 | 8 | 20 | 48 | 130 | 585 |

## Progi jakości

Dotychczasowy proces nie rozróżniał kandydatów i tematów. Wszystkie 494 rekordy przeszły techniczne progi budowy i zostały zapisane jako `Topic`.

Wstępna, konserwatywna symulacja reguł sprintu pozostawiła 375 terminów. Liczby odrzuceń według niezależnych, mogących się nakładać klas:

- `too_short`: 97;
- `export_artifact`: 12;
- `stopword` lub bardzo ogólne słowo: 9;
- pojedyncze wystąpienie: 6.

To nie jest jeszcze wynik docelowego algorytmu: nie uwzględnia preferowania fraz, TF-IDF, wariantów ani ręcznych aliasów.

## Graf bazowy

Dla ustawień odpowiadających dotychczasowemu domyślnemu UI (`min_occurrences=2`, `min_relation_weight=0.15`, limit 100):

- węzły: 100;
- relacje: 99.

Graf przedstawiał surowe terminy, a nie odrębną warstwę pojęć.

## Wnioski

- 90% rekordów stanowią unigramy, co potwierdza niedostateczne preferowanie fraz;
- niemal 20% rekordów jest bardzo krótkich;
- artefakty eksportu przechodzą do warstwy prezentacyjnej;
- dotychczasowy model nie zachowuje powodów odrzucenia, języka ani metryk kandydata;
- konieczne jest zachowanie surowych terminów w `CandidateTerm` i promowanie tylko aktywnych, wyjaśnialnie ocenionych kandydatów do `Topic`.

## Ograniczenia baseline’u

- język jest oszacowany słownikiem, bez modelu statystycznego;
- dotychczasowa tabela nie przechowuje pełnego document frequency ani zagregowanego TF-IDF;
- klasy odrzucenia nakładają się, dlatego ich suma nie jest liczbą unikalnych odrzuconych rekordów;
- raport nie zawiera nazw rzeczywistych terminów, tematów, rozmów ani fragmentów.
