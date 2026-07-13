# Kamień milowy: Visual MVP

Data: 2026-07-13  
Status: ukończono i zweryfikowano.

## Aktualny stan funkcjonalny

Mnemosyne zapewnia kompletny lokalny przepływ: SQLite → typowane API → czasowy graf tematów → wybór tematu → paginowane fragmenty → ograniczony kontekst wiadomości. Frontend działa jako pojedynczy responsywny widok React/Sigma i nie korzysta z zewnętrznych API, fontów, CDN, analityki ani telemetrii.

API udostępnia metadane, filtrowany graf, wyszukiwanie tematów, szczegóły i intensywność tematu, wystąpienia, FTS5 oraz ograniczony kontekst. Wszystkie odpowiedzi mają modele Pydantic widoczne w OpenAPI. Graf, wyszukiwanie i wystąpienia jawnie przyjmują `privacy_level`, domyślnie `private`.

## Kryteria ukończenia

- [x] backend i frontend uruchamiają się lokalnie;
- [x] graf tematów obsługuje zoom i przesuwanie;
- [x] zakres miesięcy ogranicza graf i intensywność;
- [x] wyszukiwanie wybiera temat;
- [x] kliknięcie węzła pokazuje statystyki, intensywność i relacje;
- [x] panel pokazuje paginowane fragmenty źródłowe;
- [x] wybór fragmentu ładuje wyłącznie ograniczony kontekst;
- [x] cały przepływ działa bez wysyłania danych poza komputer;
- [x] Docker Compose uruchamia cały stos jedną komendą;
- [x] wszystkie wymagane testy i kontrole kończą się powodzeniem.

## Wyniki testów

- `pytest -q`: 22 zaliczone, 3 ostrzeżenia deprecacyjne zależności;
- `ruff check backend scripts`: zaliczone;
- `ruff format --check backend scripts`: 33 pliki poprawnie sformatowane;
- `alembic check`: brak nowych operacji migracyjnych;
- `npm test`: 9 zaliczonych w 4 plikach;
- `npm run build`: 61 modułów, poprawny produkcyjny bundle;
- `npm audit --audit-level=high`: zero podatności;
- `docker-compose config --quiet`: zaliczone;
- obrazy backendu i frontendu: zbudowane;
- healthchecki Compose: oba serwisy zdrowe;
- integracja przez frontendowe `/api`: HTTP 200 dla metadanych i grafu;
- test wizualny 1440×900: poprawny układ, graf i stany początkowe.

## Ocena grafu na rzeczywistych danych

Ocena została wykonana bez zapisywania ani raportowania nazw tematów, tytułów i fragmentów. Lokalna baza zawiera 494 aktywne tematy oraz 24 361 zapisanych relacji. Przy domyślnym widoku 100 węzłów i minimalnej wadze `0.15` renderowanych jest 99 relacji, co daje czytelny punkt startowy. Obniżenie progu do zera zwiększa ten sam widok do 3674 krawędzi i tworzy nieczytelny graf, dlatego próg pozostaje regulowany, ale domyślnie konserwatywny.

Graf jest już wystarczający do użytkowej walidacji. Widać jednocześnie ograniczenie pierwszej analizy słów kluczowych: część tematów jest zbyt ogólna, a wielojęzyczny korpus wymaga dalszego strojenia stopwords. Następna iteracja powinna opierać się na obserwacjach z UI, nie na dodawaniu embeddingów.

## Znane błędy i ograniczenia

- brak potwierdzonych błędów blokujących Visual MVP;
- układ bardzo dużych grafów jest synchroniczny, choć liczba iteracji ForceAtlas2 jest automatycznie ograniczana;
- przy niskich progach graf świadomie dopuszcza bardzo dużą liczbę krawędzi;
- rola wystąpienia pochodzi obecnie z adaptera ChatGPT i dla przyszłych źródeł może być pusta;
- wyszukiwanie FTS używa bezpiecznej koniunkcji tokenów, bez wyszukiwania semantycznego;
- środowisko testowe nie ma Node.js na hoście, więc testy frontendowe wykonano w odizolowanym kontenerze Node bez dostępu do prywatnych katalogów;
- lokalny Docker Compose 1.29 korzysta z legacy buildera; migracja do Compose v2/BuildKit jest wskazana operacyjnie, ale nie blokuje działania.

## Prywatność, bezpieczeństwo i status Git

- repozytorium ma status `TEMP PUBLIC`;
- końcowy audyt bieżącego drzewa i całej historii nie wykazał eksportów, baz, archiwów, logów, sekretów, adresów e-mail ani lokalnych ścieżek;
- `sources/`, baza SQLite, logi, wyniki, `.env` i archiwa są ignorowane;
- żaden ignorowany plik nie jest śledzony;
- kontekst Docker ma 429,1 KiB i nie obejmuje `sources/` ani 181-MB lokalnej bazy;
- inspekcja obrazów potwierdziła brak `sources/`, `data/` i `.env`;
- `sources/` jest montowane tylko do odczytu, `data/` lokalnie do zapisu;
- porty 5173 i 8000 są publikowane wyłącznie na `127.0.0.1`;
- frontend produkcyjny działa jako nieuprzywilejowany Nginx, backend jako UID 1000;
- nie dodano licencji.

## Uruchomienie

Bez Dockera, w dwóch terminalach:

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

Następnie otwórz `http://127.0.0.1:5173`.

Cały stos przez Compose:

```bash
docker-compose up --build
```

Po pracy:

```bash
docker-compose down
```

## Commity kamienia milowego

- `2b42516` — normalizacja instrukcji i statusu projektu;
- `47366ec` — typowany kontrakt wyszukiwania i szczegółów tematu;
- `1ea6133` — czasowy graf i wizualny pionowy wycinek;
- `2088608` — obrazy, Compose, walidacja i audyt.

## Rekomendowany następny etap

Użyć Visual MVP przez kilka sesji na lokalnych danych i zebrać konkretne przykłady tematów zbyt ogólnych, duplikatów językowych oraz nieczytelnych relacji. Następnie dostroić stopwords, progi i heurystyki lokalnego TF-IDF. Embeddingi, ontologia i nowe importery pozostają poza następną iteracją, dopóki prostsze korekty nie zostaną ocenione wizualnie.
