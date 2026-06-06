# Prywatnosc Zdjec - Lista Zmian

## Co Zostalo Zmienione

- `core/photo_privacy.py` obsluguje prywatne oryginaly, publiczne kopie bez EXIF, miniatury z publicznej kopii oraz trwale wypalane redakcje.
- `core/field_photos.py` zapisuje nowe zdjecia terenowe jako `pending` i publicznie zwraca tylko `public_image` oraz `public_thumb` po zatwierdzeniu.
- `core/wrecks.py` robi to samo dla zdjec dopietych do teczek pojazdow i whitelistuje publiczne pliki w `/zidentyfikowane_wraki/...`.
- `app/server.py` rozdziela publiczne assety, admin-only oryginaly, adminowa kolejke przegladu oraz publiczne strony `/privacy` i `/report`.
- `web/app.js` dodaje kolejke przegladu zdjec, redakcje jako wielokaty z rotacja, publiczne pobieranie zanonimizowanej kopii i rozdzial raportow public/admin.
- `core/report_packages.py` ma dwa flow raportow: admin-only pelny pakiet oraz publiczny clean package z zatwierdzonych kopii i zdjec uzytkownika bez EXIF.
- `core/photo_retention.py` i `scripts/retire_private_originals.py` obsluguja retencje prywatnych oryginalow po 180 dniach od ostatniej weryfikacji.
- `web/privacy.html` opisuje kopie publiczne, retencje oraz wariant usuniecia albo zastapienia oryginalu wersja zanonimizowana.

## Co Zniknelo Z Publicznego Dostepu

- Oryginaly zdjec.
- `record.json`.
- Stare pola i linki `original_url`, `original_path`, `thumbnail_url`, `original_file`, `thumb_file`.
- Stare publiczne pliki typu `original.jpg` i `thumb.jpg`.
- Zdjecia ze statusem `pending` albo `rejected`.
- ZIP/PDF pelnych pakietow raportowych z danymi zglaszajacego.
- Prywatne katalogi oryginalow i raportow.

## Co Jest Publiczne

- Zatwierdzone publiczne kopie zdjec jako `public_image`.
- Zatwierdzone publiczne miniatury jako `public_thumb`.
- Publiczne strony teczek pojazdow, ale tylko z zatwierdzonymi zdjeciami.
- Publiczny clean report generowany z zatwierdzonych publicznych zdjec i zdjec uzytkownika po usunieciu EXIF.

## Retencja Oryginalow

- Domyslny limit retencji prywatnych oryginalow to `PRIVATE_ORIGINAL_RETENTION_DAYS = 180`.
- Zatwierdzone zdjecie po terminie retencji dostaje prywatny oryginal zastapiony plikiem `retained_public.jpg`, czyli ta sama zanonimizowana kopia JPEG bez EXIF.
- Odrzucone zdjecie po terminie retencji ma prywatny oryginal usuwany i rekord oznaczany `private_original_deleted_at`.
- Skrypt domyslnie dziala jako dry-run: `.venv/bin/python scripts/retire_private_originals.py`.
- Zapis zmian wymaga jawnej flagi: `.venv/bin/python scripts/retire_private_originals.py --apply`.

## Statusy Zdjec

- `pending`: nie widac publicznie, brak publicznej publikacji zdjecia.
- `approved`: backend generuje publiczna kopie bez EXIF i miniatury, frontend przywraca widok pinezek/zdjec.
- `rejected`: publiczne kopie sa usuwane albo odcinane od publicznego API.
