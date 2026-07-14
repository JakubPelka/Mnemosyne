# ADR 0006: Oddzielenie kandydatów od prezentowanych tematów

Status: zaakceptowano 2026-07-14.

## Kontekst

Pierwsza analiza zapisywała surowe słowa kluczowe bezpośrednio jako tematy. Utrudniało to zachowanie odrzuconych terminów, wyjaśnienie filtracji, preferowanie fraz i bezpieczne ręczne aliasy.

## Decyzja

Surowe n-gramy są trwałymi `CandidateTerm` z licznikami, wynikiem jakości, statusem i powodem odrzucenia. Prezentacyjny `Topic` jest osobną, stabilnie identyfikowaną jednostką. Tabela `topic_terms` łączy temat z terminami podstawowymi, aliasami i mapowaniami ręcznymi, a `event_candidate_terms` zachowuje diagnostyczne przypisania do zdarzeń.

Automatyczny graf korzysta wyłącznie z aktywnych tematów. Odrzucone terminy nie są kasowane. Ręczne mapowania z prawdziwych danych pozostają w ignorowanym pliku lokalnym; repo zawiera tylko przykład syntetyczny.

## Konsekwencje

Warstwa tematów jest wyjaśnialna i możliwa do ponownej budowy bez utraty surowych wyników. Frazy mogą zastępować ogólne unigramy, a API może osobno udostępnić diagnostykę. Grupowanie wariantów pozostaje celowo proste i nie rozwiązuje jeszcze pełnej lematyzacji, synonimii ani tematów wielojęzycznych.
