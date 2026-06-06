# WreckScanner — rekomendacje poprawek aplikacji

Data: 2026-06-03  
Cel dokumentu: uporządkować najbliższe poprawki aplikacji WreckScanner tak, aby pojedyncza „teczka wraku” była wiarygodna technicznie, użyteczna dla mieszkańca i trudna do zignorowania przez służby/urząd.

---

## 1. Kierunek produktu

WreckScanner powinien być nie tylko mapą wraków, ale systemem prowadzenia „teczki pojazdu”. Każdy pojazd powinien mieć jedną kartę sprawy, do której można dopinać kolejne dowody: ortofotomapy, zdjęcia terenowe, metadane, zgłoszenia, odpowiedzi urzędu i statusy.

Najważniejsze założenie:

> Jedno auto = jedna teczka. Wiele osób może dodawać kolejne obserwacje, ale nie powinno powstawać wiele rozproszonych zgłoszeń dla tego samego pojazdu.

Docelowo aplikacja powinna wspierać dwa tryby:

1. **Tryb publiczny** — karta sprawy z zamaskowanymi tablicami i bez danych osobowych.
2. **Tryb urzędowy** — pełny raport/ZIP/PDF z oryginalnymi zdjęciami, EXIF, hashem plików, pełnymi tablicami, danymi zgłaszającego i formalnym wnioskiem.

---

## 2. Podstawa prawna i język raportu

Główna podstawa dla zgłoszeń to art. 50a Prawa o ruchu drogowym: pojazd pozostawiony bez tablic rejestracyjnych albo pojazd, którego stan wskazuje, że nie jest używany, może zostać usunięty z drogi przez straż gminną lub Policję na koszt właściciela albo posiadacza.

Źródło: https://sip.lex.pl/akty-prawne/dzu-dziennik-ustaw/prawo-o-ruchu-drogowym-16798732/art-50-a

W raportach należy unikać przesądzających sformułowań typu:

- „auto na pewno jest porzucone”,
- „auto na pewno jest niesprawne”,
- „auto na pewno stoi 5 lat”,
- „to musi zostać odholowane”.

Zalecany język:

- „stan pojazdu wskazuje na możliwe długotrwałe nieużytkowanie”,
- „widoczne cechy uzasadniają przeprowadzenie oględzin”,
- „wnoszę o ocenę przesłanek z art. 50a p.r.d.”,
- „w ocenie zgłaszającego, widoczny stan elementów układu hamulcowego budzi poważne wątpliwości co do możliwości bezpiecznego użytkowania pojazdu”.

Dla wniosku formalnego warto stosować tytuł:

> Wniosek o podjęcie czynności w trybie art. 50a Prawa o ruchu drogowym oraz o udzielenie informacji o sposobie rozpoznania sprawy.

Jeżeli użytkownik oczekuje urzędowej odpowiedzi, dokument powinien zawierać dane zgłaszającego i wyraźne żądanie pisemnej informacji o podjętych czynnościach. W sprawach skarg i wniosków KPA przewiduje załatwienie sprawy bez zbędnej zwłoki, zasadniczo nie później niż w ciągu miesiąca.

Źródło: https://sip.lex.pl/akty-prawne/dzu-dziennik-ustaw/kodeks-postepowania-administracyjnego-16784712/art-237

---

## 3. Struktura teczki wraku

Każda teczka powinna mieć spójny model danych:

```text
Teczka wraku
├── Identyfikacja
│   ├── ID sprawy
│   ├── status
│   ├── lokalizacja
│   ├── marka/model/kolor, jeśli znane
│   ├── tablica, tylko w trybie urzędowym
│   └── źródła identyfikacji
│
├── Oś czasu lotnicza
│   ├── 2020
│   ├── 2021
│   ├── 2022
│   ├── 2023, jeśli dostępne
│   ├── 2024
│   ├── 2025
│   └── 2026, po publikacji danych
│
├── Oś czasu terenowa
│   ├── zdjęcia zgłaszającego
│   ├── kolejne obserwacje użytkowników
│   ├── EXIF/GPS/hash
│   └── status weryfikacji zdjęcia
│
├── Objawy nieużytkowania
│   ├── hamulce
│   ├── opony
│   ├── zabrudzenia wokół auta
│   ├── karoseria i szyby
│   ├── brak tablic
│   └── kontekst miejsca
│
├── Działania
│   ├── wygenerowano wniosek
│   ├── wysłano przez użytkownika
│   ├── numer sprawy, jeśli jest
│   ├── odpowiedź urzędu
│   ├── ponaglenie/skarga
│   └── wynik
│
└── Eksport
    ├── raport HTML
    ├── raport PDF
    ├── pismo TXT/PDF
    └── ZIP urzędowy
```

---

## 4. Statusy sprawy

Rekomendowane statusy:

| Status | Znaczenie |
|---|---|
| `candidate` | kandydat wykryty automatycznie lub zgłoszony bez pełnej dokumentacji |
| `field_confirmed` | potwierdzony zdjęciem terenowym |
| `long_term_evidence` | ma historię w ortofoto albo co najmniej dwie obserwacje terenowe w czasie |
| `ready_for_report` | spełnia minimalne kryteria do wygenerowania wniosku |
| `report_generated` | użytkownik wygenerował raport/ZIP |
| `sent_by_user` | użytkownik oznaczył, że wysłał zgłoszenie |
| `waiting_for_response` | oczekiwanie na odpowiedź |
| `response_received` | użytkownik dodał odpowiedź urzędu |
| `removed` | pojazd usunięty |
| `rejected` | odmowa / brak podstaw |
| `no_action_30d` | brak reakcji po 30 dniach |
| `followup_needed` | wymaga ponaglenia albo kolejnej obserwacji |

---

## 5. Pierwsza karta raportu

Pierwsza karta powinna być zrozumiała w 10 sekund. Proponowany układ:

```text
Teczka pojazdu

Status: potwierdzony terenowo
Siła sprawy: niska / średnia / wysoka / bardzo wysoka
Ostatnia obserwacja: YYYY-MM-DD HH:MM
Lokalizacja: adres opisowy + GPS
Pojazd: marka/model/kolor, jeśli znane
Tablice: widoczne / brak przedniej / brak tylnej / brak obu / ukryte w wersji publicznej
Lata widoczności na ortofoto: 2020, 2021, 2022, 2024, 2025
Zdjęcia terenowe: liczba
Najważniejsze przesłanki:
- brak tablicy / pusta ramka,
- widoczna korozja tarcz hamulcowych,
- nagromadzone osady i liście przy kołach,
- pojazd widoczny na ortofotomapach w wielu latach,
- lokalizacja w miejscu utrudniającym parkowanie/ruch/wjazd.

Wniosek: oględziny i ocena przesłanek z art. 50a p.r.d.
```

---

## 6. Zdjęcia terenowe i EXIF

### 6.1. Oryginały

W pakiecie urzędowym nie usuwać EXIF z oryginalnych zdjęć. Oryginały powinny być przechowywane oddzielnie:

```text
zdjecia_oryginalne/
  20260602_213652_original.jpg
  20260602_213701_original.jpg
  20260602_213711_original.jpg
```

Dla każdego oryginału należy zapisać:

- nazwę pliku,
- SHA-256,
- datę z EXIF,
- GPS z EXIF, jeśli istnieje,
- źródło GPS: EXIF / formularz / ręczne wskazanie na mapie,
- użytkownika/zgłaszającego, jeśli zgoda i tryb urzędowy,
- informację, czy zdjęcie jest widoczne publicznie.

Przykład:

```json
{
  "filename": "20260602_213652_original.jpg",
  "sha256": "...",
  "taken_at_exif": "2026-06-02T21:36:52",
  "gps": [51.1100, 17.0300],
  "gps_source": "EXIF GPS",
  "public_visible": false,
  "included_in_official_zip": true
}
```

### 6.2. Podglądy

Do raportu HTML używać kopii podglądowych:

```text
zdjecia_podgladowe/
  20260602_213652_preview.webp
  20260602_213701_preview.webp
```

Podglądy mogą mieć usunięty EXIF, zmniejszoną rozdzielczość i zamaskowane tablice, jeśli raport jest publiczny.

### 6.3. Raport HTML

Raport HTML powinien pokazywać zdjęcia terenowe przed ortofoto. Kolejność:

1. karta sprawy,
2. zdjęcia terenowe,
3. oś czasu,
4. ortofoto,
5. objawy nieużytkowania,
6. treść zgłoszenia,
7. załączniki i metadane.

---

## 7. Oś czasu zdjęć terenowych

Zdjęcia terenowe powinny tworzyć timeline niezależny od ortofoto.

Minimalny model obserwacji:

```json
{
  "observation_id": "obs_20260602_213652_xxxx",
  "wreck_id": "wreck_...",
  "created_at": "2026-06-02T21:36:52",
  "source": "field_photo",
  "author_type": "owner | user | anonymous | moderator",
  "photos": ["..."],
  "gps": [51.1100, 17.0300],
  "gps_source": "EXIF GPS",
  "notes": "Widoczna korozja tarcz i brak przedniej tablicy",
  "verification_status": "pending | accepted | rejected | needs_review"
}
```

Dla użytkownika końcowego timeline może wyglądać tak:

```text
2021 — widoczny na ortofoto
2024 — widoczny na ortofoto
2025 — widoczny na ortofoto
2026-06-02 — zdjęcia terenowe, potwierdzono stan pojazdu
2026-07-15 — kolejna obserwacja użytkownika, nadal stoi
```

---

## 8. Zgłoszenie TXT/PDF w raporcie HTML

Plik `zgloszenie.txt` powinien być wyświetlany w raporcie HTML jako osobna sekcja:

```html
<section id="formal-request">
  <h2>Treść wniosku do Straży Miejskiej / urzędu</h2>
  <pre>...</pre>
</section>
```

Dodatkowo raport powinien mieć przycisk:

- `Kopiuj treść wniosku`,
- `Pobierz TXT`,
- `Pobierz PDF`,
- `Pobierz ZIP z załącznikami`.

---

## 9. Generator wniosku

Generator powinien mieć pola:

- imię i nazwisko zgłaszającego,
- adres do korespondencji albo e-mail,
- telefon opcjonalnie,
- adresat: Straż Miejska / Policja / urząd / zarządca terenu,
- dokładna lokalizacja pojazdu,
- opis pojazdu,
- objawy nieużytkowania,
- czy pojazd utrudnia ruch / parkowanie / wjazd,
- żądanie pisemnej odpowiedzi,
- zgoda na dołączenie zdjęć i metadanych.

Generator powinien blokować tryb „oficjalny”, jeśli dane zgłaszającego są oczywiście testowe, np. `Jan Kowalski`, `555 555 555`, `test@test.pl`, chyba że użytkownik oznaczy paczkę jako testową.

---

## 10. Przykładowy tekst wniosku

```text
[Imię i nazwisko]
[Adres / e-mail]
[Telefon opcjonalnie]

[Data]

Do: [Straż Miejska / Policja / Urząd]

WNIOSEK O PODJĘCIE CZYNNOŚCI W TRYBIE ART. 50A PRAWA O RUCHU DROGOWYM
ORAZ O UDZIELENIE INFORMACJI O SPOSOBIE ROZPOZNANIA SPRAWY

Wnoszę o przeprowadzenie oględzin i podjęcie czynności wobec pojazdu znajdującego się w lokalizacji:
[adres opisowy, współrzędne GPS].

Opis pojazdu:
[marka/model/kolor/nr rejestracyjny, jeśli znany].

Uzasadnienie:
Stan pojazdu wskazuje na możliwe długotrwałe nieużytkowanie. W dokumentacji fotograficznej widoczne są następujące cechy:
- [cecha 1],
- [cecha 2],
- [cecha 3].

Dodatkowo dokumentacja obejmuje zdjęcia terenowe z datą i lokalizacją oraz, jeśli dostępne, porównanie widoczności pojazdu na ortofotomapach z kolejnych lat.

Wnoszę o ocenę przesłanek do usunięcia pojazdu w trybie art. 50a Prawa o ruchu drogowym oraz o pisemną informację, jakie czynności zostały podjęte, w szczególności:
1. czy przeprowadzono oględziny,
2. czy ustalono właściciela lub posiadacza pojazdu,
3. czy skierowano wezwanie do usunięcia pojazdu,
4. czy wydano dyspozycję usunięcia pojazdu,
5. w przypadku odmowy — o wskazanie podstawy odmowy.

Załączniki:
- raport HTML/PDF,
- zdjęcia terenowe,
- metadane zdjęć,
- oś czasu ortofoto, jeśli dostępna,
- plik z danymi sprawy.

[Podpis]
```

---

## 11. Objawy nieużytkowania — klasyfikacja

Aplikacja powinna mieć checklistę objawów. Każdy objaw powinien mieć opis ostrożny, a nie kategoryczny.

### 11.1. Tarcze hamulcowe

Tarcze są jednym z najlepszych elementów do dokumentowania. Należy jednak rozróżnić lekki nalot po wilgoci od głębokiej korozji i śladów niepełnej pracy klocka.

| Objaw | Siła przesłanki | Opis do raportu |
|---|---|---|
| lekki pomarańczowy nalot | słaba | możliwy krótkotrwały efekt wilgoci |
| rdza na całej powierzchni roboczej | średnia | może wskazywać na brak niedawnej jazdy |
| rowki, pofalowania, wżery | mocna | wymaga kontroli technicznej układu hamulcowego |
| brak śladów pełnego kontaktu klocka z tarczą | bardzo mocna | może wskazywać na nieprawidłową pracę hamulca |
| tarcza skorodowana + zacisk/piasta/śruby skorodowane | bardzo mocna | silna przesłanka długotrwałego postoju i/lub niesprawności |

Przykładowy tekst:

> Widoczny stan tarczy hamulcowej wykracza poza typowy lekki nalot korozyjny powstający po krótkim postoju w wilgoci. Na zdjęciu widoczne są rowki, nierówna powierzchnia robocza, głębsza korozja oraz miejscami brak śladów pełnego kontaktu klocka hamulcowego z tarczą. W ocenie zgłaszającego może to wskazywać zarówno na długotrwałe nieużytkowanie pojazdu, jak i na niesprawność elementów układu hamulcowego.

### 11.2. Opony

| Objaw | Siła przesłanki |
|---|---|
| lekko ugięta opona | słaba/średnia |
| wyraźnie płaska opona | mocna |
| deformacja opony od postoju | mocna |
| brudny ślad wokół opony | średnia |
| pęknięcia boków opony | mocna |
| stary DOT, jeśli widoczny | pomocnicza |

Opis:

> Widoczne ugięcie lub spłaszczenie opony może wskazywać na długotrwały brak dopompowania albo nieszczelność. Sam objaw nie przesądza o czasie postoju, ale w połączeniu z innymi cechami stanowi przesłankę nieużytkowania.

### 11.3. Brud wokół pojazdu

| Objaw | Znaczenie |
|---|---|
| liście pod autem i przy kołach | brak rotacji miejsca |
| pył i piasek przy oponach | długotrwały postój |
| chwasty przy kołach | mocna przesłanka długotrwałości |
| obrys brudu na nawierzchni | miejsce długo zajęte |
| śmieci uwięzione pod autem | brak przemieszczania pojazdu |

Opis:

> Wokół pojazdu widoczne są nagromadzone liście, pył i zabrudzenia przy kołach oraz pod progiem. Taki układ zanieczyszczeń może wskazywać na brak regularnego przestawiania pojazdu i długotrwałe zajmowanie tego samego miejsca.

### 11.4. Karoseria, szyby i uszczelki

| Miejsce postoju | Typowe ślady |
|---|---|
| pod drzewami | liście, pyłki, żywica, ptasie odchody, zielony nalot |
| przy ruchliwej ulicy | pył drogowy, ciemny osad, brud na tylnej części auta |
| przy budowie | jasny pył mineralny/cementowy |
| w cieniu i wilgoci | mech/glony przy uszczelkach |
| pod gołym niebem | płowienie lakieru, matowienie, spękania gum |
| pod balkonami/elewacją | zacieki, lokalne zabrudzenia, odpady |

Opis:

> Na karoserii i szybach widoczne są nagromadzone osady właściwe dla miejsca postoju. Brak śladów bieżącego mycia oraz charakterystyczne nagromadzenie zanieczyszczeń mogą wskazywać na dłuższy brak eksploatacji pojazdu.

### 11.5. Tablice rejestracyjne

| Objaw | Siła przesłanki |
|---|---|
| brak jednej tablicy | mocna |
| brak obu tablic | bardzo mocna |
| pusta ramka | mocna |
| tablice nieczytelne od brudu | średnia/mocna |
| tablice zamaskowane publicznie, pełne w urzędowym ZIP | zalecane |

Opis:

> Pojazd nie posiada [przedniej/tylnej/obu] tablic rejestracyjnych albo posiada pustą ramkę po tablicy. Jest to istotna przesłanka do oceny podstaw z art. 50a p.r.d.

---

## 12. Rekomendowana skala siły sprawy

| Poziom | Warunki |
|---|---|
| niska | jedno zdjęcie lub sam kandydat z mapy |
| średnia | zdjęcie terenowe + jeden/dwa objawy nieużytkowania |
| wysoka | zdjęcia terenowe + ortofoto albo kilka silnych objawów |
| bardzo wysoka | brak tablic / mocne objawy techniczne / kilka obserwacji w czasie / utrudnienie ruchu |

Przykład logiki:

```text
+2 brak tablicy
+2 zdjęcie terenowe z EXIF/GPS
+2 widoczna korozja tarcz z rowkami/wżerami
+1 zabrudzenia przy kołach
+1 płaska opona
+2 widoczność w wielu latach ortofoto
+2 kilka obserwacji terenowych w odstępie 30+ dni
+1 utrudnianie wjazdu/ruchu/parkowania
```

---

## 13. Wysyłka zgłoszeń — model bez spamu

Aplikacja nie powinna automatycznie masowo wysyłać zgłoszeń w imieniu wszystkich. Bezpieczniejszy i bardziej wiarygodny model:

1. użytkownik przygotowuje własny raport,
2. podaje swoje dane,
3. aplikacja generuje wniosek i paczkę dowodową,
4. użytkownik sam wysyła zgłoszenie,
5. aplikacja pozwala oznaczyć status „wysłano” i dodać odpowiedź urzędu.

WreckScanner powinien być narzędziem do składania uporządkowanej dokumentacji, a nie centralnym automatem do spamowania skrzynek urzędów.

---

## 14. Odpowiedzi urzędów i dalszy workflow

Dodać moduł „Odpowiedź urzędu”:

- upload PDF/JPG/tekst odpowiedzi,
- data odpowiedzi,
- numer sprawy,
- wynik: oględziny / wezwanie właściciela / brak podstaw / usunięto / przekazano innemu organowi,
- następny termin działania,
- możliwość wygenerowania ponaglenia/skargi po 30 dniach.

Statusy po odpowiedzi:

```text
odpowiedź otrzymana
wezwanie właściciela
usunięty
odmowa
przekazano do innego organu
brak reakcji po 30 dniach
ponaglenie wygenerowane
```

---

## 15. Prywatność i dane osobowe

Wersja publiczna:

- maskować tablice,
- usuwać EXIF z publicznych podglądów,
- nie pokazywać danych zgłaszającego,
- rozważyć lekkie zaokrąglenie lokalizacji dla spraw wrażliwych,
- nie publikować VIN ani danych z wnętrza pojazdu.

Wersja urzędowa:

- pełne zdjęcia,
- pełne tablice,
- EXIF,
- hash plików,
- dane zgłaszającego,
- pełna lokalizacja.

W UI należy jasno pokazać różnicę:

```text
Pakiet publiczny: bez danych osobowych, tablice zamaskowane, EXIF usunięty.
Pakiet urzędowy: pełne dane i oryginalne zdjęcia, przeznaczone do wysłania przez zgłaszającego do właściwego organu.
```

---

## 16. Mapa i wydajność

### 16.1. Leaflet / kafle

Sprawdzić konfigurację warstw mapowych:

- `minZoom`,
- `maxZoom`,
- `maxNativeZoom`,
- `bounds`,
- `noWrap`,
- `errorTileUrl`.

`maxNativeZoom` jest opcją warstwy `L.TileLayer`, a nie samej mapy.

Źródło: https://leafletjs.com/reference.html

Przykład:

```js
L.tileLayer(url, {
  minZoom: 12,
  maxZoom: 20,
  maxNativeZoom: 18,
  bounds: wroclawBounds,
  noWrap: true,
  errorTileUrl: "/static/blank-tile.png"
});
```

Jeżeli 4xx w Cloudflare pochodzą z kafli, sprawdzić, czy frontend nie żąda nieistniejących kafli poza zakresem danych.

### 16.2. Cache

Dla statycznych raportów i zasobów ustawić cache.

Cloudflare Cache Rules pozwalają kontrolować, co i jak jest cache’owane.

Źródło: https://developers.cloudflare.com/cache/how-to/cache-rules/

Rekomendowane nagłówki dla plików statycznych z hashem/timestampem:

```http
Cache-Control: public, max-age=31536000, immutable
```

Dla raportu HTML:

```http
Cache-Control: public, max-age=300, stale-while-revalidate=86400
```

Przykładowe reguły Cloudflare:

```text
If URI Path contains "/zidentyfikowane_wraki/"
Then: Eligible for cache, Edge TTL 1 month, Browser TTL 1 month

If URI Path contains "/api/field-photos/" and URI Path contains "/thumbnail"
Then: Eligible for cache, Edge TTL 1 month, Browser TTL 1 month
```

Nie cache’ować publicznie prywatnych oryginałów ani paczek urzędowych bez kontroli dostępu.

---

## 17. Raspberry Pi 5 jako origin

Ponieważ serwer działa na Raspberry Pi 5, aplikacja powinna minimalizować ciężkie operacje synchroniczne.

Na Pi 5 można spokojnie robić:

- serwowanie aplikacji,
- generowanie raportów,
- odczyt EXIF,
- hash SHA-256,
- miniatury w kolejce,
- lekkie OCR w kolejce,
- zapytania do UFG API,
- składanie ZIP.

Unikać:

- ciężkich modeli vision lokalnie,
- masowego OCR bez kolejki,
- generowania ZIP/PDF w request-response,
- serwowania ciężkich zdjęć bez cache,
- trzymania wszystkiego na microSD bez backupu.

Rekomendacje:

- SSD/NVMe,
- kolejka zadań,
- limit równoległych analiz,
- cache po stronie Cloudflare,
- generowanie ZIP jako zadanie w tle,
- miniatury i WebP dla publicznych podglądów.

---

## 18. AI / OCR / UFG — kolejność wdrożenia

### Etap 1: bezpieczne i tanie

- lokalny odczyt EXIF,
- hash plików,
- ręczne zaznaczenie tablicy,
- ręczny opis z checklisty,
- raport HTML/PDF/ZIP.

### Etap 2: półautomatyka

- OCR tablic jako sugestia,
- AI poprawiające opis na język urzędowy,
- AI tworzące listę braków w dokumentacji,
- ręczne zatwierdzenie przez użytkownika.

### Etap 3: integracja UFG, jeśli będzie legalne API

- zapytanie tylko po zatwierdzeniu tablicy,
- cache wyniku dla tablica + data,
- wynik jako pole pomocnicze,
- nie używać statusu OC jako jedynej podstawy do wniosku o usunięcie.

---

## 19. Minimalne kryteria „gotowe do wniosku”

Wniosek formalny powinien być oznaczony jako gotowy dopiero, gdy ma:

- dokładną lokalizację,
- co najmniej jedno zdjęcie całego pojazdu,
- co najmniej jedno zdjęcie kontekstu miejsca,
- zdjęcie tablic albo informację o ich braku,
- opis co najmniej jednej przesłanki nieużytkowania,
- datę obserwacji,
- dane zgłaszającego,
- wskazanego adresata.

Dla silnego wniosku dodatkowo:

- EXIF/GPS,
- hash zdjęć,
- zdjęcia detali technicznych,
- oś czasu ortofoto,
- kolejna obserwacja po czasie,
- informacja o utrudnianiu ruchu/parkowania/wjazdu.

---

## 20. Lista konkretnych zadań dla agenta w VS Code

### Priorytet A — raport i zdjęcia

- [ ] Nie usuwać EXIF z oryginałów w pakiecie urzędowym.
- [ ] Zapisywać oryginały w `zdjecia_oryginalne/`.
- [ ] Generować podglądy do HTML w `zdjecia_podgladowe/`.
- [ ] Liczyć SHA-256 dla każdego zdjęcia.
- [ ] Dodać `photos_metadata.json`.
- [ ] Dodać zdjęcia terenowe do `raport.html`.
- [ ] Wyświetlić treść `zgloszenie.txt` w `raport.html`.
- [ ] Poprawić pierwszą kartę raportu.
- [ ] Dodać oś czasu łączącą ortofoto i zdjęcia terenowe.

### Priorytet B — objawy nieużytkowania

- [ ] Dodać checklistę objawów.
- [ ] Dodać kategorię „hamulce / tarcze”.
- [ ] Dodać kategorię „opony”.
- [ ] Dodać kategorię „brud wokół pojazdu”.
- [ ] Dodać kategorię „karoseria i szyby”.
- [ ] Dodać kategorię „tablice”.
- [ ] Dodać ocenę siły przesłanki.
- [ ] Dodać ostrożne sformułowania do raportu.

### Priorytet C — wnioski i statusy

- [ ] Dodać tryb `testowy` i `urzędowy` generatora.
- [ ] W trybie urzędowym wymagać danych zgłaszającego.
- [ ] Generować tytuł wniosku formalnego.
- [ ] Dodać prośbę o pisemną informację o sposobie rozpoznania.
- [ ] Dodać statusy sprawy.
- [ ] Dodać możliwość wgrania odpowiedzi urzędu.

### Priorytet D — wydajność

- [ ] Sprawdzić top 4xx paths w Cloudflare.
- [ ] Dodać `errorTileUrl` dla warstw Leaflet.
- [ ] Sprawdzić `maxNativeZoom`, `bounds`, `noWrap`.
- [ ] Ustawić `Cache-Control` dla statycznych zdjęć/JSON/HTML.
- [ ] Dodać Cloudflare Cache Rules.
- [ ] Przenieść generowanie ZIP/PDF do kolejki.
- [ ] Zoptymalizować PNG/JPG/WebP.

---

## 21. Najważniejsza zasada końcowa

WreckScanner nie powinien udawać organu ani diagnosty. Powinien robić coś bardziej wartościowego:

> Porządkować dowody, opisywać widoczne przesłanki, ułatwiać mieszkańcom przygotowanie kompletnego wniosku i śledzić, czy sprawa została rozpoznana.

To zwiększa wiarygodność projektu i zmniejsza ryzyko, że urząd potraktuje zgłoszenia jako spam albo emocjonalne donosy.
