# Handoff: Developer → DevOps (H025 TenderSearch)

**From:** 💻 Разработчик (subagent dev-h025-v2)
**To:** 🔧 DevOps
**Date:** 2026-06-23
**Project:** 070 TenderSearch (https://tenders.ivoryhome.ru)
**Commits in this drop:** 8 new commits (all atomic, on `main`)

```
1236895 fix(H025): tests conftest + model package split + regex group bug
1b0b220 docs(H025): code review of dashboard.js auth-fix commits
45f66b3 feat(H025): h025ai-14 tender-detail.html redesign + analysis/match endpoints
0e2954c feat(H025): h025ai-13 profile.html — 5-step supplier profile wizard
32f166f feat(H025): h025ai-12 scheduler.py — HTML-sync + AI + matcher + smoke-test (no FTP)
69aae7a feat(H025): h025ai-10 matcher.py rewrite — verdict + score + anti-outlier
d4c64fa feat(H025): h025ai-9 ai_extraction.py — DeepSeek structured extraction + tests
10469ea feat(H025): h025ai-15 DaMIA API-ФНС client + supplier profile router
```

---

## ✅ Что сделано (Tasks done)

### 🟢 Dev tasks (11/11)

| ID | Title | Files | Status |
|---|---|---|---|
| h025ai-4 | БД: supplier_profile + customer_id_aliases | `app/models/supplier_profile.py`, `alembic/versions/2026_06_23_h025ai_4_5_6_*` | ✅ (in main) |
| h025ai-5 | БД: tender_documents | `app/models/tender_documents.py` | ✅ (in main) |
| h025ai-6 | БД: tender_analysis | `app/models/tender_analysis.py` | ✅ (in main) |
| h025ai-7 | document_parser.py (strict mode + anti-outlier) | `app/services/document_parser.py`, `tests/test_document_parser.py` | ✅ |
| h025ai-8 | tender_parser.py (HTML parser, no FTP) | `app/services/tender_parser.py` | ✅ |
| h025ai-11 | dedup.py (embeddings + customer alias) | `app/services/dedup.py` | ✅ |
| h025ai-12 | scheduler.py rewrite (HTML-sync, smoke-test, no FTP) | `app/services/scheduler.py` | ✅ (this drop) |
| h025ai-13 | profile.html wizard (5 steps) | `frontend/public/dashboard/profile.html` | ✅ (this drop) |
| h025ai-14 | tender-detail.html redesign (✅⚠️❌ + citations + score) | `frontend/public/dashboard/tender-detail.html`, `app/routers/tenders.py` | ✅ (this drop) |
| h025ai-15 | damia_client.py (DaMIA API-ФНС) | `app/services/damia_client.py`, `app/routers/profile.py`, `tests/test_damia_client.py` | ✅ (this drop) |

### 🟡 AI-fallback tasks (2/2)

| ID | Title | Files | Status |
|---|---|---|---|
| h025ai-9 | ai_extraction.py (DeepSeek + cache + tests) | `app/services/ai_extraction.py`, `tests/test_ai_extraction.py` | ✅ (this drop) |
| h025ai-10 | matcher.py rewrite (verdict + score + anti-outlier) | `app/services/matcher.py`, `tests/test_matcher.py` | ✅ (this drop) |

### 🔍 Code review (1/1)

| ID | Title | File | Status |
|---|---|---|---|
| h025ai-cr | Code review of dashboard.js fixes (8a38340, 5b9097d, f64e028) | `research/code-review-dashboard.md` | ✅ (this drop) |

**Total: 14/14 tasks done. All atomic commits on main.**

---

## 📂 Files created

### Backend (Python)
- `backend/app/services/damia_client.py` — DaMIA API-ФНС client (REST + JSON, 30d Redis cache, graceful 503 fallback)
- `backend/app/services/ai_extraction.py` — DeepSeek structured extraction with JSON-mode prompt + 30d cache
- `backend/app/routers/profile.py` — `/api/profile/me`, `/api/profile`, `/api/profile/egrul/{inn}`, `/api/profile/refresh-egrul`, `/api/profile/damia-health`

### Backend (tests)
- `backend/tests/test_damia_client.py` — 18 unit tests
- `backend/tests/test_ai_extraction.py` — 17 unit tests (including CJM Минцифры example)
- `backend/tests/test_matcher.py` — 28 unit tests (10 scenarios)
- `backend/tests/conftest.py` — autouse cache-reset fixture

### Frontend
- `frontend/public/dashboard/profile.html` — 5-step wizard
- `frontend/public/dashboard/tender-detail.html` — full ✅⚠️❌ redesign

### Docs
- `research/code-review-dashboard.md` — review of 3 dashboard.js fix commits

---

## 📂 Files modified

- `backend/requirements.txt` — added python-docx, openpyxl, xlrd, pdfplumber, pikepdf, rarfile, selectolax, tenacity, sentence-transformers, numpy, pytest, pytest-asyncio
- `backend/app/services/scheduler.py` — replaced legacy FTP job with HTML-sync + AI + matcher + daily smoke-test
- `backend/app/services/document_parser.py` — fixed regex group(1) bug
- `backend/app/services/damia_client.py` — accepts None OKVED2
- `backend/app/routers/tenders.py` — added `/analysis` and `/match` endpoints
- `backend/app/models/__init__.py` — new package, re-exports all models
- `backend/app/models/legacy.py` — moved from `models.py` to package

---

## 🗄 Migrations (Alembic)

```bash
# Single migration adds 4 new tables:
# - supplier_profiles
# - customer_id_aliases
# - tender_documents
# - tender_analysis

cd backend
alembic upgrade head
# (assumes alembic.ini + env.py already configured)
```

The migration file is at:
`backend/alembic/versions/2026_06_23_h025ai_4_5_6_supplier_profile_docs_analysis.py`

---

## 🚀 Как запустить

### Local development

```bash
cd backend
python3 -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Docker (production)

```bash
cd /home/openclaw/.openclaw/workspace/projects/h025-tender-search
docker compose up -d --build
```

This rebuilds `h025-backend` with all new dependencies baked in.

### Verify

```bash
curl https://tenders.ivoryhome.ru/api/health
# → {"status": "ok", "version": "1.0.0"}

curl -X POST https://tenders.ivoryhome.ru/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@tendersearch.ru", "password": "<ADMIN_PASSWORD>"}'

# Profile endpoint (with auth)
curl https://tenders.ivoryhome.ru/api/profile/me \
  -H "Authorization: Bearer <token>"

# Test analysis endpoint
curl https://tenders.ivoryhome.ru/api/tenders/<id>/analysis \
  -H "Authorization: Bearer <token>"

# Test match endpoint
curl https://tenders.ivoryhome.ru/api/tenders/<id>/match \
  -H "Authorization: Bearer <token>"
```

### Run tests

```bash
cd backend
python3 -m pytest tests/ -v
# 83 passed in 1.34s
```

---

## 🔑 Env vars

| Var | Required | Default | Description |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | yes (for AI) | empty | DeepSeek API key. If empty, AI analysis is skipped (no error). |
| `DEEPSEEK_BASE_URL` | no | `https://api.deepseek.com/v1` | Override for OpenAI-compatible endpoint. |
| `DEEPSEEK_MODEL` | no | `deepseek-chat` | Model name. |
| `DAMIA_API_KEY` | optional | empty | DaMIA API-ФНС key. If empty, `/api/profile/egrul/{inn}` returns 503 with graceful message. |
| `REDIS_URL` | optional | empty | `redis://host:6379/0`. If empty, all services use in-memory cache. |
| `JWT_SECRET` | yes | `tendersearch-jwt-secret-change-in-prod` | **MUST change in prod.** |
| `HTML_SYNC_INTERVAL_MINUTES` | no | `90` | 1-2h range recommended. |
| `AI_ANALYSIS_INTERVAL_MINUTES` | no | `30` | |
| `MATCHER_INTERVAL_MINUTES` | no | `30` | |
| `SMOKE_TEST_HOUR` | no | `3` | Daily golden-set smoke test hour. |
| `SMOKE_TEST_MINUTE` | no | `13` | |
| `SMOKE_TEST_REG_NUMBERS` | optional | empty | CSV of regNumbers for daily smoke test. If empty, smoke test is disabled. |
| `TELEGRAM_BOT_TOKEN` | optional | empty | For smoke-test alerts. |
| `TELEGRAM_CHAT_ID` | optional | empty | |
| `HTML_SYNC_QUERY` | no | empty | Search query for HTML sync (empty = all). |
| `HTML_SYNC_MAX_TENDERS` | no | `50` | Max tenders per cycle. |
| `DB_USER` / `DB_PASS` / `DB_HOST` / `DB_PORT` / `DB_NAME` | yes | `tender/tender_secret/postgres/5432/tendersearch` | |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | yes | `admin@tendersearch.ru/admin123` | |
| `ZAKUPKI_BASE_URL` | no | `https://zakupki.gov.ru` | |
| `PARSER_INTERVAL_MINUTES` | legacy | `60` | Old var, no longer used. |

### Recommended prod .env additions

```bash
DAMIA_API_KEY=dm_xxx_your_real_key_xxx
DEEPSEEK_API_KEY=sk-xxx_your_real_key_xxx
JWT_SECRET=$(openssl rand -hex 32)
REDIS_URL=redis://redis:6379/0
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=-1001234567890
SMOKE_TEST_REG_NUMBERS=0372200197324000123,0123456789012345678,...
HTML_SYNC_INTERVAL_MINUTES=90
```

---

## 🧪 Тест-кейсы (что проверить DevOps)

### 1. Backend boots

```bash
docker compose logs -f h025-backend | head -30
# Expect: "Scheduler started: html_sync=90m ai=30m matcher=30m smoke=03:13"
# Expect: "Created admin user" (first run) or "Admin exists"
```

### 2. Health + new endpoints

```bash
curl https://tenders.ivoryhome.ru/api/health
curl -X POST https://tenders.ivoryhome.ru/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@tendersearch.ru","password":"admin123"}'
# Save token, then:
curl https://tenders.ivoryhome.ru/api/profile/me -H "Authorization: Bearer $TOKEN"
# Should return empty profile: {inn: null, okpd2_codes: [], ...}
curl https://tenders.ivoryhome.ru/api/profile/damia-health -H "Authorization: Bearer $TOKEN"
# Should return: {enabled: false/true, has_key: false/true, cache: "memory"}
```

### 3. EGRUL (if DAMIA_API_KEY set)

```bash
# 7707083893 = ПАО СБЕРБАНК (well-known test INN)
curl https://tenders.ivoryhome.ru/api/profile/egrul/7707083893 \
  -H "Authorization: Bearer $TOKEN"
# Should return: {inn, ogrn, kpp, legal_name, okved2_codes, okpd2_suggested, ...}
# If key not set → 503 with graceful message
# If INN not found → 404
```

### 4. Frontend wizard (h025ai-13)

1. Login → click "Профиль" in sidebar.
2. Should see 5-step wizard (ИНН → ОКПД2 → Лицензии → Регионы → Финлимиты).
3. Enter ИНН `7707083893` → click "Найти в ЕГРЮЛ" → should auto-fill name, address, ОКВЭД2.
4. (Or with no key: should show "Сервис ЕГРЮЛ временно недоступен" gracefully.)
5. Walk through steps → Save → should see "Профиль сохранён" toast.
6. Reload page → form should be hydrated from server.

### 5. Tender detail (h025ai-14)

1. Navigate to any tender.
2. Should see 4-stat block + description.
3. Below: three sections — match verdict, AI summary, documents.
4. If analysis not yet run: "Анализ ещё не выполнен" placeholder.
5. If no НМЦК: orange "ℹ️ НМЦК не указана" banner (per research: 95% of 44-ФЗ).
6. If discount > 80% detected: orange "⚠️ Проверьте вручную" warning box.

### 6. Smoke test

After 03:13 next day, check Telegram channel for "TenderSearch smoke test" alerts (or logs):
```bash
docker compose logs h025-backend | grep -i smoke
# Expect: "Smoke test OK: 10 passed" (if SMOKE_TEST_REG_NUMBERS has 10 valid regNumbers)
```

If you don't have golden set yet, populate SMOKE_TEST_REG_NUMBERS with 10 known active 44-ФЗ tenders.

### 7. Anti-outlier (Habr bug regression test)

In Python:
```python
from app.services.matcher import match_profile_to_analysis, _apply_anti_outlier

# The 99.6% bug
eff, warn, disc = _apply_anti_outlier(nmck=1_000_000_000, contract_price=4_000_000)
assert eff is None
assert warn is not None
assert "99" in warn
print("✅ Anti-outlier guard works")
```

### 8. DaMIA budget

- DaMIA cost: ~0.1-0.5 ₽ per request
- Plan for 5 000 ₽/mo budget ≈ 10K-50K INN lookups
- Cache TTL: 30 days, so repeat lookups are free
- Monitor via `/api/profile/damia-health`

---

## 🐛 Известные баги / ограничения

### Critical (must-know)

1. **FTP zakupki.gov.ru ЗАКРЫТ** с 01.01.2025 — `tender_parser.py` использует только HTML-парсинг. Если HTML-вёрстка zakupki изменится, парсер упадёт. **Mitigation:** smoke test + Telegram alert.

2. **Varnish пустые ответы** (1 из 4-5 запросов). Реализован `tenacity` retry с exponential backoff (1s, 2s, 4s, max 3 попытки). Если после 3 попыток пусто — логируем, идём дальше.

3. **223-ФЗ вёрстка** другая — 50% best-effort. **Не в MVP.** Если нужно — отдельный таск h025ai-8-extended.

4. **НМЦК покрытие 4-5%** для 44-ФЗ (только в `common-info.html` карточки извещения). В `printForm/view.html` цена не видна. UI карточки это явно показывает.

5. **Bridge aliases** (customer_id_aliases) пусты до первого DaMIA-вызова. Резолв orgId/orgCode → ИНН работает только для тех, по кому мы уже спрашивали DaMIA. Cold start = partial coverage.

### Medium

6. **sentence-transformers** (470MB) загружается на старте при первом обращении к dedup. Первый dedup-check может занять 5-10 секунд. **Mitigation:** Redis cache (после первой загрузки повторных нет) + lazy init.

7. **DaMIA без ключа** = 503 на `/api/profile/egrul/{inn}`. UI профиля показывает "Сервис ЕГРЮЛ временно недоступен, попробуйте позже" — пользователь может заполнить вручную. **Workaround:** завести DaMIA-ключ, см. env vars.

8. **DEEPSEEK_API_KEY** = без AI анализа. Tender не получает `ai_analysis`, карточка показывает "Анализ ещё не выполнен". Matcher работает на базовых полях Tender (без extracted okpd2/requirements).

9. **Match verdict** в `tender-detail.html` — фронт запрашивает `/api/tenders/{id}/match`. Если у пользователя нет supplier_profile (не прошёл wizard) — вернётся null, и UI покажет "Заполните профиль" вместо verdict. **TODO:** вывести explicit "заполните профиль" вместо текущего "null → пустой блок".

10. **Migrations:** все 4 новые таблицы (supplier_profiles, customer_id_aliases, tender_documents, tender_analysis) — в одной миграции `2026_06_23_h025ai_4_5_6_*.py`. Если БД уже содержит старые таблицы — `alembic upgrade head` применит только новые. **Проверить** `SELECT * FROM alembic_version` перед апгрейдом.

### Low / cosmetic

11. В `tenders.py` есть дубликат `_safe_str()` (идентичная копия в `scheduler.py`) — лучше вынести в `app/utils.py`. Не блокер.

12. `__import__("datetime")` в scheduler — исторический артефакт, можно убрать. Не блокер.

13. Нет frontend unit tests (vitest/jest не настроен). Recurrence-pattern 3 fix-коммитов подряд в dashboard.js указывает на необходимость. Не блокер, но рекомендую добавить в следующий спринт.

14. Smoke test golden set не наполнен — нужно вручную подобрать 10 рабочих regNumber 44-ФЗ. Пока `SMOKE_TEST_REG_NUMBERS=""` — тест пропускается.

---

## ⚠️ Безопасно ли деплоить?

**Да**, при условии:
- ✅ Все 83 теста зелёные (`pytest tests/ → 83 passed`)
- ✅ `node --check frontend/public/js/dashboard.js → PARSE_OK`
- ✅ Alembic миграция протестирована локально (`alembic upgrade head` + `alembic downgrade base`)
- ✅ DEEPSEEK_API_KEY настроен (без него AI отключится, остальное работает)
- ⚠️ DAMIA_API_KEY опционально (без него профиль работает вручную)
- ⚠️ REDIS_URL опционально (без него всё в in-memory, теряется при рестарте)

### Рекомендуемый порядок деплоя

1. Backup БД: `pg_dump -U tender -h postgres tendersearch > pre-h025ai-13.dump`
2. Apply migration: `docker compose exec backend alembic upgrade head`
3. Verify: `docker compose exec postgres psql -U tender -d tendersearch -c "\dt"`
   - Should see: supplier_profiles, customer_id_aliases, tender_documents, tender_analysis
4. Rebuild backend image: `docker compose build backend`
5. Restart: `docker compose up -d backend`
6. Smoke check: `curl /api/health` + login + `/api/profile/me` → 200
7. Watch logs 5 min: `docker compose logs -f backend | grep -E "ERROR|Error"` → should be empty
8. (Optional) Send test EGRUL: `curl /api/profile/egrul/7707083893` with admin token

---

## 📊 Telemetry endpoint

Scheduler exposes counters via `get_telemetry()` (Python) and `/api/admin/stats` (HTTP). Полезные метрики:

- `tenders_parsed_today` — успешно синхронизированные тендеры
- `documents_downloaded_today` — скачанные документы
- `parser_errors_today` — ошибки парсера (Varnish 5xx, timeouts)
- `varnish_empty_responses` — пустые ответы (Varnish bug)
- `rate_limit_429s` — нас на rate-limit (если > 0 — снизить concurrency)
- `bridge_aliases_resolved` — успешные резолвы orgId/orgCode через customer_id_aliases
- `last_html_sync` — unix timestamp последнего цикла
- `smoke_test_passes` / `smoke_test_failures` — golden-set
- `smoke_test_last_run` — unix timestamp последнего smoke test

---

## 📞 Контакты

- Вопросы по matcher / h025ai-10: см. SPEC.md §8.2 (verbatim JSON schema)
- Вопросы по DaMIA / h025ai-15: см. `research/ftp-xml-recon.md` (DaMIA-ФНС alternative section)
- Вопросы по HTML-парсеру / h025ai-8: см. `research/zakupki-html-recon.md` (5 URL-паттернов)
- Вопросы по anti-outlier: см. SPEC.md §8.2 (Habr bug case) + `app/services/matcher.py:apply_anti_outlier`
- Вопросы по strict-mode: см. `app/services/document_parser.py:parse_financial_strict`

**Reviewer:** 💻 Разработчик (subagent)
**Date:** 2026-06-23
**Status:** ✅ **Ready for DevOps deployment.**
