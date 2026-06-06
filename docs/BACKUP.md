# Backup danych

WreckScanner nie używa osobnej bazy SQL. Dane aplikacji są plikami JSON, zdjęciami i pakietami raportów w katalogach projektu.

## Lokalizacja lokalnego backupu

Aktualny lokalny backup restic jest w katalogu:

```text
/home/test/Desktop/WreckScanner/.backups/wreckscanner-restic
```

Katalog `.backups/` jest ignorowany przez Git i nie powinien trafić na remote.

## Hasło backupu

Plik hasła restic jest w katalogu głównym projektu:

```text
/home/test/Desktop/WreckScanner/.restic_password
```

To ukryty plik, więc zwykłe `ls` go nie pokazuje. Sprawdź go tak:

```bash
ls -la /home/test/Desktop/WreckScanner/.restic_password
```

Obecnie `.restic_password` jest kopią `.admin_password`, bo świadomie używamy tego samego hasła do panelu administratora i lokalnego backupu. Oba pliki są ignorowane przez Git:

```text
.admin_password
.restic_password
```

Bez `.restic_password` nie da się odtworzyć backupu. Trzymaj kopię hasła poza jedyną kopią repozytorium restic.

## Zakres backupu

Domyślny backup obejmuje:

- `zidentyfikowane_wraki/`
- `zdjecia_terenowe/`
- `prywatne_zdjecia/`
- `prywatne_zgloszenia/`
- `zgloszenia_prywatnosci/`, jeśli istnieje
- `settings.json`, jeśli istnieje
- `analiza/data_diagnostics.json`

Backup nie obejmuje:

- `.venv/`
- `.cache/`
- `.backups/`
- `dane_dla_AI/`
- cache WMS/GeoTIFF
- luźnych raportów ZIP/PDF w katalogu projektu
- `.admin_password`, chyba że świadomie użyjesz `--include-admin-password`

## Komendy

Snapshot:

```bash
cd /home/test/Desktop/WreckScanner
./.venv/bin/python scripts/backup_data.py run \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password
```

Lista snapshotów:

```bash
./.venv/bin/python scripts/backup_data.py snapshots \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password
```

Kontrola repozytorium:

```bash
./.venv/bin/python scripts/backup_data.py check \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password
```

Próbne odtworzenie do katalogu tymczasowego:

```bash
mkdir -p /tmp/wreckscanner-restore-test
RESTIC_REPOSITORY=/home/test/Desktop/WreckScanner/.backups/wreckscanner-restic \
RESTIC_PASSWORD_FILE=/home/test/Desktop/WreckScanner/.restic_password \
restic restore latest --target /tmp/wreckscanner-restore-test
```

Po odtworzeniu katalog testowy będzie zawierał ścieżki z backupu pod `/tmp/wreckscanner-restore-test/home/test/Desktop/WreckScanner/...`.

