# Wersjonowana przebudowa analizy — raport migracji

Data: 2026-07-14.

## Cel

Usunąć mieszanie starych i nowych danych pochodnych, bezwzględnie rozdzielić `CandidateTerm` od `Topic`, przywrócić obowiązkowe wyszukiwanie `GIS` oraz oprzeć relacje tematów na niezależnych wydarzeniach i kontekstach.

## Przyczyna

Poprzednie przebudowy aktualizowały rekordy w miejscu. Diagnostyka wykazała 1171 historycznych rekordów `Topic`, mimo że ostatnia wersja raportowała 200 aktywnych pozycji. Ograniczenie wyniku do 200 nadal maskowało promocję przypadkowych n-gramów i ciągów uppercase. Migracja schematu nie była równoznaczna z pełnym usunięciem starych danych analitycznych.

## Zmiany

- dodano `analysis_runs` z wersją algorytmu, statusem, hashem konfiguracji i atomowym aktywowaniem;
- wszystkie regenerowalne warstwy otrzymały `analysis_run_id`;
- dodano osobne `topic_aliases` i `creation_method`;
- `rebuild_analysis.py --fresh` wymienia wyłącznie segmenty, terminy, tematy, aliasy, przypisania, relacje i ich indeksy;
- `diagnose_analysis.py` raportuje bezpieczne liczniki lub ścieżkę pojedynczego jawnie podanego terminu;
- API 0.4.0 filtruje tematy i terminy według jednego aktywnego ukończonego przebiegu;
- resolver stosuje kolejność: dokładna nazwa tematu, dokładny alias, dokładny termin, prefiks;
- automatyczna promocja dużego korpusu została wyłączona, dopóki reguły nie potrafią odróżnić pojęć od częstych fraz;
- relacje opierają się na przypisaniach `Topic → Event` i wymagają niezależnych kontekstów;
- dodano indeksy odwrotne przypisań i szybszą ścieżkę domyślnego grafu terminów.

## Stan funkcjonalny

- „Terminy” pozostają domyślną, pełną warstwą eksploracji z intensywnością, fragmentami i kontekstem;
- „Tematy (beta)” bez lokalnych overrides poprawnie pokazują 0 węzłów;
- lokalny ignorowany override właściciela tworzy 2 zatwierdzone tematy, 5 aliasów i 1 relację;
- obowiązkowy akronim jest znajdowalny niezależnie od wielkości liter: bez override jako `exact_term`, a po override jako `exact_topic`;
- kod i logi pozostają w FTS, ale nie zasilają terminów, tematów ani relacji.

## Bezpieczne porównanie lokalnej bazy

| Miara | Przed świeżą przebudową | Bez overrides | Z lokalnym override |
| --- | ---: | ---: | ---: |
| wydarzenia | 32920 | 32920 | 32920 |
| segmenty | 115470 | 115470 | 115470 |
| CandidateTerm | 8666 | 6540 | 6541 |
| Topic | 1171 | 0 | 2 |
| aliasy | 0 | 0 | 5 |
| EventTopic | 25605 | 0 | 1729 |
| TopicRelation | 7086 | 0 | 1 |
| aktywne ukończone przebiegi | 1 | 1 | 1 |
| rekordy pochodne bez przebiegu | — | 0 | 0 |
| osierocone relacje | 0 | 0 | 0 |

Jedyna lokalna relacja opiera się na 36 niezależnych kontekstach. Nazwy inne niż jawnie wskazany przez właściciela test kontraktowy nie są zapisywane w raporcie.

## Testy

- `pytest -q`: 44 zaliczone;
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: zaliczone;
- `alembic check`: brak nowych operacji;
- dwukrotne `rebuild_analysis.py --fresh`: identyczne liczniki pochodne, bez duplikatów;
- integralność relacji: 0 osieroconych rekordów, dokładnie jeden aktywny przebieg;
- API 0.4.0: wyszukiwanie, resolver, szczegóły terminu, wystąpienia oraz oba grafy odpowiadają poprawnie;
- pomiary lokalne: szczegóły terminu 5,67 s, graf 30 terminów 2,53 s, graf tematów 0,03 s.

Frontend nie zmienił się w tej migracji. Ostatni przebieg 13 testów i build był zaliczony; bieżący host nie ma Node, a aktywny stary stos Compose nie został zatrzymany bez zgody właściciela.

## Prywatność i Git

`data/local_topic_overrides.yaml`, baza i wszystkie źródła pozostają ignorowane. Testy wymuszają brak dostępu do lokalnych overrides i używają wyłącznie konfiguracji syntetycznych. Narzędzia diagnostyczne nie wypisują treści wiadomości ani innych lokalnych nazw. Dane źródłowe, `Event.text` i rekordy importu nie zostały usunięte.

## Ograniczenia i ryzyka

- automatyczna warstwa tematów dużego korpusu jest celowo wyłączona; jej przywrócenie wymaga mierzalnego grupowania semantycznego lub lepszego rozpoznawania nazw własnych;
- historia `analysis_runs` zachowuje metadane, ale w aktywnych tabelach materializowany jest tylko jeden duży komplet danych pochodnych;
- pełna przebudowa trwa około 55 s i osiąga około 1,57 GiB pamięci;
- stary proces na porcie 8000 nadal prezentuje API 0.3.0 do czasu restartu Compose;
- ręczna ocena jedynej relacji w aktualnym UI nie została wykonana przez właściciela, więc całego sprintu jakości tematów nie uznaje się jeszcze za scalalny.

## Uruchomienie

```bash
alembic upgrade head
python scripts/diagnose_analysis.py --summary
python scripts/rebuild_analysis.py --dry-run
python scripts/rebuild_analysis.py --fresh
python scripts/diagnose_analysis.py --term GIS
```

Następnie należy przebudować i uruchomić Compose, aby proces backendu używał API 0.4.0.

## Commity

- `6335ece` — wersjonowanie oraz pełna przebudowa danych pochodnych;
- optymalizacja i końcowa walidacja: commit zawierający niniejszy raport.

## Rekomendowany następny etap

Właściciel powinien odświeżyć stos i ręcznie ocenić relację dwóch lokalnie zatwierdzonych tematów. Następnie można poprawić lokalne aliasy bez ponownego importu. Automatyczne grupowanie wielojęzyczne należy projektować jako osobny etap z jednoznacznym benchmarkiem, nie jako kolejne rozszerzenie stopwords.
