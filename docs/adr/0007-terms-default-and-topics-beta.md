# ADR 0007: Terminy jako warstwa domyślna, tematy jako beta

Status: zaakceptowano 2026-07-14; koryguje ADR 0006.

## Kontekst

Pierwsza implementacja `CandidateTerm → Topic` automatycznie promowała niemal każdy zaakceptowany singleton. Usunięcie stopwords nie utworzyło warstwy semantycznej, lecz zastąpiło część ogólnych słów tokenami kodowymi. Jednocześnie domyślny graf tematów odebrał użytkownikowi działającą eksplorację terminów, w tym wyszukiwanie skrótów i prawy panel.

## Decyzja

`CandidateTerm` jest uczciwie nazywaną warstwą eksploracyjną „Terminy” i pozostaje domyślnym widokiem. Zachowuje graf, wyszukiwanie, intensywność, sąsiedztwo, fragmenty i ograniczony kontekst.

„Tematy (beta)” zawierają wyłącznie ręczne mapowania, grupy wielu wariantów, wartościowe frazy wielowyrazowe oraz rozpoznane akronimy. Samotny zaakceptowany unigram nie jest automatycznie tematem. Limit grafu jest wyłącznie górną granicą i nigdy nie jest sztucznie wypełniany.

API używa jawnego parametru `layer=terms|topics`; dotychczasowy parametr `view` grafu pozostaje przejściowo obsługiwany dla zgodności.

## Konsekwencje

Warstwa terminów może nadal zawierać pojedyncze słowa, ale jest tak nazwana i filtrowana z artefaktów. Graf tematów może być mały lub pusty. Jakość tematów zależy obecnie od prostych, wyjaśnialnych reguł i lokalnych nadpisań; pełna ontologia pozostaje poza zakresem.
