# ADR 0001: Analiza oparta na neutralnym zdarzeniu

Status: zaakceptowano 2026-07-13.

## Kontekst

Analiza tematów nie może zależeć od tabel ChatGPT, ponieważ kolejne źródła mają korzystać z tego samego grafu, osi czasu i API.

## Decyzja

Wspólny `Event` otrzymuje `context_id`, `is_active` i `analysis_enabled`. Importer mapuje własne pojęcie rozmowy lub grupy na `context_id` oraz jawnie określa udział tekstu w NLP. Usługi tematów i grafu czytają wyłącznie wspólne tabele.

## Konsekwencje

Nowe adaptery nie wymagają zmian w analizie. Rekord może pozostać przechowany i wyszukiwalny, mimo że jest wyłączony z NLP. Kosztem są dodatkowe reguły mapowania po stronie każdego adaptera.
