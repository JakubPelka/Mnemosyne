# ADR 0008: Segmenty zdarzeń oddzielają analizę od wyszukiwania

## Status

Przyjęta — 2026-07-14.

## Kontekst

Jedna wiadomość może łączyć prozę, kod, komendy, logi, cytaty i artefakty narzędziowe. Analizowanie całego `Event.text` jako jednorodnej treści promowało tokeny techniczne do warstwy tematów, choć kod i logi nadal powinny pozostać wyszukiwalne.

## Decyzja

`Event.text` pozostaje pełnym, źródłowym tekstem wiadomości. Regenerowalna tabela `event_segments` zachowuje kolejność segmentów i osobno określa udział w analizie oraz wyszukiwaniu.

- proza ma wagę tematyczną `1.0`, a cytat `0.3`;
- kod, kod inline, komendy, logi, tabele i linki są wyszukiwalne, lecz mają wagę tematyczną `0.0`;
- artefakty narzędziowe są wyłączone z analizy i wyszukiwania użytkowego;
- osobny indeks FTS5 segmentów obsługuje zakresy `all`, `prose`, `code`, `commands` i `logs`, a wynik ujawnia wyłącznie typ dopasowania;
- wybór wyniku nadal otwiera ograniczony kontekst pełnej wiadomości;
- import i przebudowa analizy odtwarzają segmenty deterministycznie i bez duplikatów.

## Konsekwencje

Główny graf nie jest zasilany tokenami pochodzącymi wyłącznie z kodu i logów, a informacja techniczna pozostaje dostępna przez FTS. Klasyfikacja jest celowo heurystyczna i wymaga testów na nowych formatach. Ewentualny graf techniczny będzie w przyszłości osobną, opcjonalną warstwą i nie zostanie połączony z głównym grafem rozmów.
