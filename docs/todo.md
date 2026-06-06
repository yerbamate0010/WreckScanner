# TODO

## Otwarte

- [ ] Doprecyzowanie oficjalnych źródeł nawierzchni.
  - Sprawdzić urzędowe źródła BDOT10k/SIP Wrocławia dla drogi/chodnika/parkingu/nawierzchni.
  - Jeśli będą dostępne stabilne dane urzędowe, rozdzielić je w UI od orientacyjnych danych OSM/Overpass.

- [ ] Uzupełnić frontendowe narzędzia jakości.
  - Dodać `Prettier` dla `web/*.js`, `web/*.css`, HTML, JSON i Markdown.
  - Rozważyć `pyright` albo `mypy` jako lekką kontrolę typów po ustabilizowaniu modułów.
  - Dodać `pre-commit` do uruchamiania format/lint przed commitem.
  - Opcjonalnie dodać `pip-audit` oraz `bandit` dla zależności i podstawowych ryzyk bezpieczeństwa.

- [ ] Modularyzacja frontendu.
  - `web/app.js` urósł i warto go dzielić etapami na moduły mapy, popupów, zdjęć, raportów i admina.
  - Robić to dopiero po ustabilizowaniu UI, bez mieszania z dużymi zmianami funkcjonalnymi.

- [ ] Doprecyzować legendę i ustawienia widoku pinezek.
  - Sprawdzić, czy po panelu warstw nadal potrzebne są dodatkowe opcje typu większe pinezki albo osobna legenda warstw.
  - Jeśli tak, dodać to jako ustawienie UI zamiast kolejnego stałego elementu na mapie.

- [ ] Przemyśleć warstwę podglądu pobranych GeoTIFF jako zwykłą mapę.
  - Rozdzielić dwa tryby: lekki podgląd zasięgu cache jako obrysy arkuszy oraz faktyczną warstwę zdjęć z cache.
  - Nie wysyłać pełnych plików `.tif` do przeglądarki; jeśli warstwa zdjęć powstanie, backend powinien czytać tylko małe okna z lokalnych GeoTIFF i zwracać kafelki PNG/JPEG.
  - Dodać manifest/sidecar dla pobranych GeoTIFF z rokiem, datą nalotu, pikselem, godłem, typem RGB/CIR, granicami i ścieżką pliku.
  - Rozważyć opcję w dolnym pasku wyboru mapy, np. warianty `GeoTIFF 2024/2025 cache`, widoczne tylko dla lat i obszarów faktycznie pobranych lokalnie.
  - Dodać osobny cache renderowanych kafelków GeoTIFF, żeby nie przeliczać dużych rastrów przy każdym przesunięciu mapy.

## Zamknięte / przeniesione z aktywnego TODO

- [x] Generator zgłoszenia do weryfikacji dla sprawy pojazdu.
  - Istnieje modal generowania zgłoszenia, szkic maila, ZIP/PDF, dodatkowe zdjęcia użytkownika oraz rozdzielenie flow admin/public.

- [x] Zdjęcia terenowe: dodawanie, grupowanie, pinezki, przenoszenie do sprawy pojazdu i użycie w raportach.

- [x] Warstwa nawierzchni OSM/Overpass.
  - Dodano osobną warstwę dla dróg, chodników, parkingów, krawężników i `surface`.
  - Warstwa jest domyślnie wyłączona, ma cache po stronie backendu i może być ukrywana przed użytkownikami niezalogowanymi.

- [x] Skalowanie celownika, przełączanie jego widoczności i overlay granic działek.

- [x] Podstawowe narzędzia jakości kodu.
  - `scripts/check.sh`, Ruff, testy jednostkowe krytycznej logiki i GitHub Actions `check` są wdrożone.

- [x] Porządkowanie publicznego repo v1.
  - Root ma główny polski `README.md`, dodatkowe materiały są w `docs/`.
  - Stare screenshoty dokumentacyjne, `cache_check.txt` i przykładowe zgłoszenie prywatności zostały usunięte z Gita.
  - Prywatne magazyny zdjęć, zgłoszeń i lokalne cache są ignorowane.
