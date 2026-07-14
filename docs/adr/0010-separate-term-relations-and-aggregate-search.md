# ADR 0010: osobne relacje terminów i nietrwały wynik zbiorczy

## Status

Przyjęta — 2026-07-14.

## Kontekst

Graf terminów był pośrednio zależny od relacji właściwych tematów, przez co konserwatywna warstwa tematów beta mogła pozostawić użyteczną eksplorację terminów bez węzłów. Wyszukiwanie wymagało też wyboru jednego rekordu, mimo że użytkownik oczekiwał obrazu wszystkich wariantów pojęcia.

## Decyzja

- relacje `CandidateTerm` są materializowane osobno jako `candidate_term_relations` i przypisane do aktywnego `analysis_run`;
- graf terminów wybiera węzły przed filtrowaniem krawędzi, dlatego węzły izolowane pozostają widoczne;
- graf tematów nie korzysta z relacji terminów;
- `/api/search/explore` tworzy nietrwały wynik zapytania, łącząc dopasowane terminy, tematy i FTS prozy;
- liczniki i paginacja wyniku są oparte na unikalnych zdarzeniach i kontekstach;
- wynik zbiorczy nie jest zapisywany jako `Topic`.

## Konsekwencje

Warstwa terminów pozostaje użyteczna nawet przy bardzo małej liczbie właściwych tematów. Kosztem jest osobna tabela relacji i dodatkowy etap przebudowy analizy. Agregaty zapytań są obliczane na żądanie, ale nie zanieczyszczają lokalnej ontologii tymczasowymi pojęciami.
