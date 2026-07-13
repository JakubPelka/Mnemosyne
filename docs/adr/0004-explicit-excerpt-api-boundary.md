# ADR 0004: Jawna granica między fragmentem a kontekstem

Status: zaakceptowano 2026-07-13.

## Kontekst

Frontend potrzebuje wyszukiwania zdarzeń i wystąpień tematu, ale automatyczne zwracanie pełnych wiadomości lub rozmów zwiększa ryzyko przypadkowego ujawnienia danych i niepotrzebnie obciąża interfejs.

## Decyzja

Kontrakt API jest opisany modelami Pydantic. Wyszukiwanie i wystąpienia zwracają tylko fragment ograniczony długością, metadane oraz stabilny identyfikator źródłowy. Ograniczony zestaw wiadomości przed i po wybranym zdarzeniu jest pobierany osobnym endpointem. Graf, wyszukiwanie i wystąpienia domyślnie filtrują `privacy_level=private`.

## Konsekwencje

OpenAPI jednoznacznie opisuje dane dostępne frontendowi, a lista wyników nigdy nie zawiera całej rozmowy. Interfejs wykonuje dodatkowe żądanie po świadomym wybraniu fragmentu. Adapter może dostarczyć pola specyficzne dla źródła, takie jak rola, ale identyfikacja i filtrowanie pozostają oparte na wspólnym `Event`.
