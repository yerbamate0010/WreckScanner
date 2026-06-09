# WreckScanner

<div align="center">

### 🌐 [**🇵🇱 Polski**](README.md) &nbsp;·&nbsp; [🇬🇧 English](docs/README.en.md)

</div>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

WreckScanner pomaga dokumentować **pojazdy nieużytkowane lub zalegające w przestrzeni publicznej**. Porównuje ortofotomapy Wrocławia z lat 2020–2025, pokazuje kandydatów do ręcznej weryfikacji i pozwala prowadzić sprawy pojazdów ze zdjęciami terenowymi, raportami oraz kontrolą prywatności.

Aktualne wydanie: **v1.1**.

Wynik działania aplikacji jest materiałem pomocniczym do weryfikacji, nie rozstrzygnięciem o stanie prawnym pojazdu.

Demo wideo: [youtube.com/watch?v=LxChEHNJ2Jg](https://www.youtube.com/watch?v=LxChEHNJ2Jg)

---

## Spis treści

- [Szybki start](#szybki-start)
- [Co jest w v1.1](#co-jest-w-v1)
- [Jak liczymy score](#jak-liczymy-score)
- [Weryfikacja kandydatów](#weryfikacja-kandydatow)
- [Mapa i warstwy](#mapa-i-warstwy)
- [Panel administratora](#panel-administratora)
- [Sprawy pojazdów](#sprawy-pojazdow)
- [Zdjęcia terenowe](#zdjecia-terenowe-administratora)
- [Prywatność i zgłoszenia](#prywatnosc-i-zgloszenia)
- [Diagnostyka](#diagnostyka)
- [Backup](#backup)
- [CLI](#cli)
- [Kontrole lokalne](#kontrole-lokalne)
- [Wymagania](#wymagania)
- [Źródła danych](#zrodla-danych)
- [Artefakty lokalne](#artefakty-lokalne)
- [Roadmap](#roadmap)
- [Licencja](#licencja)

---

## Szybki start

```bash
pip install -r requirements.txt
source .venv/bin/activate
./.venv/bin/python server.py
```

Otwórz [http://localhost:8000](http://localhost:8000), wybierz miejsce na mapie, kliknij **Skanuj obszar**. Pobranie + analiza zajmuje 1–3 minuty.

Szczegółowe, oddzielne instrukcje uruchamiania znajdują się w [`docs/START.md`](docs/START.md).

> Model YOLO musi być dostępny w `weights/yolo11s-obb.pt` lub `weights/yolo11m-obb.pt` — wybór jest w ustawieniach.
> Pierwsze użycie GeoTIFF dla danego arkusza może pobrać duży plik źródłowy do cache. Kolejne skany tego arkusza używają lokalnego cache.

## Co jest w v1.1 <a id="co-jest-w-v1"></a>

- Skanowanie małego obszaru mapy modelem YOLO OBB i porównanie detekcji na ortofotomapach z lat 2020–2025.
- Interaktywna mapa Leaflet z podkładami Wrocławia, podglądem Geoportalu `STND`, podkładem `OSM`, celownikiem skanu i pinezkami wyników.
- Sprawy pojazdów dla ręcznie zweryfikowanych miejsc, z raportem, linkami weryfikacyjnymi i możliwością generowania pakietu ZIP/PDF.
- Zdjęcia terenowe dodawane przez użytkowników albo administratora, z kolejką zatwierdzania, trwałą anonimizacją i publicznymi kopiami bez EXIF.
- Publiczny tryb korzysta tylko z zatwierdzonych, zanonimizowanych zdjęć. Oryginały i dane robocze są dostępne wyłącznie administracyjnie.
- Overlay granic działek KIEG/EGiB i dymek identyfikacji działki dla punktu na mapie.
- Warstwa nawierzchni z danymi OSM/Overpass dla dróg, chodników, parkingów, krawężników i tagów `surface`; domyślnie jest wyłączona i ładowana dopiero po zaznaczeniu.
- Panel administratora z mapą cache GeoTIFF, limitem dysku, filtrami przeglądu zdjęć i ustawieniami widoczności warstw dla użytkowników niezalogowanych.
- Strony `/privacy` i `/report` dla zasad przetwarzania danych oraz wniosków o usunięcie, korektę lub anonimizację wpisu.

## Jak liczymy score

Dla każdego pojazdu śledzonego w wielu okresach:

| Składnik | Waga | O co chodzi |
|---|---:|---|
| **Pokrycie czasowe** (skor. widocznością) | 50% | W ilu rocznikach detektor widział auto w tym samym miejscu. Zasłonięte zielenią rok nie liczy się jako "brak auta". |
| **Spójność koloru** (HSV) | 25% | Czy to *to samo* auto, a nie różne pojazdy na tym samym miejscu parkingowym. |
| **Średnia pewność YOLO** | 15% | Jak bardzo detektor był pewny. |
| **Rozpiętość czasowa** | 10% | Bonus za auto widoczne od pierwszego do ostatniego rocznika. |

**Widoczność** liczona z ExG (2G−R−B) wokół pojazdu. Jeśli pojazd pada w 50% pod liście drzewa, brak detekcji nie jest dowodem braku auta.

## Weryfikacja kandydatów <a id="weryfikacja-kandydatow"></a>

Każdy kandydat w raporcie ma **6 linków na 1-klik**:

- 🚶 **Google Street View** — stan z poziomu ulicy, tablica, wgniecenia
- 🛰️ **Google Maps** / **Apple Maps** — aktualne ujęcia satelitarne
- 📸 **Mapillary** — historyczne zdjęcia uliczne z datami
- 🇵🇱 **Geoportal Krajowy** — archiwum dla całej Polski
- 📄 **Pełny raport** — miniatury roczne, metryki i score

## Mapa i warstwy <a id="mapa-i-warstwy"></a>

Główna mapa pokazuje aktualny podkład ortofoto, celownik skanu i warstwy pinezek. Dolny suwak wybiera wyłącznie podkład widoczny w Leaflet: roczniki Wrocławia `2020`-`2025` oraz podgląd Geoportalu Krajowego `STND`. Zmiana podkładu nie zmienia skanowania YOLO, pobieranych roczników ani generowanych raportów.

Dostępne warstwy widoku:

- sprawy pojazdów,
- zdjęcia terenowe pojazdów,
- zdjęcia infrastruktury,
- zdjęcia ekspozycji na dym,
- granice i numery działek KIEG/EGiB,
- nawierzchnia: drogi, chodniki, parkingi, krawężniki i materiał z danych OSM/Overpass.

Na suwaku podkładów dostępne są roczniki `2020`-`2025`, `STND` oraz `OSM`. Administrator może ograniczyć, które warstwy i podkłady widzą użytkownicy niezalogowani.

Menu kontekstowe mapy pozwala m.in. ustawić środek skanu, skopiować link do miejsca, pokazać lub ukryć celownik oraz sprawdzić działkę dla klikniętego punktu. Dymek działki pokazuje numer, identyfikator, obręb, gminę, powiat, województwo, powierzchnię oraz typ użytku, jeśli usługa KIEG go zwróci.

## Panel administratora <a id="panel-administratora"></a>

Panel administratora grupuje narzędzia, które wcześniej nie mieściły się w ustawieniach:

- dodawanie zdjęć terenowych,
- kolejkę przeglądu i anonimizacji zdjęć,
- kolejkę zgłoszeń prywatności,
- mapę i podsumowanie cache GeoTIFF,
- ustawienia warstw widocznych dla użytkowników niezalogowanych,
- retencję prywatnych oryginałów zdjęć.

Panel ustawień nadal pozwala zmienić:

- **Model YOLO** — `yolo11s-obb.pt` szybciej albo `yolo11m-obb.pt` dokładniej.
- **Czułość detekcji** — niższy próg daje więcej kandydatów, wyższy krótszą listę.
- **Zoom miniatur w raporcie** — 5 m, 7.5 m, 10 m, 15 m albo 20 m. To wpływa na widok miniatur dowodowych, nie na pobieraną skalę ortofotomapy.
- **Filtr ortofoto** — wspólny filtr poprawy obrazu używany w podglądzie mapy i przed YOLO. Ustawienia są zapisywane w `settings.json`.
- **Limit cache GeoTIFF** — domyślnie `4 GB`, z opcją braku limitu.

Przycisk **Domyślne** w panelu ustawień przywraca bazowe parametry filtra.

## Sprawy pojazdów <a id="sprawy-pojazdow"></a>

Sprawa pojazdu to ręcznie zapisane zgłoszenie do weryfikacji, nie automatyczna decyzja algorytmu.

- Zapis z mapy albo raportu tworzy katalog `zidentyfikowane_wraki/<wreck_id>/`.
- W środku są `record.json`, lokalny `index.html` oraz pakiety dowodowe z miniaturami rocznymi, snapshotem kandydata i metadanymi analizy.
- Po restarcie serwera `GET /api/wrecks` ładuje zapisane rekordy i nakłada pinezki na główną mapę.
- Mini legenda przy mapie pozwala pokazywać i chować osobno pinezki spraw pojazdów oraz zdjęć terenowych.
- Jeśli zapiszesz ponownie kandydata w promieniu kilku metrów, aplikacja aktualizuje istniejący rekord zamiast tworzyć nowy.
- Pinezka sprawy ma przycisk usunięcia, który usuwa lokalną sprawę i odświeża warstwę mapy.
- Gdy model nie wykryje pojazdu, kliknięcie w pobranym obszarze otwiera **Ręczną inspekcję miejsca** z historią miniatur. Przycisk zapisu zakłada ręczną sprawę `zidentyfikowane_wraki/<wreck_id>/` w klikniętych współrzędnych, oznaczoną jako `manual_inspection`.
- Generator zgłoszenia tworzy ZIP z osobnym `zgloszenie.txt` oraz `raport.html`, który zawiera sekcję **Treść zgłoszenia** z adresatem, tematem i szkicem maila. Obok ZIP-a powstaje też `PDF` z podsumowaniem, zdjęciami i treścią zgłoszenia. Publiczne `index.html` sprawy pojazdu nie zapisuje danych zgłaszającego.
- Użytkownik albo administrator może dodać zdjęcia z miejsca bezpośrednio do sprawy pojazdu z pinezki albo z publicznego `index.html` sprawy. Zdjęcia mają prywatny oryginał oraz publiczną kopię bez EXIF. Publicznie widoczne są wyłącznie zatwierdzone kopie po anonimizacji.

Katalog `zidentyfikowane_wraki/` jest ignorowany przez git.

## Zdjęcia terenowe <a id="zdjecia-terenowe-administratora"></a>

Zdjęcia terenowe są obsługiwane jako osobna warstwa mapy. Użytkownik może dodać zdjęcia do kolejki `pending`, a administrator może je zatwierdzać, anonimizować, przenosić do spraw i usuwać. Upload jest niezależny od spraw pojazdów i nie miesza się z wynikami analizy YOLO.

- Dozwolone są pliki JPG, PNG i WebP, maksymalnie `10 MB` na zdjęcie i do `25` zdjęć w jednej transzy uploadu.
- Backend zapisuje prywatny oryginał poza publiczną ścieżką oraz rekord zdjęcia w `zdjecia_terenowe/<photo_id>/`.
- Każde zdjęcie startuje ze statusem `pending`. Publiczna kopia `public.jpg` i miniatura `public_thumb.jpg` są publikowane dopiero po zatwierdzeniu przez administratora.
- Publiczne kopie są zawsze zapisywane bez EXIF i mogą mieć trwale wypalone redakcje, np. zamazane tablice rejestracyjne albo identyfikatory.
- Publiczne API zwraca tylko `public_image` i `public_thumb`; nie zwraca ścieżek ani URL-i prywatnych oryginałów.
- Aplikacja odczytuje GPS z EXIF. Jeśli zdjęcie nie ma GPS, zapisuje aktualny punkt mapy jako fallback i oznacza źródło współrzędnych w metadanych.
- W modalu uploadu administrator może zaznaczyć **Ignoruj GPS z EXIF i użyj punktu mapy**, gdy GPS telefonu jest mniej dokładny niż ręcznie ustawiony środek mapy.
- Administrator może ręcznie przesuwać pinezki zdjęć. Przesunięcie grupy zdjęć przenosi wszystkie zdjęcia z tej grupy i zapisuje źródło współrzędnych jako `manual`.
- W dymku zdjęcia albo grupy zdjęć administrator ma szybki przycisk **Edytuj anonimizację zdjęć**, który otwiera kolejkę przeglądu zawężoną do wybranych zdjęć.
- Publiczna warstwa zdjęć terenowych pokazuje wyłącznie zatwierdzone, zanonimizowane kopie. Oryginały są dostępne tylko przez endpointy administracyjne.

Katalog `zdjecia_terenowe/` jest lokalnym magazynem backendu i nie powinien trafiać do repozytorium.

## Prywatność i zgłoszenia <a id="prywatnosc-i-zgloszenia"></a>

Aplikacja rozdziela prywatne oryginały od publicznych kopii:

- każde zdjęcie ma prywatny oryginał oraz publiczną kopię,
- publiczna kopia jest zawsze zapisywana bez EXIF,
- publiczna kopia nie jest publikowana, dopóki `public_review_status` nie ma wartości `approved`,
- redakcje zdjęć są wypalane w pikselach pliku, nie nakładane jako overlay HTML/CSS,
- miniatury publiczne powstają z już zanonimizowanej kopii,
- publiczne API nie zwraca `original_path`, `original_url` ani prywatnych ścieżek plików.

Administrator może w kolejce zdjęć rysować, przesuwać, skalować i obracać prostokąty anonimizacji. Po zapisie backend generuje nową publiczną kopię i miniaturę. Retencja oryginałów może zastąpić stary prywatny oryginał wersją zanonimizowaną.

Publiczne raporty i pobieranie zdjęć korzystają z zatwierdzonych kopii publicznych oraz zdjęć dodanych przez użytkownika do konkretnego raportu. Flow administracyjny może korzystać z danych roboczych, ale publiczne endpointy pozostają ograniczone do zanonimizowanych zasobów.

Strona `/privacy` opisuje cel przetwarzania, zakres danych, retencję, odbiorców i prawa osób. Strona `/report` zapisuje wniosek o usunięcie, korektę lub anonimizację wpisu do kolejki administratora.

## Diagnostyka

Po każdej analizie powstaje `analiza/run_log.json`. To najważniejszy plik do debugowania jakości wyników.

Zawiera m.in.:

- ustawienia modelu, czułości, zoomu miniatur i filtra ortofoto,
- rozmiar obrazu, skalę px/m, `imgsz`, `eps_px` i czas analizy,
- jakość każdego rocznika: `sharpness`, `mean`, `std`,
- informacje WFS/GeoTIFF: rok, `piksel`, RGB/CIR, data nalotu, cache hit/download, rozmiar pliku,
- liczbę detekcji na rocznik i top kandydatów z obserwacjami.

Raport HTML ma link **diagnostyka JSON**, a `/api/analyze` zwraca `diagnostics_url`.

Dodatkowo lokalną bazę zdjęć i teczek można sprawdzić skryptem:

```bash
./.venv/bin/python scripts/diagnose_data.py
```

Skrypt kontroluje m.in. brakujące `record.json`, niepoprawne typy pinezek, brakujące oryginały prywatne i publiczne pochodne, błędne współrzędne, duplikaty zdjęć przeniesionych do teczek pojazdów oraz spójność ręcznych i automatycznych dowodów. Raport JSON można zapisać tak:

```bash
./.venv/bin/python scripts/diagnose_data.py --output-json analiza/data_diagnostics.json
```

## Backup

Projekt używa `restic` do backupu lokalnej bazy danych. Obrazy i sprawy dowodowe zostają plikami; nie przenosimy ich teraz do bazy SQL.

Szczegółowa instrukcja i aktualne lokalne ścieżki są w [`docs/BACKUP.md`](docs/BACKUP.md). Obecny lokalny backup jest w `.backups/wreckscanner-restic`, a plik hasła restic jest w `.restic_password` w katalogu głównym projektu.

Wrapper projektu uruchamia diagnostykę danych przed backupem i blokuje backup, jeśli diagnostyka ma `error`:

```bash
./.venv/bin/python scripts/backup_data.py run \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password
```

Domyślnie backup obejmuje:

- `zidentyfikowane_wraki/`
- `zdjecia_terenowe/`
- `prywatne_zdjecia/`
- `prywatne_zgloszenia/`
- `zgloszenia_prywatnosci/`
- `settings.json`, jeśli istnieje
- `analiza/data_diagnostics.json`

Nie obejmuje cache WMS/GeoTIFF, `.venv`, `.cache`, `dane_dla_AI/` ani plików raportów leżących luzem w katalogu projektu. `.admin_password` jest pomijany domyślnie; można go dołączyć świadomie przez `--include-admin-password`. Pliku `.restic_password` nigdy nie trzymaj razem z jedyną kopią backupu.

Podstawowe komendy:

```bash
./.venv/bin/python scripts/backup_data.py init --repo .backups/wreckscanner-restic --password-file .restic_password
./.venv/bin/python scripts/backup_data.py check --repo .backups/wreckscanner-restic --password-file .restic_password
./.venv/bin/python scripts/backup_data.py snapshots --repo .backups/wreckscanner-restic --password-file .restic_password
./.venv/bin/python scripts/backup_data.py forget --repo .backups/wreckscanner-restic --password-file .restic_password --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune
```

Hasło restic jest wymagane do odtworzenia backupu. Trzeba przechowywać jego kopię poza maszyną z aplikacją.

## CLI

```bash
python3 analyze.py                              # domyślnie
python3 analyze.py --conf 0.18 --eps 2.5        # czulej
python3 analyze.py --conf 0.35 --eps 1.5        # tylko twarde przypadki
python3 analyze.py --fast                       # szybciej, jedna skala dla najnowszego zdjęcia
python3 analyze.py --crop-m 7.5                 # zoom miniatur w raporcie
```

- `--conf` — próg pewności YOLO (historia; obecne zdjęcie i tak idzie czulszym wieloskalowym przebiegiem)
- `--eps` — tolerancja „to to samo miejsce" w **metrach** (1.5–3.0)
- `--model` — `weights/yolo11s-obb.pt` (domyślny) lub `weights/yolo11m-obb.pt` (wolniej, dokładniej)
- `--fast` — pomija wieloskalową detekcję najnowszego zdjęcia; szybsze, ale może znaleźć mniej kandydatów
- `--crop-m` — wielkość miniatur dowodowych w raporcie, domyślnie 7.5 m
- `--no-enhance` — wyłącza wspólny filtr ortofoto przed analizą YOLO

## Kontrole lokalne

Przed commitem możesz odpalić podstawowy zestaw kontroli:

```bash
pip install -r requirements-dev.txt
scripts/check.sh
```

Skrypt wybiera `.venv/bin/python`, jeśli istnieje, kompiluje moduły Pythona, odpala Ruff lint/format, testy jednostkowe i sprawdza whitespace przez `git diff --check`.

## Wymagania

- Python 3.10+
- ~3 GB miejsca (PyTorch + modele YOLO)
- GPU opcjonalnie (~10× szybciej), CPU w pełni wystarczy

## Źródła danych <a id="zrodla-danych"></a>

| Źródło | Lata | Częstotliwość |
|---|---|---|
| **Geoportal Wrocławia** (główne) | 2020–2025 | 1 nalot / rok |
| **Geoportal Krajowy** | 2010–dziś | 1–3 naloty / rok |
| **Mapillary** | 2014–dziś | crowdsourcing |
| **Street View / Apple Maps** | różnie | weryfikacja manualna |

**Endpointy WMS/WMTS:**
- `https://gis1.um.wroc.pl/arcgis/services/ogc/OGC_ortofoto_{rok}/MapServer/WMSServer`
- `https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMTS/StandardResolution`
- `https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/Archiwalne`

Warstwa `StandardResolution` jest używana tylko jako publiczny podgląd WMTS w dolnym suwaku. `HighResolution` nie jest pokazana osobno, bo dla testowanego obszaru dublowała podkład Wrocław 2024. Prawdziwa ortofotomapa Geoportalu (`TrueOrtho`) nie jest pokazywana w suwaku, bo w obecnym układzie mapy Leaflet/EPSG:3857 zwracała puste kafle dla testowanych punktów.

**WFS → GeoTIFF cache:**

Przycisk **Skanuj obszar** automatycznie sprawdza WFS Geoportalu Krajowego dla lat 2024/2025. Jeśli arkusz jest RGB i ma lepszą rozdzielczość wejściową (`<= 0.10 m/pixel`), aplikacja pobiera surowy GeoTIFF raz do cache:

- `dane_dla_AI/wfs_geotiff_cache/raw_geotiff/`

Kolejne skany tego samego arkusza nie pobierają pliku ponownie, tylko wycinają nowy kadr lokalnie. Duże arkusze mogą mieć około 1 GB, więc pierwsze pobranie może potrwać.

Limit cache GeoTIFF ustawiasz w panelu **Ustawienia → Cache GeoTIFF**. Domyślnie to `4 GB`. Po przekroczeniu limitu aplikacja usuwa najstarsze kompletne arkusze TIFF, ale zachowuje aktualnie używany arkusz. Przerwane pobrania `.part` są wznawiane przy kolejnym skanie.

Ręczny diagnostyczny spike nadal jest dostępny:

```bash
python3 scripts/download_geoportal_wfs_geotiff.py --list-only
python3 scripts/download_geoportal_wfs_geotiff.py --years 2025
```

## Artefakty lokalne

Te katalogi/pliki są lokalne i ignorowane przez git:

- `dane_dla_AI/` — pobrane ortofotomapy, metadane obszaru i cache GeoTIFF.
- `analiza/` — raport, miniatury, overlay, `candidates.json`, `run_log.json`.
- `zidentyfikowane_wraki/` — ręcznie zapisane sprawy pojazdów.
- `zdjecia_terenowe/` — lokalny magazyn rekordów zdjęć terenowych i publicznych kopii po anonimizacji.
- `prywatne_zdjecia/` — prywatne oryginały zdjęć używane tylko administracyjnie.
- `prywatne_zgloszenia/` — prywatne pakiety zgłoszeń i robocze artefakty raportów.
- `zgloszenia_prywatnosci/` — kolejka wniosków o usunięcie, korektę lub anonimizację.
- `settings.json` — lokalne ustawienia filtra ortofoto.
- `.cache/` — cache Matplotlib i inne lokalne pliki pomocnicze.
- `.backups/` — lokalne szyfrowane repozytorium backupu restic.

## Roadmap

Bieżące pomysły są zapisane w [docs/todo.md](docs/todo.md). Najbliższe większe kierunki:

- dokładniejsze rozpoznanie typu terenu przy dymku działki, np. droga, chodnik, parking, zieleń i nawierzchnia,
- stopniowa modularyzacja dużych plików frontendu,
- dalsze dopracowanie ustawień widoku pinezek i legendy mapy.

## Licencja

[MIT](LICENSE) — możesz używać, modyfikować i dystrybuować bez ograniczeń, byle zachować nagłówek copyright.

Dane ortofoto pochodzą z UM Wrocławia i Geoportalu Krajowego — sprawdź ich warunki użytkowania osobno.
