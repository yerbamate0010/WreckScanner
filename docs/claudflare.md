# Cloudflare quick tunnel

Ta instrukcja opisuje tymczasowe wystawienie aplikacji do internetu przez Cloudflare Tunnel.
Nie wymaga dostepu do routera ani przekierowania portow.

Gdy uzytkownik napisze "uruchom claudflare" albo "uruchom cloudflare", wykonaj ponizsze kroki w tej kolejnosci.

## 1. Uruchom lokalny serwer

W katalogu projektu:

```bash
cd /home/test/Desktop/cars_detector
.venv/bin/python server.py
```

Serwer ma wypisac:

```text
Serwer dziala na http://localhost:8000
```

Jesli sandbox blokuje otwarcie portu 8000, uruchom ten sam command z uprawnieniem `require_escalated`.

Nie uzywaj systemowego `python3 server.py`, bo moze nie miec zaleznosci takich jak `cv2`.

## 2. Sprawdz lub pobierz cloudflared

Sprawdz:

```bash
which cloudflared
```

Jesli `cloudflared` nie istnieje, na tym urzadzeniu pobierz binarke ARM64 do `/tmp`:

```bash
curl -L --fail -o /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
chmod +x /tmp/cloudflared
```

To jest tymczasowa instalacja. Po restarcie systemu `/tmp/cloudflared` moze zniknac i trzeba pobrac ponownie.

## 3. Uruchom tunel

Jesli `cloudflared` jest w systemie:

```bash
cloudflared tunnel --url http://localhost:8000
```

Jesli zostal pobrany do `/tmp`:

```bash
/tmp/cloudflared tunnel --url http://localhost:8000
```

W logu znajdz publiczny adres:

```text
https://...trycloudflare.com
```

Ten adres jest tymczasowy i dziala tylko tak dlugo, jak dzialaja oba procesy:

- `.venv/bin/python server.py`
- `cloudflared tunnel --url http://localhost:8000`

Po ponownym uruchomieniu tunelu adres zwykle bedzie inny.

## 4. Test po uruchomieniu

Sprawdz strone:

```bash
curl -I --max-time 15 https://ADRES.trycloudflare.com
```

Oczekiwane:

```text
HTTP/2 200
```

Sprawdz kafelek mapy przez proxy aplikacji:

```bash
curl -L --max-time 20 -o /tmp/test-tile.png -w '%{http_code} %{content_type} %{size_download}\n' 'https://ADRES.trycloudflare.com/wms_proxy/OGC_ortofoto_2025/MapServer/WMSServer?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=1&STYLES=&CRS=EPSG:4326&BBOX=51.089000,17.038000,51.090000,17.039000&WIDTH=256&HEIGHT=256&FORMAT=image/png'
```

Oczekiwane:

```text
200 image/png ...
```

## 5. Jesli mapa sie nie laduje

Sprawdz, czy frontend nie ma twardych adresow z portem 8000:

```bash
grep -n "localhost\|:8000\|http://" web/app.js
```

Aplikacja wystawiana przez HTTPS Cloudflare musi uzywac adresow wzglednych:

- `/api/...`
- `/wms_proxy/...`
- `/analiza/...`

Nie uzywaj w frontendzie:

- `http://${window.location.hostname}:8000/...`
- `http://localhost:8000/...`

## 6. Co powiedziec uzytkownikowi

Po udanym uruchomieniu podaj publiczny adres i napisz:

```text
Serwer dziala lokalnie na http://localhost:8000 i jest wystawiony przez Cloudflare Tunnel:
https://ADRES.trycloudflare.com

To jest tymczasowy adres trycloudflare.com. Bedzie dzialal dopoki dziala lokalny serwer, proces cloudflared i komputer ma internet.
```

## 7. Wersja stala

Do stalego adresu potrzeba domeny podpietej do Cloudflare oraz named tunnel, np.:

```text
cars.twojadomena.pl -> http://localhost:8000
```

Wersja stala nadal nie wymaga dostepu do routera.
