# Format eksportu OpenAI / ChatGPT

Status: format pierwszego eksportu został zbadany strukturalnie 13 lipca 2026 r.

Raport nie zawiera prawdziwej treści rozmów, wartości profilu użytkownika, identyfikatorów rekordów ani jawnych lokalnych ścieżek.

## Kontener eksportu

Źródłem jest archiwum ZIP z plikami na najwyższym poziomie. Archiwum nie zawierało zaszyfrowanych wpisów, ścieżek absolutnych ani segmentów `..`. Zostało lokalnie wypakowane do ignorowanego katalogu `sources/`; parser obsługuje również bezpośredni odczyt ZIP-a bez wypakowywania.

Rozmowy znajdują się w osiemnastu shardach od `conversations-000.json` do `conversations-017.json`. Każdy shard jest tablicą obiektów rozmów. Parser obsługuje także starszą pojedynczą nazwę `conversations.json` i nie zależy od dokładnej liczby shardów.

Pozostałe pliki obejmują między innymi:

- `export_manifest.json` — manifest oraz wersja logicznej struktury eksportu;
- `conversation_asset_file_names.json` i pliki `.dat` — mapowanie oraz binarne zasoby rozmów;
- `library_files.json` — metadane biblioteki plików;
- `message_feedback.json` — oceny wiadomości;
- `shared_conversations.json` — metadane rozmów udostępnionych;
- `user.json` i `user_settings.json` — profil oraz ustawienia użytkownika;
- `chat.html` — przeglądarkowa reprezentacja eksportu.

MVP importuje wyłącznie shardy rozmów. Załączniki, profil, ustawienia, oceny, rozmowy udostępnione, bibliotekę plików i HTML pozostawiamy w surowym źródle bez normalizacji.

## Rozmowa

Obiekt rozmowy zawiera stabilne `id` i/lub `conversation_id`, `title`, uniksowe `create_time` i `update_time`, `current_node` oraz słownik `mapping`. Występują także opcjonalne informacje o modelu, archiwizacji, przypięciu, pamięci, trybie badania, głosie, szablonie i pluginach. MVP zachowuje podstawowe pola rozmowy; pozostałe metadane mogą zostać dodane później bez zmiany wspólnego modelu zdarzeń.

## Wiadomość i drzewo

`mapping` jest słownikiem indeksowanym identyfikatorem węzła. Każdy węzeł zawiera `id`, `parent` oraz `message`. Korzeń rozmowy ma `message=null`; pozostałe węzły wskazują rodzica. Eksport nie zawiera gotowej listy dzieci, więc parser odwraca relacje `parent` i buduje drzewo.

Wiadomość zawiera `id`, `author`, `create_time`, `content` i `metadata`. Rola autora znajduje się w `author.role`. W badanym eksporcie występują role `user` i `assistant`.

Kolejność jest wyznaczana deterministycznym przejściem drzewa od korzenia. Rodzeństwo jest sortowane według czasu, a następnie identyfikatora. `sequence_number` obejmuje wszystkie gałęzie, natomiast ścieżka od `current_node` do korzenia otrzymuje osobne oznaczenie `is_on_current_branch`. Relacja `parent_message_id` pozostaje podstawą do późniejszego pobierania kontekstu w obrębie właściwej gałęzi.

## Typy treści

Występują cztery typy:

- `text` — tekst w tablicy `parts`;
- `multimodal_text` — tekst oraz strukturalne odwołania do obrazu, audio lub wideo;
- `thoughts` — wewnętrzne rekordy rozumowania;
- `reasoning_recap` — tekstowe podsumowanie rozumowania.

MVP łączy tekstowe elementy `parts`, ignorując binarne odwołania do zasobów. Rekordy `thoughts` są zachowane strukturalnie, ale ich zawartość nie trafia do tekstu ani analizy tematów. `reasoning_recap` jest zachowywany jako tekst. Ta decyzja wymaga ponownej oceny przed budową indeksu tematów.

## Daty, braki i stabilność

Daty rozmów i wiadomości są liczbami zmiennoprzecinkowymi reprezentującymi Unix time w sekundach; parser normalizuje je do UTC. Implementacja toleruje także ISO 8601, brak daty, pusty tekst, nieznany typ treści i częściowo uszkodzone rekordy. Ostrzeżenia zawierają wyłącznie kod oraz numer rekordu, nigdy treść, tytuł ani identyfikator użytkownika.

Gdy brakuje identyfikatora rozmowy, adapter tworzy deterministyczny hash metadanych strukturalnych i kluczy mapowania. Hash nie zależy od nazwy shardu ani pozycji rekordu. Na rzeczywistym eksporcie wszystkie rozmowy i wiadomości miały źródłowe identyfikatory, a wszystkie znaczniki czasu miały oczekiwany format liczbowy.

`message_id` nie jest globalnie unikalne w całym eksporcie; powtórzenia występują pomiędzy różnymi rozmowami. Stabilną tożsamością wiadomości w bazie jest dlatego para `(conversation_id, message_id)`. W obrębie tej pary nie wykryto duplikatów.
