# WreckScanner architecture diagnostics

Generated: `2026-06-09T16:45:11Z`

## Summary

| Metric | Value |
| --- | --- |
| source_files | 70 |
| python_files | 60 |
| javascript_files | 6 |
| html_css_files | 4 |

## Biggest files

| Path | Lines | Bytes |
| --- | --- | --- |
| web/app.js | 4591 | 190400 |
| web/styles.css | 3154 | 63155 |
| app/server.py | 1698 | 72025 |
| tests/test_server_contracts.py | 1404 | 64690 |
| web/i18n.js | 1246 | 79223 |
| core/wrecks.py | 1189 | 49563 |
| web/index.html | 1175 | 78069 |
| tests/test_frontend_contracts.py | 973 | 67594 |
| core/report_packages.py | 799 | 29519 |
| core/data_diagnostics.py | 711 | 26500 |
| app/map_downloads.py | 638 | 24000 |
| scripts/download_geoportal_wfs_geotiff.py | 635 | 23657 |
| core/reporter.py | 617 | 29290 |
| scripts/diagnose_architecture.py | 614 | 22931 |
| core/field_photos.py | 515 | 18560 |

## Longest functions

| Path | Function | Line | Lines | Kind |
| --- | --- | --- | --- | --- |
| app/server.py | do_POST | 1229 | 441 | python |
| tests/test_frontend_contracts.py | test_report_package_modal_and_admin_popup_action_exist | 89 | 268 | python |
| core/reporter.py | render_report | 359 | 235 | python |
| core/data_diagnostics.py | _audit_wrecks | 414 | 214 | python |
| app/map_downloads.py | apply_wfs_geotiff_replacements | 166 | 204 | python |
| tests/test_frontend_contracts.py | test_map_layer_toggles_and_manual_wreck_creation_exist | 686 | 192 | python |
| tests/test_frontend_contracts.py | test_admin_field_photo_upload_layer_exists | 505 | 180 | python |
| core/scoring.py | score_candidates | 112 | 162 | python |
| app/server.py | do_GET | 760 | 150 | python |
| core/data_diagnostics.py | _audit_field_photos | 258 | 133 | python |
| tests/test_server_contracts.py | test_wreck_photo_upload_allows_public_pending_and_admin_review | 869 | 132 | python |
| core/reporter.py | save_candidate_crops | 183 | 116 | python |
| core/wrecks.py | _render_record_html | 874 | 112 | python |
| core/wrecks.py | save_wreck_from_rank | 1077 | 112 | python |
| tests/test_server_contracts.py | test_field_photo_list_assets_are_public_and_delete_requires_admin | 1127 | 107 | python |
| web/app.js | runAll | 4372 | 106 | javascript |
| scripts/diagnose_architecture.py | format_markdown | 483 | 100 | python |
| scripts/download_geoportal_wfs_geotiff.py | main | 540 | 91 | python |
| app/analyze.py | main | 77 | 90 | python |
| tests/test_frontend_contracts.py | test_scan_area_controls_context_menu_and_crosshair_contract | 879 | 90 | python |

## Dependency cycles

_None found._

## Group dependencies

| From | To | Count |
| --- | --- | --- |
| app | app | 7 |
| app | core | 92 |
| app | scripts | 11 |
| core | core | 155 |
| scripts | core | 19 |

## HTTP endpoints and route strings

| Endpoint | Source | Line | Method hint |
| --- | --- | --- | --- |
| / | app/map_downloads.py | 435 | HTTP |
| / | app/server.py | 710 | HTTP |
| / | app/server.py | 755 | HEAD |
| / | app/server.py | 906 | GET |
| / | app/server.py | 935 | DELETE |
| / | app/server.py | 1000 | PATCH |
| / | app/server.py | 1528 | POST |
| / | core/data_diagnostics.py | 49 | HTTP |
| / | core/field_photos.py | 274 | HTTP |
| / | core/photo_privacy.py | 27 | HTTP |
| / | core/photo_retention.py | 58 | HTTP |
| / | core/privacy_requests.py | 46 | HTTP |
| / | core/report_packages.py | 601 | HTTP |
| / | core/wreck_photo_transfers.py | 100 | HTTP |
| / | core/wrecks.py | 56 | HTTP |
| / | scripts/diagnose_architecture.py | 264 | HTTP |
| / | scripts/download_geoportal_krajowy.py | 119 | HTTP |
| / | scripts/download_geoportal_wfs_geotiff.py | 534 | HTTP |
| / | scripts/retire_private_originals.py | 55 | HTTP |
| / | tests/test_data_diagnostics.py | 91 | HTTP |
| / | tests/test_photo_retention.py | 210 | HTTP |
| / | tests/test_wrecks_contracts.py | 111 | HTTP |
| /api/ | scripts/diagnose_architecture.py | 264 | HTTP |
| /api/admin/geotiff-cache | app/server.py | 793 | GET |
| /api/admin/geotiff-cache | web/config.js | 26 | JS |
| /api/admin/geotiff-cache/ | app/server.py | 932 | DELETE |
| /api/admin/geotiff-cache/ | tests/test_server_contracts.py | 283 | HTTP |
| /api/admin/geotiff-cache/../secret.tif | tests/test_server_contracts.py | 304 | HTTP |
| /api/admin/geotiff-cache/79810_131924_M-33-35-A-d-3-3.tif | tests/test_server_contracts.py | 268 | HTTP |
| /api/admin/login | app/server.py | 1231 | POST |
| /api/admin/login | tests/test_server_contracts.py | 345 | HTTP |
| /api/admin/login | web/config.js | 22 | JS |
| /api/admin/logout | app/server.py | 1234 | POST |
| /api/admin/logout | web/config.js | 23 | JS |
| /api/admin/photo-retention | app/server.py | 808 | GET |
| /api/admin/photo-retention | tests/test_server_contracts.py | 590 | HTTP |
| /api/admin/photo-retention | web/config.js | 28 | JS |
| /api/admin/photo-retention/run | app/server.py | 1245 | POST |
| /api/admin/photo-retention/run | tests/test_server_contracts.py | 593 | HTTP |
| /api/admin/photos | app/server.py | 787 | GET |
| /api/admin/photos | web/config.js | 24 | JS |
| /api/admin/photos/field/ | core/field_photos.py | 343 | HTTP |
| /api/admin/photos/field/ | tests/test_server_contracts.py | 1175 | HTTP |
| /api/admin/photos/wreck/ | core/wrecks.py | 514 | HTTP |
| /api/admin/photos/wreck/wreck_51100000_17200000/ | tests/test_server_contracts.py | 934 | HTTP |
| /api/admin/photos?status=all&scope=field&ids= | tests/test_server_contracts.py | 1186 | HTTP |
| /api/admin/photos?status=all&scope=field&ids=missing-photo | tests/test_server_contracts.py | 1190 | HTTP |
| /api/admin/privacy-requests | app/server.py | 796 | GET |
| /api/admin/privacy-requests | tests/test_server_contracts.py | 538 | HTTP |
| /api/admin/privacy-requests | web/config.js | 27 | JS |
| /api/admin/privacy-requests/ | tests/test_server_contracts.py | 550 | HTTP |
| /api/admin/privacy-requests?status=in_progress | tests/test_server_contracts.py | 560 | HTTP |
| /api/admin/status | app/server.py | 784 | GET |
| /api/admin/status | web/config.js | 21 | JS |
| /api/admin/wrecks | app/server.py | 790 | GET |
| /api/admin/wrecks | web/config.js | 25 | JS |
| /api/analyze | app/server.py | 1537 | POST |
| /api/analyze | tests/test_server_contracts.py | 755 | HTTP |
| /api/analyze | web/config.js | 15 | JS |
| /api/cadastral/identify | app/server.py | 819 | GET |

## Risky patterns

### broad_excepts

| Path | Line | Detail |
| --- | --- | --- |
| app/map_downloads.py | 392 | Exception |
| app/map_downloads.py | 196 | Exception |
| app/map_downloads.py | 326 | Exception |
| app/map_downloads.py | 474 | Exception |
| app/server.py | 170 | Exception |
| app/server.py | 566 | Exception |
| app/server.py | 679 | Exception |
| app/server.py | 705 | Exception |
| app/server.py | 1178 | Exception |
| app/server.py | 1199 | Exception |
| app/server.py | 199 | Exception |
| app/server.py | 752 | Exception |
| app/server.py | 778 | Exception |
| app/server.py | 829 | Exception |
| app/server.py | 853 | Exception |
| app/server.py | 866 | Exception |
| app/server.py | 884 | Exception |
| app/server.py | 900 | Exception |
| app/server.py | 928 | Exception |
| app/server.py | 945 | Exception |
| app/server.py | 963 | Exception |
| app/server.py | 988 | Exception |
| app/server.py | 1015 | Exception |
| app/server.py | 1045 | Exception |
| app/server.py | 1064 | Exception |
| app/server.py | 1077 | Exception |
| app/server.py | 1123 | Exception |
| app/server.py | 1242 | Exception |
| app/server.py | 1255 | Exception |
| app/server.py | 1284 | Exception |

### shell_true

_None found._

### dynamic_code

_None found._

### pickle_usage

_None found._

### print_calls

| Path | Line | Detail |
| --- | --- | --- |
| app/analyze.py | 96 |  |
| app/analyze.py | 111 |  |
| app/analyze.py | 119 |  |
| app/analyze.py | 166 |  |
| app/analyze.py | 91 |  |
| app/analyze.py | 128 |  |
| app/analyze.py | 116 |  |
| app/analyze.py | 134 |  |
| app/map_downloads.py | 620 |  |
| app/map_downloads.py | 601 |  |
| app/server.py | 1686 |  |
| app/server.py | 1687 |  |
| app/server.py | 1689 |  |
| app/server.py | 1693 |  |
| app/server.py | 1201 |  |
| app/server.py | 1677 |  |
| app/server.py | 1682 |  |
| app/server.py | 1683 |  |
| app/server.py | 200 |  |
| app/server.py | 1434 |  |
| app/server.py | 1578 |  |
| app/wms_cache.py | 75 |  |
| app/wms_cache.py | 120 |  |
| core/detection.py | 60 |  |
| core/enhancement.py | 59 |  |
| core/enhancement.py | 56 |  |
| core/enhancement.py | 65 |  |
| core/scoring.py | 121 |  |
| core/vision.py | 144 |  |
| core/vision.py | 148 |  |

### console_calls

| Path | Line | Detail |
| --- | --- | --- |
| web/app.js | 62 | console.warn('Nie udało się odczytać ustawienia warstwy działek.', err); |
| web/app.js | 263 | console.warn('Nie udało się zapisać ustawienia warstwy działek.', err); |
| web/app.js | 403 | console.warn('Nie udało się pobrać warstwy nawierzchni.', err); |
| web/map_helpers.js | 38 | console.warn('Nie udało się odczytać zapisanej pozycji mapy.', err); |
| web/map_helpers.js | 52 | console.warn('Nie udało się zapisać pozycji mapy.', err); |

## Tool availability

| Command | Available | Version |
| --- | --- | --- |
| python | True | 3.13.5 |
| git | True | git version 2.47.3 |
| pytest | True | pytest 9.0.3 |
| ruff | True | ruff 0.15.16 |
| bandit | True | bandit 1.9.4 |
| pip-audit | True | pip-audit 2.10.0 |
| radon | True | 6.0.1 |
| vulture | True | vulture 2.16 |
| node | False |  |
| npm | False |  |

## Last analysis run log

| Field | Value |
| --- | --- |
| path | analiza/run_log.json |
| generated_at | 2026-06-09T15:39:52Z |
| status | ok |
| candidate_count | 10 |
| image_count | 6 |
| analysis_seconds | 42.947 |
