# H025 TenderSearch — SPEC.md

**Статус:** BUILD (Фаза 1 завершена, Фаза 2 — разведка источников данных завершена, build_spec_ready)
**Дата:** 2026-06-23
**Версия:** 1.2 (🚨 обновлена архитектура источников данных: FTP zakupki.gov.ru ЗАКРЫТ с 01.01.2025, HTML-парсинг = primary, SOAP = secondary, DaMIA-ФНС = commercial fallback для профиля)
**PM:** Проджект
**Изменения v1.1 → v1.2:**
- ❌ Удалена ссылка на `ftp://ftp.zakupki.gov.ru` как primary (FTP закрыт с 01.01.2025 — 5 независимых подтверждений)
- ✅ HTML-парсинг `zakupki.gov.ru` подтверждён как primary (Habr, июнь 2026)
- 🆕 SOAP-сервис `int44.zakupki.gov.ru` (getDocsIP, бесплатно для физлиц) = secondary для bulk-выгрузок
- 💰 DaMIA API-ФНС (0.1 ₽/запрос) подключён в MVP для профиля по ИНН
- 🆕 Bridge-таблица `customer_id_aliases` для резолва orgId/orgCode → ИНН (3 идентификатора заказчика в ЕИС)
- 🆕 Anti-outlier guard (дисконт <80%) + strict mode для финансовых полей в парсере
- 🆕 Varnish retry + exponential backoff (1s, 2s, 4s) для пустых ответов
- 🆕 TTL кэш: 5 мин (лента) / 24ч (карточка) / 7д (документы) / 30д (bridge) / 1ч (negative)
- 🆕 Smoke-test в CI на 10 известных тендерах (golden-set)
- 📌 h025ai-12 (cron) обновлён: HTML-sync каждые 1-2 часа, SOAP bulk раз в сутки, FTP-sync УДАЛЁН

## 1. Обзор продукта

| Параметр | Значение |
|---|---|
| ID | H025 |
| Название | TenderSearch — AI-аналитик тендеров госзакупок |
| Домен | tenders.ivoryhome.ru |
| Источники данных | zakupki.gov.ru (44-ФЗ, 223-ФЗ), ftp.zakupki.gov.ru (XML), API партнёров (DaMIA, Multitender — резерв) |
| AI-модель | DeepSeek Flash (deepseek/deepseek-v4-flash) |
| Бюджет | $500 (Фаза 1) + $300 (Фаза 2 — парсинг документов) |
| Срок | 2 недели (1 неделя MVP + 1 неделя AI-документы) |

## 2. Проблема

Поставщики тратят часы на ручной поиск и анализ тендеров. Сотни закупок публикуются ежедневно — невозможно отследить все вручную. Нужен AI-ассистент который:
- Автоматически парсит zakupki.gov.ru
- Анализирует документацию AI
- Подбирает тендеры под критерии поставщика
- Присылает подборки на email / в ЛК

## 3. Решение

TenderSearch — SaaS-платформа «умного аналитика закупок»:

**Ядро продукта:**
- Парсер тендеров с zakupki.gov.ru + FTP-выгрузки XML
- **AI-разбор прикреплённых документов** (Word, Excel, PDF, ZIP-архивы) — уникальная фича
- Извлечение требований к лицензиям/сертификатам (ФСТЭК, ФСБ, СРО, МЧС)
- Извлечение финансовых условий (залог, банковская гарантия, обеспечение)
- Извлечение критериев оценки (для конкурсов) с весами в %
- AI-сводка «совпало/не совпало» с цитатами из исходных документов
- Профиль поставщика (ИНН, ОКПД2, лицензии, финансы, регионы)
- Периодические подборки без дублей (смарт-дедупликация по номеру + похожести)
- Личный кабинет с рекомендациями
- Telegram-бот для уведомлений
- Email-дайджесты
- 4 тарифа (Free / Pro / Business / Agency)

## 4. Роли
- **user** — ЛК, подписки, просмотр тендеров
- **admin** — статистика, пользователи, управление тарифами

## 5. Страницы

### Лендинг
- `/` — Hero, как работает, тарифы, FAQ
- `/login` — вход
- `/register` — регистрация
- `/pricing` — тарифы

### Личный кабинет
- `/dashboard` — подборки тендеров, рекомендации
- `/dashboard/subscriptions` — управление подписками
- `/dashboard/tenders` — все тендеры
- `/dashboard/tenders/:id` — карточка тендера с AI-анализом
- `/dashboard/profile` — профиль и критерии
- `/dashboard/plan` — тариф и биллинг

### Дополнительно
- `/marketing` — маркетинговая стратегия (QA-доступ)
- `/tester` — панель тестировщика

## 6. API

### Auth
- `POST /api/auth/register` — регистрация
- `POST /api/auth/login` — вход (JWT)
- `GET /api/auth/me` — профиль
- `PATCH /api/auth/me` — обновить профиль (ИНН, ОКПД2, лицензии, регионы, финлимиты)

### Supplier Profile (профиль поставщика — ключ к матчингу)
- `GET /api/profile` — получить профиль
- `PATCH /api/profile` — обновить профиль
- `POST /api/profile/licenses` — добавить лицензию (тип, номер, срок)
- `DELETE /api/profile/licenses/:id` — удалить лицензию
- `GET /api/profile/egrul/:inn` — авто-подтянуть данные из ЕГРЮЛ по ИНН (через **DaMIA API-ФНС**, добавлен в v1.2)

### Tenders
- `GET /api/tenders` — поиск тендеров (query, filters, пагинация)
- `GET /api/tenders/:id` — карточка тендера + AI-сводка (✅⚠️❌)
- `GET /api/tenders/:id/documents` — список документов тендера
- `GET /api/tenders/:id/documents/:docId/download` — скачать оригинал документа
- `POST /api/tenders/:id/analyze` — запросить AI-разбор (с токенами или в лимит)
- `GET /api/tenders/:id/analysis` — получить результат AI-разбора
- `GET /api/tenders/match` — получить матчинг по профилю + подпискам (verdict по каждому)

### Subscriptions
- `POST /api/subscriptions` — создать подписку (ключевые слова, фильтры)
- `GET /api/subscriptions` — мои подписки
- `PATCH /api/subscriptions/:id` — обновить подписку
- `DELETE /api/subscriptions/:id` — удалить подписку
- `GET /api/subscriptions/:id/matches` — подобранные тендеры (только новые, без дублей)

### Cron / AI
- Внутренний: **HTML-парсинг zakupki.gov.ru каждые 1-2 часа (44-ФЗ)** — обновлён в v1.2 (был FTP, теперь HTML)
- Внутренний: ~~скачивание FTP-выгрузок XML~~ ❌ **УДАЛЕНО в v1.2** (FTP закрыт с 01.01.2025)
- Внутренний: **SOAP bulk-выгрузка getDocsByOrgRegionRequest — раз в сутки в 03:00** (Phase 2)
- Внутренний: скачивание прикреплённых документов к новым тендерам (concurrency=3, retry)
- Внутренний: AI-анализ новых тендеров (DeepSeek)
- Внутренний: сопоставление тендеров с профилем поставщика + подписками
- Внутренний: **проверка ЕГРЮЛ по ИНН через DaMIA API-ФНС** (при регистрации/обновлении профиля, кэш 30 дней)
- Внутренний: проверка сроков лицензий (ежедневно, уведомления за 60/30/7 дней)
- Внутренний: дедупликация (по номеру закупки + похожести описания)
- Внутренний: **smoke-test golden-set** (10 известных тендеров) — ежедневно, alert при регрессии

### Admin
- `GET /api/admin/stats` — статистика
- `GET /api/admin/users` — пользователи
- `GET /api/admin/tariffs` — управление тарифами
- `GET /api/admin/tenders` — все тендеры + статусы парсинга/AI

### Billing
- `GET /api/tariffs` — публичные тарифы (4 шт: Free / Pro / Business / Agency)
- `POST /api/billing/create-payment` — создать платёж
- `POST /api/billing/webhook` — webhook платёжки

### Notifications
- `GET /api/notifications` — уведомления пользователя
- `POST /api/notifications/read/:id` — пометить прочитанным
- `POST /api/telegram/connect` — привязать Telegram (получение bot-token)
- `POST /api/telegram/webhook` — webhook от Telegram-бота

## 7. Тарифы (v2 — расширенные)

| Тариф | Цена | AI-анализов/мес | Подписок | Документов на тендер | Приоритет | Telegram-бот |
|---|---|---|---|---|---|---|
| **Free** | 0 ₽ | 20 | 2 | до 5 | низкий | ❌ |
| **Pro** | 1 990 ₽ | 200 | 10 | до 30 | стандартный | ✅ |
| **Business** | 4 990 ₽ | 1 000 | 50 | безлимит | высокий | ✅ |
| **Agency** | 9 990 ₽ | безлимит | 100 | безлимит | наивысший | ✅ + white-label |

## 8. Технический стек

- **Бэкенд:** Python 3.11 + FastAPI
- **База:** PostgreSQL 16
- **AI:** DeepSeek Flash (через API ключ)
- **Парсер:** httpx + BeautifulSoup4 + lxml + aiohttp (async)
- **Обработка документов:**
  - PDF: `pdfplumber` + `PyPDF2`
  - Word (.docx): `python-docx`
  - Word (.doc — старый формат): `antiword` / `textract`
  - Excel (.xlsx): `openpyxl`
  - Excel (.xls — старый): `xlrd`
  - ZIP-архивы: `zipfile` (рекурсивный разбор)
  - RAR-архивы: `rarfile` (опционально)
- **OCR (для сканов):** Tesseract / PaddleOCR (опционально)
- **Cron:** APScheduler (внутри FastAPI)
- **Email:** aiosmtplib (дайджесты) + SMTP relay
- **Telegram:** python-telegram-bot (async)
- **Фронтенд:** Vanilla HTML/CSS/JS (SPA) — тёмная тема
- **Деплой:** Docker Compose (3 контейнера: backend, frontend=nginx, postgres)

## 8.1. Источники данных (приоритеты) — v1.2

> 🚨 **КРИТИЧНОЕ ОБНОВЛЕНИЕ v1.2 (2026-06-23):** FTP-сервер `ftp://ftp.zakupki.gov.ru` **ЗАКРЫТ с 01.01.2025** (5 независимых подтверждений: официальный ЕИС, Habr «Парсил zakupki.gov.ru без API» (июнь 2026), vc.ru, cryptopro.ru, intec-balance.ru). Использовать его для MVP **НЕВОЗМОЖНО**. Замена — HTML-парсинг (primary) + SOAP (secondary) + DaMIA-ФНС (commercial для профиля).

**Основной источник (для MVP):**
1. **zakupki.gov.ru (HTML-парсинг)** — поиск по извещениям 44-ФЗ (extended search results)
   - URL: `https://zakupki.gov.ru/epz/order/extendedsearch/results.html?fz44=on&...`
   - Параметры: searchString, morphology, fz44, регионы, цена, publishDateFrom/To, sortBy
   - Карточка 44-ФЗ: `https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html?regNumber={regNumber}`
   - Документы тендера: `https://zakupki.gov.ru/epz/order/notice/zk20/view/documents.html?regNumber={regNumber}`
   - Скачивание файла: `https://zakupki.gov.ru/epz/main/public/download/downloadDocument.html?id={documentId}`
   - Карточка контракта: `https://zakupki.gov.ru/epz/contract/contractCard/common-info.html?reestrNumber={reestrNumber}`
   - Акты приёмки: `https://zakupki.gov.ru/epz/contract/contractCard/document-info.html?reestrNumber={reestrNumber}`
   - **Concurrency=3, rate-limit 8 req/s, User-Agent обязателен**
   - **Retry + exponential backoff (1s, 2s, 4s)** для Varnish пустых ответов (1 из 4-5 запросов)
   - **Coverage:** 44-ФЗ — 100%, 223-ФЗ — best-effort (отдельный парсер в Phase 2)

2. **SOAP-сервис int44.zakupki.gov.ru (secondary, bulk)** — для архивных выгрузок и справочников
   - Endpoint: `https://int44.zakupki.gov.ru/eis-integration/services/getDocsIP` (физлица, бесплатно через токен Госуслуг)
   - Метод `getDocsByOrgRegionRequest` — bulk-выгрузка по региону + типу документа за день
   - Метод `getNsiRequest` — справочники (ОКПД2, КТРУ, регионы)
   - ⚠️ **Сервис нестабилен** (2025: getDocsLE2 → "POST not supported"). Использовать ТОЛЬКО для bulk/разовых выгрузок, не как primary.
   - **Этап:** Phase 2 (для инициализации БД по новым регионам)

**Коммерческий fallback (для авто-заполнения профиля):**
3. **DaMIA API-ФНС** — авто-подтягивание реквизитов юрлица по ИНН
   - URL: `https://api.damia.ru/fns/...` (REST + JSON)
   - Цена: от 0.1 ₽/запрос (индивидуальный тариф), **5 000 ₽/мес в MVP** (~10 000 запросов)
   - **Назначение:** endpoint `GET /api/profile/egrul/:inn` (SPEC §6) — авто-заполнение supplier_profile при регистрации
   - **Альтернатива (отклонена):** самим парсить `egrul.nalog.ru` — captcha, юр. риски, +5-7 дней разработки

**DEPRECATED / НЕ используется в MVP:**
- ❌ ~~`ftp://ftp.zakupki.gov.ru`~~ — ЗАКРЫТ с 01.01.2025
- ❌ ~~Seldon 1.7/Pro (8 280 ₽/мес)~~ — overkill, не нужен
- ❌ ~~Multitender API (15 000 ₽/мес)~~ — в 3 раза дороже DaMIA
- ❌ ~~Контур.Закупки (от 20 000 ₽/мес)~~ — enterprise, не нужно
- ⏸️ ~~223-ФЗ парсер~~ — Phase 2 (другая вёрстка, best-effort)
- ⏸️ ~~DaMIA API-Закупки~~ — Post-MVP (при >1000 user как fallback при сбоях HTML-парсера)
- ⏸️ ~~SOAP `getDocsLE` (юрлица)~~ — нужна КЭП юрлица, сложно

**Бюджет источников данных (MVP):**
| Источник | Бюджет/мес | Назначение |
|---|---|---|
| zakupki.gov.ru HTML | $0 (бесплатно) | Primary — 100% данных о тендерах |
| SOAP getDocsIP | $0 (бесплатно) | Bulk-выгрузки (Phase 2) |
| DaMIA API-ФНС | ~5 000 ₽ ($55) | Профиль по ИНН (только при регистрации/обновлении) |
| **ИТОГО** | **~$55/мес** | Вписывается в $500 MVP + $300 фаза 2 |

**Мониторинг источников (для h025ai-12 cron + Admin API):**
- ✅ Ежедневный **smoke-test** в CI на 10 известных тендерах (golden-set regNumber)
- ✅ Алерты при регрессии вёрстки (если хоть один из golden-set упал)
- ✅ Метрики: `tenders_parsed_today`, `documents_downloaded_today`, `parser_errors_today`, `varnish_empty_responses`, `rate_limit_429s`, `bridge_aliases_resolved`, `last_html_sync`

## 8.2. Матчинг профиля поставщика

**Входные данные (профиль):**
- ИНН → авто-парсинг из ЕГРЮЛ (название, ОКВЭД2, регион)
- ОКПД2 (массив кодов, что поставляет)
- Регионы (массив, где работает)
- Лицензии (массив: тип, номер, срок действия)
- Финлимиты: мин/макс сумма контракта, макс размер обеспечения
- Типы процедур (аукцион / конкурс / запрос котировок / единственный)

**Алгоритм матчинга (verdict):**
- ✅ **СОВПАДАЕТ**: ОКПД2 ∩ профиль ≠ ∅, регион в списке, сумма в диапазоне, нет блокирующих требований
- ⚠️ **ТРЕБУЕТ ВНИМАНИЯ**: частичное совпадение, есть требования которые нужно проверить (лицензии, опыт)
- ❌ **НЕ ПОДХОДИТ**: ОКПД2 не совпадает ИЛИ регион вне списка ИЛИ сумма вне диапазона ИЛИ дедлайн < 3 дней

**Скоринг (0-100):**
- Совпадение ОКПД2: +30
- Совпадение региона: +20
- Сумма в диапазоне: +20
- Все нужные лицензии есть: +20
- Время на подготовку ≥ 7 дней: +10

**⚠️ Anti-outlier guard (добавлен в v1.2 на основе research):**
При скоринге по цене применять фильтр `discount = 1 - (contract_price / max_price) < 0.80` — иначе дисконт >80% почти всегда = ошибка парсинга или рамочный контракт. UI должен явно показать "⚠️ Проверьте вручную" если discount >80%, а не использовать такие данные для скоринга.

**⚠️ Strict mode для финансовых полей (добавлен в v1.2):**
Парсер НМЦК должен использовать ТОЛЬКО canonical label ("Начальная (максимальная) цена контракта"), без fallback на синонимы. Fallback «Максимальное значение цены договора» = рамочный лимит, а не НМЦК — ломает скоринг (реально наблюдаемый баг в Habr-кейсе, дисконт 99.6%).

**⚠️ НМЦК покрытие (добавлен в v1.2):**
НМЦК живёт в `/order/notice/ea44/view/common-info.html` (карточка извещения), а НЕ в карточке контракта. Покрытие для 44-ФЗ: **только 4-5%**. UI карточки тендера (h025ai-14) должен явно показывать "НМЦК не указана" (без ложных 0), если данных нет.

**Извлечение данных из документов (AI-prompt):**
```
Извлеки из документа:
1. Предмет закупки (кратко)
2. Требования к товару/работе/услуге
3. Коды ОКПД2 / ОКВЭД2
4. Требования к участнику (лицензии, опыт, СРО)
5. Размер обеспечения заявки (₽ и %)
6. Размер обеспечения контракта (₽ и %)
7. Срок исполнения
8. Срок подачи заявок
9. Критерии оценки (если конкурс): название, вес в %
10. Штрафы и пени

Верни в JSON:
{
  "subject": "...",
  "okpd2_codes": ["..."],
  "requirements": {
    "licenses": [{"type": "ФСТЭК", "level": "..."}],
    "sro": true,
    "experience_years": 3
  },
  "financial": {
    "application_guarantee_rub": 622500,
    "application_guarantee_pct": 5,
    "contract_guarantee_rub": 1245000,
    "contract_guarantee_pct": 10
  },
  "deadlines": {
    "submission": "2026-06-28T10:00:00",
    "execution_days": 90
  },
  "evaluation_criteria": [
    {"name": "Цена контракта", "weight_pct": 60},
    {"name": "Квалификация", "weight_pct": 30}
  ],
  "source_pages": [3, 12, 25],
  "source_quotes": ["..."]
}
```

## 9. Структура проекта

```
h025-tender-search/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI entry
│   │   ├── config.py        # Settings
│   │   ├── database.py      # SQLAlchemy
│   │   ├── models.py        # DB models
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── tenders.py
│   │   │   ├── subscriptions.py
│   │   │   ├── admin.py
│   │   │   └── billing.py
│   │   ├── services/
│   │   │   ├── parser.py    # zakupki.gov.ru парсер
│   │   │   ├── ai.py        # DeepSeek анализ
│   │   │   └── matcher.py   # Подбор тендеров
│   │   └── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── public/
│   │   ├── index.html       # Лендинг
│   │   ├── login.html
│   │   ├── register.html
│   │   ├── pricing.html
│   │   ├── tester.html      # QA-панель
│   │   ├── marketing.html   # Маркетинг-стратегия
│   │   ├── dashboard/       # ЛК
│   │   ├── css/
│   │   ├── js/
│   │   └── img/
│   └── nginx.conf
├── docker-compose.yml
├── SPEC.md
├── CJM.md
└── PLAN.md
```

## 10. CJM (ключевые этапы)

1. **Посадка** — лендинг → регистрация (конверсия 5%)
2. **Онбординг** — заполнение критериев → первая подписка
3. **Aha-момент** — первая AI-подборка тендеров в ЛК
4. **Активация** — открытие карточки тендера с AI-анализом
5. **Удержание** — регулярные email-уведомления о новых тендерах
6. **Монетизация** — исчерпание лимита Free → апгрейд на Pro

## 11. KPI (v2 — обновлённые)

| Метрика | Месяц 1 | Месяц 3 | Месяц 6 |
|---|---|---|---|
| Регистрации | 80 | 350 | 800 |
| Конверсия Free→Pro | 7% | 10% | 12% |
| MAU retention | 50% | 55% | 60% |
| Тендеров в индексе | 3 000 | 15 000 | 50 000 |
| AI-анализов документов | 200 | 1 500 | 5 000 |
| Точность AI-извлечения | 85% | 92% | 95% |
| MRR | 5K ₽ | 35K ₽ | 95K ₽ |
| NPS | 30 | 45 | 55 |

**KPI качества AI:**
- Точность извлечения ОКПД2: >90%
- Точность извлечения суммы обеспечения: >95%
- Точность извлечения сроков: >92%
- Точность определения лицензий: >88%
- Время AI-разбора одного тендера: <60 сек
