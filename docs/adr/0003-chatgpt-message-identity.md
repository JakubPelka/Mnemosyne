# ADR 0003: Tożsamość wiadomości ChatGPT jest złożona

Status: zaakceptowano 2026-07-13.

## Kontekst

Inspekcja pełnego eksportu wykazała, że `message_id` może powtarzać się pomiędzy różnymi rozmowami. Globalny klucz oparty wyłącznie na tym polu powodował nadpisywanie rekordów.

## Decyzja

Stabilnym kluczem wiadomości i odpowiadającego zdarzenia jest para `(conversation_id, message_id)`. Oryginalny `message_id` pozostaje osobnym polem źródłowym.

## Konsekwencje

Ponowny import zachowuje wszystkie wiadomości i jest idempotentny. Każde odwołanie do wiadomości poza adapterem powinno używać wewnętrznego klucza lub `event_id`, nie samego źródłowego `message_id`.
