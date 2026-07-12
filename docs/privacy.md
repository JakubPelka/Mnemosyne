# Prywatność i bezpieczeństwo

Mnemosyne przetwarza dane użytkownika lokalnie. Kod aplikacji nie może wysyłać treści rozmów, metadanych ani wygenerowanych indeksów do zewnętrznych usług. Nie planujemy telemetrii ani chmurowych API.

## Dane prywatne

Katalogi `sources/`, `data/`, `output/`, `imports/`, `exports/`, `raw_data/`, `private_data/`, `cache/`, `logs/`, `embeddings/` i `indexes/` mogą zawierać dane prywatne i są ignorowane przez Git. Eksporty archiwalne oraz lokalne bazy są ignorowane również według rozszerzenia. Wszystkie importowane zdarzenia domyślnie otrzymują `privacy_level=private`.

Surowe eksporty są zachowywane lokalnie w `sources/`. Importer traktuje je jako dane tylko do odczytu; ich usunięcie pozostaje świadomą decyzją użytkownika.

Logi, ostrzeżenia i wyjątki nie mogą zawierać tekstu wiadomości. Testy i dokumentacja mogą używać wyłącznie jawnie syntetycznych danych.

## Usunięcie danych lokalnych

Zatrzymaj aplikację, a następnie usuń lokalną bazę z `data/` oraz powiązane katalogi `output/`, `cache/`, `logs/`, `embeddings/` i `indexes/`. Usunięcie bazy nie usuwa oryginalnego eksportu z `sources/`.

## Kontrola przed publikacją

1. Uruchom `git status --ignored` i sprawdź, czy prywatne artefakty są ignorowane.
2. Uruchom `git ls-files` i upewnij się, że nie ma tam danych źródłowych, baz, logów ani wyników.
3. Przeszukaj śledzone pliki pod kątem sekretów i charakterystycznych prywatnych fraz.
4. Skontroluj całą historię Git, nie tylko bieżący commit.
5. Do czasu zakończenia audytu utrzymuj repozytorium jako prywatne.

Samo dopisanie reguły do `.gitignore` nie usuwa pliku, który był wcześniej śledzony, ani nie czyści historii Git.
