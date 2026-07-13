# ADR 0002: Lokalny graf słów kluczowych jako pierwsza analiza

Status: zaakceptowano 2026-07-13.

## Kontekst

MVP potrzebuje użytecznego grafu bez chmurowych modeli, płatnych API i ciężkich zależności NLP.

## Decyzja

Pierwsza analiza wykorzystuje normalizację Unicode, wspólne stopwords PL/SV/EN, unigramy, bigramy i lokalny TF-IDF. Do zdarzenia przypisywana jest ograniczona liczba tematów. Relacje liczą współwystąpienia w wiadomościach i kontekstach oraz przechowują znormalizowaną wagę.

## Konsekwencje

Proces jest deterministyczny, szybki i całkowicie lokalny. Nie rozpoznaje jednak synonimów, odmiany wyrazów ani równoważnych tematów między językami. Jakość musi zostać zweryfikowana wizualnie przed dodaniem cięższych metod.
