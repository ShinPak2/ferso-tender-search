# H025 TenderSearch — SPEC.md

**Статус:** BUILD (Фаза 1)
**Дата:** 2026-06-22
**PM:** Проджект

## 1. Обзор продукта

| Параметр | Значение |
|---|---|
| ID | H025 |
| Название | TenderSearch — AI-анализ тендеров госзакупок |
| Домен | tenders.ivoryhome.ru |
| Источник | zakupki.gov.ru (44-ФЗ, 223-ФЗ) |
| AI-модель | DeepSeek Flash (deepseek/deepseek-v4-flash) |
| Бюджет | $500 |
| Срок | 1 неделя |

## 2. Проблема

Поставщики тратят часы на ручной поиск и анализ тендеров. Сотни закупок публикуются ежедневно — невозможно отследить все вручную. Нужен AI-ассистент который:
- Автоматически парсит zakupki.gov.ru
- Анализирует документацию AI
- Подбирает тендеры под критерии поставщика
- Присылает подборки на email / в ЛК

## 3. Решение

TenderSearch — SaaS-платформа:
- Парсер тендеров (zakupki.gov.ru)
- AI-анализ документации и критериев
- Периодические подборки по подписке
- Личный кабинет с рекомендациями
- 3 тарифа (Free / Pro / Business)

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

### Tenders
- `GET /api/tenders` — поиск тендеров (query, filters, пагинация)
- `GET /api/tenders/:id` — карточка тендера + AI-анализ
- `GET /api/tenders/:id/documents` — документация тендера

### Subscriptions
- `POST /api/subscriptions` — создать подписку (ключевые слова, фильтры)
- `GET /api/subscriptions` — мои подписки
- `DELETE /api/subscriptions/:id` — удалить подписку
- `GET /api/subscriptions/:id/matches` — подобранные тендеры

### Cron / AI
- Внутренний: парсинг zakupki.gov.ru каждый час
- Внутренний: AI-анализ новых тендеров
- Внутренний: сопоставление тендеров с подписками

### Admin
- `GET /api/admin/stats` — статистика
- `GET /api/admin/users` — пользователи
- `GET /api/admin/tariffs` — управление тарифами

### Billing
- `GET /api/tariffs` — публичные тарифы
- `POST /api/billing/create-payment` — создать платёж
- `POST /api/billing/webhook` — webhook платёжки

## 7. Тарифы

| Тариф | Цена | Тендеров/мес | Подписок | AI-анализ |
|---|---|---|---|---|
| Free | 0 ₽ | 10 | 1 | Базовый |
| Pro | 1 990 ₽ | 100 | 5 | Полный |
| Business | 4 990 ₽ | 500 | 20 | Полный + приоритет |

## 8. Технический стек

- **Бэкенд:** Python 3.11 + FastAPI
- **База:** PostgreSQL 16
- **AI:** DeepSeek Flash (через API ключ)
- **Парсер:** httpx + BeautifulSoup4 + lxml
- **Cron:** APScheduler (внутри FastAPI)
- **Фронтенд:** Vanilla HTML/CSS/JS (SPA) — тёмная тема
- **Деплой:** Docker Compose (3 контейнера: backend, frontend=nginx, postgres)

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

## 11. KPI
- Регистрации: 50/мес
- Конверсия Free→Pro: 5%
- MAU retention: 40%
- Тендеров в индексе: 10 000+
- AI-анализов: 500/мес
