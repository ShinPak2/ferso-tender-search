// QA-панель тестировщика TenderSearch H025
// Хранилище прогресса и багов в localStorage браузера (не отправляется на сервер)

(function () {
  'use strict';

  // ── КОНФИГ ХРАНИЛИЩА ─────────────────────────────────
  const STORAGE_KEY = 'tendersearch.qa.v1';
  const SECTIONS = [
    { id: 'smoke',         icon: '🔥', title: 'Smoke (критический путь)' },
    { id: 'auth',          icon: '🔐', title: 'Auth' },
    { id: 'tenders',       icon: '📋', title: 'Поиск тендеров' },
    { id: 'subscriptions', icon: '📬', title: 'Подписки' },
    { id: 'ai',            icon: '🤖', title: 'AI-анализ' },
    { id: 'dashboard',     icon: '📊', title: 'Дашборд' },
    { id: 'api',           icon: '⚙️', title: 'API (ручной curl)' },
    { id: 'ui',            icon: '🎨', title: 'UI/UX' },
    { id: 'security',      icon: '🔒', title: 'Безопасность' },
    { id: 'perf',          icon: '⚡', title: 'Производительность' }
  ];

  // ── ТЕСТ-КЕЙСЫ ──────────────────────────────────────
  const TESTCASES = [
    // ===== SMOKE =====
    { id: 'SMOKE-01', section: 'smoke', priority: 'critical', title: 'Открыть https://tenders.ivoryhome.ru → лендинг рендерится',
      pre: 'Открыть в чистом браузере', steps: '1. Перейти на / 2. Дождаться загрузки 3. Проверить Hero, тарифы, FAQ',
      expected: 'Лендинг загружается < 2с, нет ошибок в Console (F12)', module: 'Лендинг' },
    { id: 'SMOKE-02', section: 'smoke', priority: 'critical', title: 'Регистрация нового пользователя через форму',
      pre: 'Не залогинен', steps: '1. /register 2. Ввести email qa+<random>@test.ru 3. Пароль Test1234! 4. Submit',
      expected: 'Регистрация успешна → редирект в /dashboard или /login', module: 'Auth' },
    { id: 'SMOKE-03', section: 'smoke', priority: 'critical', title: 'Логин существующего пользователя v2@inbox.ru',
      pre: 'Логин: v2@inbox.ru / Qwerty01', steps: '1. /login 2. Ввести креды 3. Submit 4. Должен попасть в /dashboard',
      expected: '200 OK, JWT-токен в localStorage, редирект в /dashboard, дашборд показывает имя', module: 'Auth' },
    { id: 'SMOKE-04', section: 'smoke', priority: 'critical', title: 'Просмотр списка тендеров в дашборде',
      pre: 'Залогинен как v2@inbox.ru', steps: '1. Открыть /dashboard 2. Дождаться загрузки stats 3. Проверить выдачу',
      expected: 'Список тендеров рендерится, карточки кликабельны', module: 'Дашборд' },
    { id: 'SMOKE-05', section: 'smoke', priority: 'critical', title: 'Карточка тендера с AI-анализом',
      pre: 'В дашборде', steps: '1. Кликнуть на любой тендер 2. Дождаться загрузки 3. Проверить поля',
      expected: 'Карточка открывается, есть поля: title, customer, price, deadline, law_type, AI-анализ', module: 'Тендеры' },
    { id: 'SMOKE-06', section: 'smoke', priority: 'critical', title: 'Создание подписки',
      pre: 'Залогинен', steps: '1. /dashboard/subscriptions 2. Заполнить name + keywords 3. Save',
      expected: 'Подписка создана, отображается в списке, лимит не превышен (Free = 1)', module: 'Подписки' },
    { id: 'SMOKE-07', section: 'smoke', priority: 'critical', title: 'Выход из аккаунта',
      pre: 'Залогинен', steps: '1. Кликнуть «Выйти» в /dashboard 2. Должен редиректнуть на /',
      expected: 'Токен удалён из localStorage, редирект на лендинг, защищённые страницы → /login', module: 'Auth' },

    // ===== AUTH =====
    { id: 'AUTH-01', section: 'auth', priority: 'high', title: 'Регистрация с уже существующим email',
      pre: 'Сначала создать test@x.ru', steps: 'POST /api/auth/register с тем же email',
      expected: '409 Conflict или 400 с понятным сообщением', module: 'Auth' },
    { id: 'AUTH-02', section: 'auth', priority: 'high', title: 'Регистрация с коротким паролем (< 6)',
      steps: 'POST с password=«123»',
      expected: '400 + сообщение «Пароль слишком короткий»', module: 'Auth' },
    { id: 'AUTH-03', section: 'auth', priority: 'high', title: 'Регистрация с невалидным email',
      steps: 'POST с email=«not-an-email»',
      expected: '400 + ошибка валидации email', module: 'Auth' },
    { id: 'AUTH-04', section: 'auth', priority: 'high', title: 'Логин с неверным паролем',
      steps: 'POST /api/auth/login v2@inbox.ru / WrongPass',
      expected: '401 Unauthorized, без утечки о существовании email', module: 'Auth' },
    { id: 'AUTH-05', section: 'auth', priority: 'medium', title: 'GET /api/auth/me с токеном',
      steps: 'GET с Authorization: Bearer <token>',
      expected: '200 + JSON {id, email, name, tariff, role}', module: 'Auth' },
    { id: 'AUTH-06', section: 'auth', priority: 'medium', title: 'GET /api/auth/me без токена',
      steps: 'GET без заголовка Authorization',
      expected: '401 Unauthorized', module: 'Auth' },
    { id: 'AUTH-07', section: 'auth', priority: 'medium', title: 'GET /api/auth/me с невалидным токеном',
      steps: 'GET с токеном «eyJfakefake»',
      expected: '401 Unauthorized', module: 'Auth' },
    { id: 'AUTH-08', section: 'auth', priority: 'low', title: 'Пароль не возвращается в API-ответах',
      steps: 'Логин, посмотреть ответ /api/auth/me',
      expected: 'Поля password / password_hash нет в ответе', module: 'Auth' },
    { id: 'AUTH-09', section: 'auth', priority: 'medium', title: 'Rate-limit на логин (5 попыток/мин)',
      steps: '10 раз подряд POST /api/auth/login с неверным паролем',
      expected: 'После 5 попыток 429 Too Many Requests', module: 'Auth' },

    // ===== TENDERS =====
    { id: 'TEN-01', section: 'tenders', priority: 'high', title: 'GET /api/tenders без фильтров',
      steps: 'GET /api/tenders',
      expected: '200, JSON {items: [...], total, page, page_size}', module: 'Tenders' },
    { id: 'TEN-02', section: 'tenders', priority: 'high', title: 'Поиск по ключевому слову',
      steps: 'GET /api/tenders?q=строительство',
      expected: 'Список тендеров с «строительство» в названии или описании', module: 'Tenders' },
    { id: 'TEN-03', section: 'tenders', priority: 'high', title: 'Фильтр по 44-ФЗ',
      steps: 'GET /api/tenders?law_type=44-ФЗ',
      expected: 'Только тендеры по 44-ФЗ', module: 'Tenders' },
    { id: 'TEN-04', section: 'tenders', priority: 'high', title: 'Фильтр по диапазону цены',
      steps: 'GET /api/tenders?price_min=100000&price_max=5000000',
      expected: 'Только тендеры в этом диапазоне', module: 'Tenders' },
    { id: 'TEN-05', section: 'tenders', priority: 'medium', title: 'Фильтр по региону',
      steps: 'GET /api/tenders?region=Москва',
      expected: 'Только московские тендеры', module: 'Tenders' },
    { id: 'TEN-06', section: 'tenders', priority: 'high', title: 'Пагинация: page=1&page_size=5',
      steps: 'GET /api/tenders?page=1&page_size=5',
      expected: '5 тендеров, total >= 5, pages >= 1', module: 'Tenders' },
    { id: 'TEN-07', section: 'tenders', priority: 'high', title: 'Карточка тендера по ID',
      steps: 'Сначала GET /api/tenders → взять id → GET /api/tenders/{id}',
      expected: '200, все поля тендера + AI-анализ (если есть)', module: 'Tenders' },
    { id: 'TEN-08', section: 'tenders', priority: 'medium', title: '404 для несуществующего тендера',
      steps: 'GET /api/tenders/00000000-0000-0000-0000-000000000000',
      expected: '404 Not Found с JSON {detail: "..."}', module: 'Tenders' },
    { id: 'TEN-09', section: 'tenders', priority: 'medium', title: 'Документы тендера',
      steps: 'GET /api/tenders/{id}/documents',
      expected: '200, JSON-массив с полями id, name, url, size, type', module: 'Tenders' },
    { id: 'TEN-10', section: 'tenders', priority: 'low', title: 'Сортировка по дате',
      steps: 'GET /api/tenders?sort=deadline:asc',
      expected: 'Тендеры отсортированы по возрастанию deadline', module: 'Tenders' },

    // ===== SUBSCRIPTIONS =====
    { id: 'SUB-01', section: 'subscriptions', priority: 'high', title: 'Создание подписки',
      steps: 'POST /api/subscriptions {name: "Стройка", keywords: ["строительство","ремонт"]}',
      expected: '201, возвращает {id, name, keywords, match_count: 0, created_at}', module: 'Подписки' },
    { id: 'SUB-02', section: 'subscriptions', priority: 'high', title: 'Валидация: пустое имя',
      steps: 'POST с name=""',
      expected: '400 + сообщение об ошибке', module: 'Подписки' },
    { id: 'SUB-03', section: 'subscriptions', priority: 'high', title: 'Лимит Free (1 подписка)',
      pre: 'Free-тариф, уже есть 1 подписка', steps: 'POST ещё одну',
      expected: '403 + сообщение «Лимит подписок для тарифа Free исчерпан»', module: 'Подписки' },
    { id: 'SUB-04', section: 'subscriptions', priority: 'medium', title: 'GET /api/subscriptions — список',
      steps: 'GET /api/subscriptions',
      expected: '200, массив моих подписок', module: 'Подписки' },
    { id: 'SUB-05', section: 'subscriptions', priority: 'medium', title: 'DELETE /api/subscriptions/{id}',
      steps: 'Создать → удалить',
      expected: '204 No Content', module: 'Подписки' },
    { id: 'SUB-06', section: 'subscriptions', priority: 'medium', title: 'Нельзя удалить чужую подписку',
      steps: 'Под другим юзером DELETE чужого id',
      expected: '404 или 403', module: 'Подписки' },
    { id: 'SUB-07', section: 'subscriptions', priority: 'medium', title: 'GET /api/subscriptions/{id}/matches',
      steps: 'GET для существующей подписки',
      expected: '200, массив подобранных тендеров', module: 'Подписки' },
    { id: 'SUB-08', section: 'subscriptions', priority: 'medium', title: 'Ключевые слова сохраняются',
      steps: 'Создать с ["IT","сервер"], GET, проверить',
      expected: 'keywords содержит оба слова', module: 'Подписки' },

    // ===== AI =====
    { id: 'AI-01', section: 'ai', priority: 'high', title: 'AI-анализ создаётся при первом GET /api/tenders/{id}',
      steps: 'Открыть новый тендер, проверить наличие поля analysis',
      expected: '200, есть analysis с полями relevance (1-10), risks, recommendation', module: 'AI' },
    { id: 'AI-02', section: 'ai', priority: 'medium', title: 'AI-анализ показывает verdict',
      steps: 'Открыть карточку тендера, прокрутить до AI-блока',
      expected: 'Есть verdict: «подходит» / «не подходит» / «требует проверки»', module: 'AI' },
    { id: 'AI-03', section: 'ai', priority: 'medium', title: 'Mock-анализ при отсутствии API-ключа DeepSeek',
      steps: 'Если в .env нет DEEPSEEK_API_KEY, анализ всё равно работает',
      expected: 'AI-анализ с пометкой «демо» или просто текст', module: 'AI' },
    { id: 'AI-04', section: 'ai', priority: 'low', title: 'Повторный запрос не перезаписывает анализ',
      steps: 'GET /api/tenders/{id} дважды подряд',
      expected: 'analysis не изменился (кэш)', module: 'AI' },
    { id: 'AI-05', section: 'ai', priority: 'high', title: 'Ошибка AI не ломает ответ',
      steps: 'Симулировать сбой AI (на сервере), GET /api/tenders/{id}',
      expected: '200, без поля analysis, без 500', module: 'AI' },

    // ===== DASHBOARD =====
    { id: 'DASH-01', section: 'dashboard', priority: 'high', title: '/dashboard — статы',
      steps: 'Залогинен, открыть /dashboard',
      expected: 'Видны stat-tenders, stat-subscriptions, stat-active, stat-ai', module: 'Дашборд' },
    { id: 'DASH-02', section: 'dashboard', priority: 'critical', title: 'Редирект на /login без токена',
      steps: 'Выйти → открыть /dashboard',
      expected: 'Редирект на /login', module: 'Дашборд' },
    { id: 'DASH-03', section: 'dashboard', priority: 'medium', title: '/dashboard/subscriptions',
      steps: 'Открыть страницу',
      expected: 'Список подписок рендерится', module: 'Дашборд' },
    { id: 'DASH-04', section: 'dashboard', priority: 'medium', title: '/dashboard/tenders — таблица',
      steps: 'Открыть страницу',
      expected: 'Таблица тендеров с пагинацией', module: 'Дашборд' },
    { id: 'DASH-05', section: 'dashboard', priority: 'high', title: '/dashboard/tender-detail?id=...',
      steps: 'Открыть с реальным id',
      expected: 'Карточка с AI-анализом', module: 'Дашборд' },
    { id: 'DASH-06', section: 'dashboard', priority: 'medium', title: '/dashboard/profile',
      steps: 'Открыть страницу',
      expected: 'Имя, email, смена пароля (если есть)', module: 'Дашборд' },
    { id: 'DASH-07', section: 'dashboard', priority: 'medium', title: '/dashboard/plan',
      steps: 'Открыть страницу',
      expected: 'Текущий тариф + список тарифов для апгрейда', module: 'Дашборд' },

    // ===== API (curl) =====
    { id: 'API-01', section: 'api', priority: 'high', title: 'GET /api/health',
      steps: 'curl -s https://tenders.ivoryhome.ru/api/health',
      expected: '200, {"status":"ok","db":"ok"}', module: 'API' },
    { id: 'API-02', section: 'api', priority: 'high', title: 'GET /api/tariffs',
      steps: 'curl -s https://tenders.ivoryhome.ru/api/tariffs',
      expected: '200, JSON-массив из 4 тарифов (Free, Pro, Business, Agency)', module: 'API' },
    { id: 'API-03', section: 'api', priority: 'medium', title: 'CORS заголовки',
      steps: 'curl -I -X OPTIONS /api/auth/login -H "Origin: https://example.com"',
      expected: 'Access-Control-Allow-Origin: * или конкретный домен', module: 'API' },
    { id: 'API-04', section: 'api', priority: 'medium', title: 'Content-Type JSON',
      steps: 'curl -I /api/tenders',
      expected: 'Content-Type: application/json', module: 'API' },
    { id: 'API-05', section: 'api', priority: 'medium', title: 'Gzip / br сжатие',
      steps: 'curl -H "Accept-Encoding: gzip" -I /api/tenders',
      expected: 'Content-Encoding: gzip или br', module: 'API' },
    { id: 'API-06', section: 'api', priority: 'low', title: 'OPTIONS preflight 204',
      steps: 'curl -X OPTIONS /api/tenders -H "Origin: ..." -H "Access-Control-Request-Method: GET"',
      expected: '204 No Content + CORS заголовки', module: 'API' },

    // ===== UI/UX =====
    { id: 'UI-01', section: 'ui', priority: 'high', title: 'Тёмная тема на всех страницах',
      steps: 'Пройти все страницы, проверить bg',
      expected: 'Единая тёмная тема, светлого текста на тёмном фоне', module: 'UI' },
    { id: 'UI-02', section: 'ui', priority: 'high', title: 'Мобильная адаптивность (≤768px)',
      steps: 'DevTools → Toggle device → iPhone',
      expected: 'Все элементы помещаются, нет горизонтального скролла, кнопки кликабельны', module: 'UI' },
    { id: 'UI-03', section: 'ui', priority: 'medium', title: 'Состояния загрузки (spinner)',
      steps: 'Слабое соединение (DevTools → Slow 3G) → переходы',
      expected: 'Есть спиннер/скелетон во время загрузки', module: 'UI' },
    { id: 'UI-04', section: 'ui', priority: 'medium', title: 'Пустые состояния',
      steps: 'Free-юзер без подписок → /dashboard/subscriptions',
      expected: 'Empty state с иконкой и подсказкой', module: 'UI' },
    { id: 'UI-05', section: 'ui', priority: 'medium', title: 'Ошибки отображаются',
      steps: 'Попытаться открыть несуществующий тендер',
      expected: 'Toast/banner с понятным сообщением', module: 'UI' },
    { id: 'UI-06', section: 'ui', priority: 'low', title: 'Кнопки disabled во время запроса',
      steps: 'Двойной клик на «Войти»',
      expected: 'Кнопка дизейблится, нет двойного POST', module: 'UI' },
    { id: 'UI-07', section: 'ui', priority: 'low', title: 'Favicon отображается',
      steps: 'Посмотреть на вкладку браузера',
      expected: 'Видна фиолетовая звезда (TenderSearch favicon)', module: 'UI' },
    { id: 'UI-08', section: 'ui', priority: 'medium', title: 'Навигация: хлебные крошки',
      steps: 'Пройти /dashboard → /dashboard/subscriptions',
      expected: 'Есть крошки или кнопка «Назад»', module: 'UI' },
    { id: 'UI-09', section: 'ui', priority: 'low', title: 'Footer: support@ferso.ru',
      steps: 'Скролл вниз на любой странице',
      expected: 'В футере видна почта support@ferso.ru', module: 'UI' },
    { id: 'UI-10', section: 'ui', priority: 'medium', title: 'No JS errors в Console',
      steps: 'F12 → Console → пройти основной путь',
      expected: '0 красных ошибок (warnings допустимы)', module: 'UI' },

    // ===== SECURITY =====
    { id: 'SEC-01', section: 'security', priority: 'critical', title: 'Все эндпоинты кроме /auth требуют токен',
      steps: 'curl без Authorization на /api/tenders, /api/subscriptions, /api/tenders/{id}',
      expected: '401 на каждом', module: 'Security' },
    { id: 'SEC-02', section: 'security', priority: 'high', title: 'SQL-инъекции в фильтрах',
      steps: 'GET /api/tenders?q=\' OR 1=1--',
      expected: 'Либо 400, либо без ошибки. Никаких 500 и утечки данных', module: 'Security' },
    { id: 'SEC-03', section: 'security', priority: 'high', title: 'XSS в названии тендера (поле из БД)',
      steps: 'Если есть способ залить тендер с <script>alert(1)</script> — открыть',
      expected: 'Скрипт НЕ выполняется, текст экранирован', module: 'Security' },
    { id: 'SEC-04', section: 'security', priority: 'medium', title: 'Хеширование паролей (bcrypt)',
      steps: 'Зайти в контейнер, посмотреть таблицу users',
      expected: 'Пароль хранится как $2b$... (bcrypt), не plaintext', module: 'Security' },
    { id: 'SEC-05', section: 'security', priority: 'medium', title: 'Admin эндпоинты только для admin-роли',
      steps: 'curl /api/admin/stats без admin-токена',
      expected: '403 Forbidden', module: 'Security' },
    { id: 'SEC-06', section: 'security', priority: 'medium', title: 'JWT signature проверяется',
      steps: 'Подделать токен (изменить payload, оставить header)',
      expected: '401', module: 'Security' },
    { id: 'SEC-07', section: 'security', priority: 'low', title: 'HTTPS редирект с HTTP',
      steps: 'curl -I http://tenders.ivoryhome.ru',
      expected: '301/302 на https://', module: 'Security' },
    { id: 'SEC-08', section: 'security', priority: 'low', title: 'HSTS заголовок',
      steps: 'curl -I https://tenders.ivoryhome.ru',
      expected: 'Strict-Transport-Security присутствует', module: 'Security' },

    // ===== PERFORMANCE =====
    { id: 'PERF-01', section: 'perf', priority: 'high', title: 'GET /api/tenders p95 < 500ms',
      steps: 'ab -n 100 -c 10 /api/tenders (или wrk)',
      expected: 'p95 < 500ms', module: 'Performance' },
    { id: 'PERF-02', section: 'perf', priority: 'medium', title: 'GET /api/tenders/{id} p95 < 800ms',
      steps: 'ab -n 100 -c 10 /api/tenders/<id>',
      expected: 'p95 < 800ms (AI добавляет время)', module: 'Performance' },
    { id: 'PERF-03', section: 'perf', priority: 'high', title: 'Лендинг / First Contentful Paint',
      steps: 'Chrome DevTools → Lighthouse → Performance',
      expected: 'FCP < 1.5s, LCP < 2.5s', module: 'Performance' },
    { id: 'PERF-04', section: 'perf', priority: 'medium', title: 'Дашборд с 20 тендерами < 2s',
      steps: 'Залогинен, открыть /dashboard',
      expected: 'Полная загрузка < 2s на нормальном соединении', module: 'Performance' },
    { id: 'PERF-05', section: 'perf', priority: 'low', title: 'Bundle size < 500KB',
      steps: 'DevTools → Network → JS',
      expected: 'Суммарный JS < 500KB', module: 'Performance' },
    { id: 'PERF-06', section: 'perf', priority: 'medium', title: 'AI-анализ не блокирует основной ответ',
      steps: 'GET /api/tenders/{id} с медленным AI (10+ сек)',
      expected: 'Ответ 200 приходит сразу, AI-анализ обновляется асинхронно (или возвращается placeholder)', module: 'Performance' }
  ];

  // ── STATE ───────────────────────────────────────────
  let state = loadState();
  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { console.error('Failed to load state:', e); }
    return { results: {}, bugs: [], tester: '', sessionStart: Date.now() };
  }
  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) { console.error('Failed to save state:', e); }
  }

  // ── TOAST ───────────────────────────────────────────
  function toast(msg, type = 'info') {
    const el = document.getElementById('qa-toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'qa-toast ' + type + ' show';
    setTimeout(() => { el.className = 'qa-toast ' + type; }, 2500);
  }

  // ── РЕНДЕР ВКЛАДОК И ТЕСТ-КЕЙСОВ ────────────────────
  function renderTabs() {
    const tabsEl = document.getElementById('qa-tabs');
    if (!tabsEl) return;
    tabsEl.innerHTML = SECTIONS.map((s, i) => {
      const count = TESTCASES.filter(t => t.section === s.id).length;
      const passed = TESTCASES.filter(t => t.section === s.id && state.results[t.id] === 'passed').length;
      return `<button class="qa-tab ${i === 0 ? 'active' : ''}" data-section="${s.id}">${s.icon} ${s.title} <span style="opacity:0.6">(${passed}/${count})</span></button>`;
    }).join('');

    tabsEl.querySelectorAll('.qa-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        tabsEl.querySelectorAll('.qa-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.querySelectorAll('.qa-tab-content').forEach(c => c.classList.remove('active'));
        const target = document.getElementById('tab-' + tab.dataset.section);
        if (target) target.classList.add('active');
      });
    });
  }

  function renderTestcases() {
    const container = document.getElementById('qa-tabs-content');
    if (!container) return;
    container.innerHTML = SECTIONS.map((s, i) => {
      const cases = TESTCASES.filter(t => t.section === s.id);
      return `<div class="qa-tab-content ${i === 0 ? 'active' : ''}" id="tab-${s.id}">${cases.map(renderTestcase).join('')}</div>`;
    }).join('');
    bindTestcaseActions();
  }

  function renderTestcase(tc) {
    const result = state.results[tc.id] || '';
    return `
      <div class="qa-testcase ${result}" data-tc-id="${tc.id}" data-section="${tc.section}">
        <div class="qa-testcase-header">
          <span class="qa-testcase-id">${tc.id}</span>
          <span class="qa-testcase-priority prio-${tc.priority}">${tc.priority}</span>
          <span class="qa-testcase-title">${escapeHtml(tc.title)}</span>
        </div>
        ${tc.pre ? `<div class="qa-testcase-meta"><div class="qa-testcase-meta-label">Предусловия</div><div class="qa-testcase-meta-value">${escapeHtml(tc.pre)}</div></div>` : ''}
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Шаги</div>
          <div class="qa-testcase-meta-value">${escapeHtml(tc.steps)}</div>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Ожидание</div>
          <div class="qa-testcase-meta-value">${escapeHtml(tc.expected)}</div>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Модуль</div>
          <div class="qa-testcase-meta-value">${escapeHtml(tc.module)}</div>
        </div>
        <div class="qa-testcase-actions">
          <button class="qa-btn btn-pass ${result === 'passed' ? 'active' : ''}" data-action="passed">✅ Pass</button>
          <button class="qa-btn btn-fail ${result === 'failed' ? 'active' : ''}" data-action="failed">❌ Fail</button>
          <button class="qa-btn btn-skip ${result === 'skipped' ? 'active' : ''}" data-action="skipped">⏭ Skip</button>
          <button class="qa-btn btn-block ${result === 'blocked' ? 'active' : ''}" data-action="blocked">🚫 Block</button>
          ${result && result !== '' ? `<button class="qa-btn" data-action="clear">↺ Сбросить</button>` : ''}
        </div>
      </div>
    `;
  }

  function bindTestcaseActions() {
    document.querySelectorAll('.qa-testcase-actions .qa-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const card = btn.closest('.qa-testcase');
        const tcId = card.dataset.tcId;
        const action = btn.dataset.action;
        if (action === 'clear') {
          delete state.results[tcId];
        } else {
          state.results[tcId] = action;
        }
        saveState();
        renderTestcases();
        renderProgress();
        renderTabs();
        filterTestcases(); // re-apply filter
        toast(action === 'clear' ? 'Сброшено' : 'Отмечено: ' + action, action === 'failed' ? 'error' : 'success');
      });
    });
  }

  // ── ПРОГРЕСС ────────────────────────────────────────
  function renderProgress() {
    const total = TESTCASES.length;
    const passed = Object.values(state.results).filter(r => r === 'passed').length;
    const failed = Object.values(state.results).filter(r => r === 'failed').length;
    const skipped = Object.values(state.results).filter(r => r === 'skipped').length;
    const blocked = Object.values(state.results).filter(r => r === 'blocked').length;
    const tested = passed + failed + skipped + blocked;
    const remaining = total - tested;

    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-passed').textContent = passed;
    document.getElementById('stat-failed').textContent = failed;
    document.getElementById('stat-skipped').textContent = skipped;
    document.getElementById('stat-blocked').textContent = blocked;
    document.getElementById('stat-remaining').textContent = remaining;
    document.getElementById('progress-bar').style.width = (tested / total * 100) + '%';
  }

  // ── ФИЛЬТРЫ ─────────────────────────────────────────
  window.filterTestcases = function () {
    const section = document.getElementById('filter-section').value;
    const status = document.getElementById('filter-status').value;
    const search = document.getElementById('filter-search').value.toLowerCase().trim();
    document.querySelectorAll('.qa-testcase').forEach(card => {
      let show = true;
      if (section !== 'all' && card.dataset.section !== section) show = false;
      if (status === 'untested' && state.results[card.dataset.tcId]) show = false;
      if (status !== 'all' && status !== 'untested' && state.results[card.dataset.tcId] !== status) show = false;
      if (search && !card.textContent.toLowerCase().includes(search)) show = false;
      card.style.display = show ? '' : 'none';
    });
  };

  // ── БАГ-РЕПОРТЫ ─────────────────────────────────────
  window.addBug = function () {
    const title = document.getElementById('bug-title').value.trim();
    if (!title) { toast('Заполни заголовок', 'error'); return; }
    const steps = document.getElementById('bug-steps').value.trim();
    const expected = document.getElementById('bug-expected').value.trim();
    const actual = document.getElementById('bug-actual').value.trim();
    if (!steps || !expected || !actual) { toast('Заполни шаги / ожидание / факт', 'error'); return; }

    const bug = {
      id: 'BUG-' + String(state.bugs.length + 1).padStart(3, '0'),
      createdAt: new Date().toISOString(),
      title,
      severity: document.getElementById('bug-severity').value,
      module: document.getElementById('bug-module').value,
      browser: document.getElementById('bug-browser').value.trim(),
      os: document.getElementById('bug-os').value.trim(),
      steps, expected, actual,
      logs: document.getElementById('bug-logs').value.trim(),
      screenshot: document.getElementById('bug-screenshot').files[0]?.name || null,
      status: 'open'
    };
    state.bugs.push(bug);
    saveState();
    renderBugs();
    clearBugForm();
    toast('✅ Дефект добавлен: ' + bug.id, 'success');
  };

  window.clearBugForm = function () {
    ['bug-title','bug-steps','bug-expected','bug-actual','bug-logs','bug-screenshot','bug-browser','bug-os']
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    document.getElementById('bug-severity').value = 'P1';
  };

  function renderBugs() {
    const list = document.getElementById('bugs-list');
    const count = document.getElementById('bugs-count');
    if (!list || !count) return;
    count.textContent = state.bugs.length;
    if (state.bugs.length === 0) {
      list.innerHTML = '<p class="text-muted">Дефектов пока нет. Заполни форму выше.</p>';
      return;
    }
    list.innerHTML = state.bugs.map(bug => `
      <div class="qa-testcase failed" style="margin-top:12px">
        <div class="qa-testcase-header">
          <span class="qa-testcase-id">${bug.id}</span>
          <span class="qa-testcase-priority prio-${bug.severity === 'P0' ? 'critical' : bug.severity === 'P1' ? 'high' : bug.severity === 'P2' ? 'medium' : 'low'}">${bug.severity}</span>
          <span class="qa-testcase-title">${escapeHtml(bug.title)}</span>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Создан</div>
          <div class="qa-testcase-meta-value">${new Date(bug.createdAt).toLocaleString('ru-RU')}</div>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Модуль</div>
          <div class="qa-testcase-meta-value">${escapeHtml(bug.module)}</div>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Окружение</div>
          <div class="qa-testcase-meta-value">${escapeHtml([bug.browser, bug.os].filter(Boolean).join(' / ') || '—')}</div>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Шаги</div>
          <div class="qa-testcase-meta-value">${escapeHtml(bug.steps)}</div>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Ожидание</div>
          <div class="qa-testcase-meta-value">${escapeHtml(bug.expected)}</div>
        </div>
        <div class="qa-testcase-meta">
          <div class="qa-testcase-meta-label">Факт</div>
          <div class="qa-testcase-meta-value">${escapeHtml(bug.actual)}</div>
        </div>
        ${bug.logs ? `<div class="qa-testcase-meta"><div class="qa-testcase-meta-label">Логи</div><div class="qa-testcase-meta-value"><pre style="white-space:pre-wrap;font-size:0.8rem;background:var(--bg-tertiary);padding:8px;border-radius:4px;margin:0">${escapeHtml(bug.logs)}</pre></div></div>` : ''}
        ${bug.screenshot ? `<div class="qa-testcase-meta"><div class="qa-testcase-meta-label">Скриншот</div><div class="qa-testcase-meta-value">${escapeHtml(bug.screenshot)}</div></div>` : ''}
        <div class="qa-testcase-actions">
          <button class="qa-btn" onclick="deleteBug('${bug.id}')">🗑 Удалить</button>
        </div>
      </div>
    `).join('');
  }

  window.deleteBug = function (id) {
    if (!confirm('Удалить ' + id + '?')) return;
    state.bugs = state.bugs.filter(b => b.id !== id);
    saveState();
    renderBugs();
    toast('Удалено', 'info');
  };

  // ── ЭКСПОРТ ОТЧЁТА ─────────────────────────────────
  function buildReport() {
    const total = TESTCASES.length;
    const passed = Object.values(state.results).filter(r => r === 'passed').length;
    const failed = Object.values(state.results).filter(r => r === 'failed').length;
    const skipped = Object.values(state.results).filter(r => r === 'skipped').length;
    const blocked = Object.values(state.results).filter(r => r === 'blocked').length;
    const tested = passed + failed + skipped + blocked;
    return {
      meta: {
        project: 'H025 TenderSearch',
        domain: 'https://tenders.ivoryhome.ru',
        tester: document.getElementById('tester-name')?.value || state.tester || 'unknown',
        browser: document.getElementById('tester-browser')?.value || '',
        os: document.getElementById('tester-os')?.value || '',
        sessionStart: new Date(state.sessionStart).toISOString(),
        sessionEnd: new Date().toISOString(),
        userAgent: navigator.userAgent
      },
      summary: {
        total, passed, failed, skipped, blocked, untested: total - tested,
        passRate: tested > 0 ? (passed / tested * 100).toFixed(1) : 0
      },
      sections: SECTIONS.map(s => {
        const cases = TESTCASES.filter(t => t.section === s.id);
        return {
          id: s.id,
          title: s.title,
          cases: cases.map(tc => ({
            id: tc.id,
            title: tc.title,
            priority: tc.priority,
            module: tc.module,
            result: state.results[tc.id] || 'untested',
            steps: tc.steps,
            expected: tc.expected,
            pre: tc.pre || null
          }))
        };
      }),
      bugs: state.bugs
    };
  }

  window.exportReport = function () {
    const report = buildReport();
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'qa-report-tendersearch-' + new Date().toISOString().slice(0, 10) + '-' + (report.meta.tester.replace(/\s+/g, '_') || 'unknown') + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast('📥 Отчёт скачан', 'success');
  };

  window.exportBugsOnly = function () {
    const report = {
      meta: {
        project: 'H025 TenderSearch',
        date: new Date().toISOString(),
        tester: document.getElementById('tester-name')?.value || 'unknown'
      },
      bugs: state.bugs
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bugs-tendersearch-' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast('📥 Баги скачаны', 'success');
  };

  window.copyReportToClipboard = async function () {
    const report = buildReport();
    const text = JSON.stringify(report, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      toast('📋 Скопировано в буфер', 'success');
    } catch (e) {
      // Fallback
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); toast('📋 Скопировано', 'success'); }
      catch (e2) { toast('Ошибка копирования', 'error'); }
      document.body.removeChild(ta);
    }
  };

  window.resetAll = function () {
    if (!confirm('Сбросить ВСЕ результаты и баги? Это необратимо.')) return;
    state = { results: {}, bugs: [], tester: state.tester, sessionStart: Date.now() };
    saveState();
    renderAll();
    toast('🔄 Сброс выполнен', 'info');
  };

  // ── HELPER ──────────────────────────────────────────
  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // ── TESTER INFO ─────────────────────────────────────
  function renderTesterInputs() {
    const envCard = document.querySelector('.qa-env-card');
    if (!envCard) return;
    const html = `
      <div class="qa-env-card">
        <h3>👤 Кто тестирует</h3>
        <div class="qa-env-grid">
          <div class="qa-env-item">
            <div class="qa-env-label">Имя тестировщика *</div>
            <input class="form-input" id="tester-name" placeholder="Сергей / Иван / QA-team" value="${escapeHtml(state.tester || '')}">
          </div>
          <div class="qa-env-item">
            <div class="qa-env-label">Браузер</div>
            <input class="form-input" id="tester-browser" placeholder="Chrome 124 / Firefox 126 / Safari 17.4">
          </div>
          <div class="qa-env-item">
            <div class="qa-env-label">ОС</div>
            <input class="form-input" id="tester-os" placeholder="macOS 14.5 / Windows 11 / Ubuntu 24.04">
          </div>
        </div>
        <p class="text-muted" style="margin-top:12px;font-size:0.85rem">Эти данные попадут в финальный JSON-отчёт. Заполни сразу — потом будет вшито в имя файла.</p>
      </div>
    `;
    envCard.insertAdjacentHTML('afterend', html);
    document.getElementById('tester-name').addEventListener('input', e => { state.tester = e.target.value; saveState(); });
    document.getElementById('tester-browser').addEventListener('change', e => saveState());
    document.getElementById('tester-os').addEventListener('change', e => saveState());
  }

  function renderAll() {
    renderTesterInputs();
    renderTabs();
    renderTestcases();
    renderProgress();
    renderBugs();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderAll);
  } else {
    renderAll();
  }
})();