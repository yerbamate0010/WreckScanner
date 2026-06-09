# WreckScanner audit summary

Generated on: 2026-06-09
Branch: `audit/diagnostics-and-quality`

## Scope

- Added local diagnostic configuration in `pyproject.toml`.
- Added `scripts/diagnose_architecture.py` for read-only architecture diagnostics.
- Added minimal JS diagnostics scaffolding: `package.json` and `eslint.config.mjs`.
- Created and used local `.venv-audit/`; no global packages, secrets, deploy config, data migrations, or service restarts.

## Reports

- `analiza/pytest.txt`
- `analiza/coverage.txt`
- `analiza/htmlcov/index.html`
- `analiza/ruff.txt`
- `analiza/bandit.json`
- `analiza/pip-audit.json`
- `analiza/radon-complexity.txt`
- `analiza/radon-maintainability.txt`
- `analiza/vulture.txt`
- `analiza/data_diagnostics.json`
- `analiza/architecture_diagnostics.md`
- `analiza/architecture_diagnostics.json`
- `analiza/node-check.txt`
- `analiza/npm-install.txt`
- `analiza/eslint.txt`

## Results

- Tests: 144 passed, 6 rasterio warnings.
- Coverage: 59% total.
- Ruff: 26 findings with the expanded rule set.
- Bandit: 19 findings: 6 high, 2 medium, 11 low.
- pip-audit: 0 known vulnerabilities after upgrading `.venv-audit` pip to 26.1.2.
- Radon: average complexity B; several D/F hotspots.
- Vulture: no high-confidence dead code at `--min-confidence 80`.
- Data diagnostics: OK, 0 errors/warnings/info. It reports 149 old field-photo records without `issue_type`; no data migration was performed.
- Frontend: blocked in this environment because `node`, `npm`, and `npx` are not installed.

## Highest Priority Fixes

1. Fix the likely closure bug in `app/map_downloads.py:281`.
   Ruff B023 reports that `on_download_progress` closes over loop variables `selected`, `tif_path`, and `year`. This can produce wrong progress messages/state if callbacks fire after the loop advances. Bind values as defaults or move callback creation into a helper factory, then add a focused test around WFS GeoTIFF progress reporting.

2. Reduce risk in `app/server.py` before broad refactors.
   `Handler.do_POST` is Radon F(108), `do_GET` is F(48), and the file returns many `str(e)` payloads to clients. First pass should add `request_id`, central error helpers, and logging in small steps. Then extract admin/public/analysis route handlers behind the existing behavior.

3. Replace XML parsing in Geoportal scripts.
   Bandit B314/B405 flags `xml.etree.ElementTree.fromstring` in `scripts/download_geoportal_krajowy.py:52` and `scripts/download_geoportal_wfs_geotiff.py:194`. Use `defusedxml.ElementTree` or a tightly constrained parser path for untrusted upstream WFS/WMS responses.

4. Review SHA1 use flagged by Bandit B324.
   Findings are in `core/field_photos.py:219`, `core/privacy_requests.py:27`, `core/report_packages.py:208`, and `core/wrecks.py:114`, `core/wrecks.py:119`, `core/wrecks.py:644`. If these are only non-security IDs, mark with `usedforsecurity=False`; if any value gates access, migrate to SHA-256 or token-safe randomness.

5. Add tests around low-coverage critical paths.
   Coverage is weak in `app/map_downloads.py` (13%), `core/vision.py` (30%), `core/scoring.py` (35%), `core/detection.py` (0%), and `app/analyze.py` (0%). Prefer lightweight tests with fake models/images and no GPU dependency.

6. Stage the frontend split after Node is available.
   `web/app.js` is 4591 lines. Keep behavior stable and extract small modules in this order: API client, map layers, scan flow, wreck markers, admin panel, field photos, settings panel. Run `npm install` and `npm run lint:web` once Node/npm exist on the machine.

7. Convert diagnostics output from `print` to logging over time.
   Architecture diagnostics found repeated `print` calls in server/analyze/cache/scoring/vision paths and broad `except Exception` handlers. Convert user-facing CLI prints separately from long-running server logs; use structured context and avoid leaking raw exception strings to public API clients.

## Notes

- No dependency cycles were detected.
- No `shell=True`, `eval`, `exec`, or `pickle` usage was detected by the architecture scanner.
- The generated HTML coverage directory contains its own `.gitignore`; top-level `.gitignore` now also excludes `.coverage`, `.venv-audit/`, and `node_modules/`.
