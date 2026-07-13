# ADR 0005: Lokalna wizualizacja React, Sigma i Graphology

Status: zaakceptowano 2026-07-13.

## Kontekst

Visual MVP musi płynnie pokazać setki tematów i tysiące relacji bez wysyłania danych, ciężkiego silnika dashboardowego ani uzależniania interfejsu od formatu ChatGPT.

## Decyzja

Frontend używa React, Sigma.js i Graphology. Odpowiedź neutralnego API jest mapowana do grafu po stronie klienta. Rozmiar węzła używa transformacji logarytmicznej, krawędź skaluje grubość według wagi, a początkowe pozycje są deterministyczne. ForceAtlas2 wykonuje ograniczoną liczbę iteracji, a pozycje węzłów są buforowane podczas sesji, aby drobne zmiany filtrów nie przestawiały całego grafu. Vite komunikuje się z backendem przez lokalne `/api`.

## Konsekwencje

Interfejs pozostaje lekki, lokalny i niezależny od źródła. Użytkownik może obniżyć próg relacji do analizy gęstszego grafu, ale domyślny próg preferuje czytelność. Bardzo duże limity węzłów mogą nadal wymagać czasu na synchroniczny układ; liczba iteracji jest wtedy automatycznie redukowana.
