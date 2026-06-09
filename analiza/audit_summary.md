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

- Tests: 145 passed, 6 rasterio warnings.
- Coverage: 59% total.
- Ruff: 0 findings with the expanded rule set.
- Bandit: 0 findings. The XML parser, SHA1, subprocess, and false-positive sentinel findings are resolved.
- pip-audit: 0 known vulnerabilities after upgrading `.venv-audit` pip to 26.1.2.
- Radon: average complexity B; several D/F hotspots.
- Vulture: no high-confidence dead code at `--min-confidence 80`.
- Data diagnostics: OK, 0 errors/warnings/info. It reports 149 old field-photo records without `issue_type`; no data migration was performed.
- Frontend: blocked in this environment because `node`, `npm`, and `npx` are not installed.

## Highest Priority Fixes

1. Continue reducing risk in `app/server.py` before broad refactors.
   `Handler.do_POST` is Radon F(108), `do_GET` is F(48), and the file returns many `str(e)` payloads to clients. First pass should add `request_id`, central error helpers, and logging in small steps. Then extract admin/public/analysis route handlers behind the existing behavior.

2. Add tests around low-coverage critical paths.
   Coverage is weak in `app/map_downloads.py` (13%), `core/vision.py` (30%), `core/scoring.py` (35%), `core/detection.py` (0%), and `app/analyze.py` (0%). Prefer lightweight tests with fake models/images and no GPU dependency.

3. Stage the frontend split after Node is available.
   `web/app.js` is 4591 lines. Keep behavior stable and extract small modules in this order: API client, map layers, scan flow, wreck markers, admin panel, field photos, settings panel. Run `npm install` and `npm run lint:web` once Node/npm exist on the machine.

4. Convert diagnostics output from `print` to logging over time.
   Architecture diagnostics found repeated `print` calls in server/analyze/cache/scoring/vision paths and broad `except Exception` handlers. Convert user-facing CLI prints separately from long-running server logs; use structured context and avoid leaking raw exception strings to public API clients.

## Notes

- No dependency cycles were detected.
- No `shell=True`, `eval`, `exec`, or `pickle` usage was detected by the architecture scanner.
- The generated HTML coverage directory contains its own `.gitignore`; top-level `.gitignore` now also excludes `.coverage`, `.venv-audit/`, and `node_modules/`.
