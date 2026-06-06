# Uruchamianie serwera

## Lokalnie
```bash
cd /home/test/Desktop/WreckScanner
source .venv/bin/activate
./.venv/bin/python server.py
```

Otwórz w przeglądarce:

```text
http://localhost:8000
```

## Restart serwera aplikacji
Jeśli serwer działa z autostartu, nie uruchamiaj drugiej kopii ręcznie. Wystarczy ubić obecny proces `server.py`; autostart powinien podnieść świeży kod po kilku sekundach.

1. Znajdź proces serwera:

```bash
pgrep -af "/home/test/Desktop/WreckScanner/server.py"
```

2. Ubij tylko proces aplikacji, czyli linię z `.../.venv/bin/python .../server.py`:

```bash
kill <PID>
```

Nie ubijaj procesu `codex app-server`, jeśli pojawi się na liście.

3. Sprawdź, czy serwer wstał ponownie:

```bash
sleep 3
pgrep -af "/home/test/Desktop/WreckScanner/server.py"
curl -I http://localhost:8000
```

Jeśli autostart nie podniesie procesu, uruchom go ręcznie:

```bash
cd /home/test/Desktop/WreckScanner
source .venv/bin/activate
./.venv/bin/python server.py
```

## Dlaczego używać `.venv`
- Aplikacja powinna być uruchamiana w lokalnym wirtualnym środowisku `.venv`.
- Nie używaj `python3.11`, jeśli system nie ma takiego interpretera.
- Jeśli aktywujesz `.venv`, ścieżka `./.venv/bin/python` jest bezpieczna i działa zawsze.

## Panel administratora
- Hasło jest przechowywane lokalnie w pliku `.admin_password`.
- Plik `.admin_password` jest ignorowany przez git.
- Aby utworzyć nowe hasło:

```bash
openssl rand -base64 24 > .admin_password
chmod 600 .admin_password
```

## Diagnostyka lokalnej bazy danych
Przed większymi zmianami albo po pracy w terenie warto sprawdzić spójność katalogów `zdjecia_terenowe/` i `zidentyfikowane_wraki/`.

```bash
cd /home/test/Desktop/WreckScanner
./.venv/bin/python scripts/diagnose_data.py
```

Pełny raport JSON, wygodny do archiwizacji albo dalszej analizy:

```bash
./.venv/bin/python scripts/diagnose_data.py --output-json analiza/data_diagnostics.json
```

Szybsza kontrola bez otwierania obrazów:

```bash
./.venv/bin/python scripts/diagnose_data.py --no-image-check
```

## Backup lokalnej bazy danych
Backup używa `restic`. Aktualny lokalny backup jest w `.backups/wreckscanner-restic`, a plik hasła jest w ukrytym pliku `.restic_password` w katalogu głównym projektu. Szczegóły są w [`BACKUP.md`](BACKUP.md).

1. Utwórz lokalny plik hasła restic i zapisz kopię hasła poza tym komputerem. Jeśli chcesz użyć tego samego hasła co panel administratora:

```bash
cd /home/test/Desktop/WreckScanner
cp .admin_password .restic_password
chmod 600 .restic_password
```

To ukryty plik. Sprawdzisz jego obecność tak:

```bash
ls -la .restic_password
```

2. Zainicjuj repozytorium backupu:

```bash
mkdir -p .backups/wreckscanner-restic
./.venv/bin/python scripts/backup_data.py init \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password
```

3. Wykonaj backup danych:

```bash
./.venv/bin/python scripts/backup_data.py run \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password
```

Skrypt najpierw uruchamia diagnostykę danych. Jeśli znajdzie `error`, nie wykona backupu.

4. Sprawdź repozytorium i snapshoty:

```bash
./.venv/bin/python scripts/backup_data.py check \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password

./.venv/bin/python scripts/backup_data.py snapshots \
  --repo .backups/wreckscanner-restic \
  --password-file .restic_password
```

5. Próbny restore do katalogu tymczasowego:

```bash
mkdir -p /tmp/wreckscanner-restore-test
RESTIC_REPOSITORY=/home/test/Desktop/WreckScanner/.backups/wreckscanner-restic \
RESTIC_PASSWORD_FILE=/home/test/Desktop/WreckScanner/.restic_password \
restic restore latest --target /tmp/wreckscanner-restore-test
```

Domyślny backup obejmuje `zidentyfikowane_wraki/`, `zdjecia_terenowe/`, `prywatne_zdjecia/`, `prywatne_zgloszenia/`, `zgloszenia_prywatnosci/`, `settings.json` jeśli istnieje oraz `analiza/data_diagnostics.json`. Plik `.admin_password` nie jest dołączany domyślnie; można go dodać flagą `--include-admin-password`. Pliku `.restic_password` nie trzymaj razem z jedyną kopią backupu.

## Chętnie po restarcie
1. Uruchom aplikację:

```bash
cd /home/test/Desktop/WreckScanner
source .venv/bin/activate
./.venv/bin/python server.py
```

2. Sprawdź status tunelu Cloudflare (jeśli jest skonfigurowany jako usługa):

```bash
systemctl status cloudflared --no-pager
curl -I http://localhost:8000
curl -I https://wreckscanner.pl
```

3. Restart tunelu:

```bash
sudo systemctl restart cloudflared
sudo systemctl status cloudflared --no-pager
```
