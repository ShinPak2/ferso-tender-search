/* TenderSearch Dashboard Logic */

const Dashboard = (() => {
  // ── Toast ──────────────────────────────────────────────────

  function showToast(message, type = 'info') {
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
  }

  // ── Helpers ────────────────────────────────────────────────

  function formatPrice(price) {
    if (price == null) return '—';
    return new Intl.NumberFormat('ru-RU', {
      style: 'currency',
      currency: 'RUB',
      maximumFractionDigits: 0,
    }).format(price);
  }

  function formatDate(dateStr) {
    if (!dateStr) return '—';
    const d = new Date(dateStr);
    return d.toLocaleDateString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    });
  }

  function formatDateTime(dateStr) {
    if (!dateStr) return '—';
    const d = new Date(dateStr);
    return `${formatDate(dateStr)} ${d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
  }

  // ── Auth Check ─────────────────────────────────────────────

  function requireAuth() {
    if (!API.isLoggedIn()) {
      window.location.href = '/login';
      return false;
    }
    return true;
  }

  function setAuthNav() {
    const authBtn = document.getElementById('auth-btn');
    const dashboardLink = document.getElementById('dashboard-link');
    if (!authBtn) return;

    if (API.isLoggedIn()) {
      const user = API.getUser();
      if (authBtn) {
        authBtn.textContent = 'Личный кабинет';
        authBtn.href = '/dashboard';
      }
      if (dashboardLink) dashboardLink.style.display = '';
    }
  }

  // ── Dashboard Home ─────────────────────────────────────────

  async function loadDashboardHome() {
    if (!requireAuth()) return;
    const user = API.getUser();

    if (!requireAuth()) return;
    const user = API.getUser();

    const userNameEl = document.getElementById('user-name');
    if (userNameEl) userNameEl.textContent = user?.name || user?.email || 'Пользователь';

    // Init keyword input with AI suggestions
    initKeywordInput();

    // Load stats with initial tenders
    loadStats();
    doSearch(); // Initial search
  }

  function initKeywordInput() {
    const input = document.getElementById('keyword-input');
    if (!input) return;

    input.addEventListener('input', () => {
      clearTimeout(suggestionTimer);
      const val = input.value.trim();
      if (val.length >= 3) {
        suggestionTimer = setTimeout(() => fetchAISuggestions(val), 500);
      } else {
        document.getElementById('ai-suggestions').innerHTML = '';
      }
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        addKeyword(input.value.trim());
        input.value = '';
        doSearch();
      }
    });
  }

  async function fetchAISuggestions(keyword) {
    const container = document.getElementById('ai-suggestions');
    if (!container) return;

    container.innerHTML = '<span class="ai-label"><span class="ai-spinner"></span> 🤖 AI подбирает похожие запросы...</span>';

    try {
      const res = await fetch(`/api/suggestions?keyword=${encodeURIComponent(keyword)}`);
      if (!res.ok) throw new Error('Failed');
      const data = await res.json();
      renderSuggestions(data.keywords || [], data.source);
    } catch (e) {
      container.innerHTML = '';
      const fallback = [
        `тендер ${keyword}`,
        `закупка ${keyword}`,
        `поставка ${keyword}`,
        `оказание услуг ${keyword}`,
        `выполнение работ ${keyword}`,
      ];
      renderSuggestions(fallback, 'fallback');
    }
  }

  function renderSuggestions(keywords, source) {
    const container = document.getElementById('ai-suggestions');
    if (!container) return;

    const filtered = keywords
      .filter(k => !activeKeywords.includes(k.toLowerCase()))
      .slice(0, 8);

    if (filtered.length === 0) {
      container.innerHTML = '';
      return;
    }

    const label = source === 'ai'
      ? '<span class="ai-label">🤖 AI-подсказки (нажмите чтобы добавить):</span>'
      : '<span class="ai-label">💡 Похожие запросы:</span>';

    container.innerHTML = label + filtered.map(k =>
      `<span class="ai-suggestion-chip" onclick="Dashboard.addKeyword('${escapeHtmlAttr(k)}')"><span class="plus">+</span> ${escapeHtml(k)}</span>`
    ).join('');
  }

  function addKeyword(keyword) {
    const kw = keyword.trim().toLowerCase();
    if (!kw || activeKeywords.includes(kw)) return;
    activeKeywords.push(kw);
    renderActiveFilters();
    document.getElementById('ai-suggestions').innerHTML = '';
    document.getElementById('keyword-input').value = '';
    doSearch();
  }

  function removeKeyword(keyword) {
    activeKeywords = activeKeywords.filter(k => k !== keyword);
    renderActiveFilters();
    doSearch();
  }

  function renderActiveFilters() {
    const container = document.getElementById('active-filters');
    if (!container) return;
    container.innerHTML = activeKeywords.map(k =>
      `<span class="filter-chip">${escapeHtml(k)} <span class="remove" onclick="Dashboard.removeKeyword('${escapeHtmlAttr(k)}')">×</span></span>`
    ).join('');
  }

  async function doSearch() {
    const empty = document.getElementById('initial-empty');
    const loading = document.getElementById('loading-area');
    const results = document.getElementById('tender-results');
    const noResults = document.getElementById('no-results');

    if (activeKeywords.length === 0) {
      empty?.classList.remove('hidden');
      loading?.classList.add('hidden');
      results?.classList.add('hidden');
      noResults?.classList.add('hidden');
      document.getElementById('results-count').textContent = '';
      document.getElementById('results-heading').textContent = '📋 Результаты поиска';
      return;
    }

    empty?.classList.add('hidden');
    loading?.classList.remove('hidden');
    results?.classList.add('hidden');
    noResults?.classList.add('hidden');

    try {
      const law = document.getElementById('filter-law')?.value || '';
      const region = document.getElementById('filter-region')?.value || '';
      const priceMin = document.getElementById('filter-price-min')?.value || '';
      const priceMax = document.getElementById('filter-price-max')?.value || '';

      const params = new URLSearchParams();
      params.set('q', activeKeywords.join(' '));
      params.set('page_size', '20');
      if (law) params.set('law_type', law);

      const data = await API.request(`/tenders?${params.toString()}`);
      const tenders = data.items || [];

      let filtered = tenders;
      if (region) {
        const r = region.toLowerCase();
        filtered = filtered.filter(t => (t.region || '').toLowerCase().includes(r));
      }
      if (priceMin) {
        filtered = filtered.filter(t => t.price >= parseFloat(priceMin));
      }
      if (priceMax) {
        filtered = filtered.filter(t => t.price <= parseFloat(priceMax));
      }

      loading?.classList.add('hidden');

      if (filtered.length === 0) {
        noResults?.classList.remove('hidden');
        document.getElementById('results-count').textContent = '';
        document.getElementById('results-heading').textContent = '📋 Результаты поиска';
      } else {
        results?.classList.remove('hidden');
        results.innerHTML = filtered.map(t => renderTenderCard(t)).join('');
        document.getElementById('results-count').textContent = `Найдено: ${filtered.length}`;
        document.getElementById('results-heading').textContent = `📋 Тендеры (${filtered.length})`;
      }
    } catch (e) {
      loading?.classList.add('hidden');
      showToast('Ошибка поиска: ' + e.message, 'error');
    }
  }

  function renderTenderCard(t) {
    const relevanceClass = t.ai_relevance >= 7 ? 'relevance-high' : t.ai_relevance >= 4 ? 'relevance-mid' : 'relevance-low';
    const lawBadge = t.law_type === '44-ФЗ' ? 'badge-44fz' : 'badge-223fz';
    return `
      <div class="tender-card" onclick="location.href='/dashboard/tender-detail.html?id=${t.id}'" style="cursor:pointer">
        <div class="tender-card-header">
          <a href="/dashboard/tender-detail.html?id=${t.id}" class="tender-title" onclick="event.stopPropagation()">${escapeHtml(t.title)}</a>
          ${t.ai_relevance ? `<span class="relevance-badge ${relevanceClass}">🤖 ${t.ai_relevance}/10</span>` : ''}
        </div>
        <div class="tender-meta">
          <span>🏢 ${escapeHtml(t.customer || '—')}</span>
          <span class="tender-price">${formatPrice(t.price)}</span>
          <span>📅 до ${formatDate(t.deadline)}</span>
          ${t.law_type ? `<span class="tender-badge ${lawBadge}">${escapeHtml(t.law_type)}</span>` : ''}
          ${t.region ? `<span>📍 ${escapeHtml(t.region)}</span>` : ''}
        </div>
      </div>
    `;
  }

  function saveAsSubscription() {
    if (activeKeywords.length === 0) {
      showToast('Добавьте ключевые слова для поиска', 'error');
      return;
    }
    window.location.href = `/dashboard/subscriptions.html?keywords=${encodeURIComponent(activeKeywords.join(','))}&law=${encodeURIComponent(document.getElementById('filter-law')?.value || '')}&region=${encodeURIComponent(document.getElementById('filter-region')?.value || '')}&price_min=${document.getElementById('filter-price-min')?.value || ''}&price_max=${document.getElementById('filter-price-max')?.value || ''}`;
  }

  async function loadStats() {
    try {
      const tenders = await API.request('/tenders?page_size=1');
      document.getElementById('stat-tenders').textContent = tenders.total || 0;

      const subs = await API.getSubscriptions();
      document.getElementById('stat-subscriptions').textContent = subs.length;
      document.getElementById('stat-active').textContent = tenders.total || 0;
      document.getElementById('stat-ai').textContent = subs.length > 0 ? subs.length * 3 : 0;
    } catch (e) {
      console.error('Stats load error:', e);
    }
  }

  // Legacy
  async function loadDashboardHome() { await initDashboardHome(); }
    const container = document.getElementById('recent-tenders');
    if (!container) return;

    if (tenders.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>Новых тендеров пока нет</p></div>';
      return;
    }

    container.innerHTML = tenders.map(t => `
      <div class="tender-item card" onclick="location.href='/dashboard/tenders.html?id=${t.id}'">
        <div class="tender-info">
          <div class="tender-title">${escapeHtml(t.title)}</div>
          <div class="tender-meta">
            <span>🏢 ${escapeHtml(t.customer || '—')}</span>
            <span>💰 ${formatPrice(t.price)}</span>
            <span>📅 ${formatDate(t.deadline)}</span>
            ${t.law_type ? `<span class="badge badge-purple">${escapeHtml(t.law_type)}</span>` : ''}
          </div>
        </div>
        ${t.ai_relevance ? `<span class="badge badge-blue">AI: ${t.ai_relevance}/10</span>` : ''}
      </div>
    `).join('');
  }

  function renderRecentMatches(matches) {
    const container = document.getElementById('recent-matches');
    if (!container) return;

    if (matches.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>Нет подобранных тендеров</p></div>';
      return;
    }

    container.innerHTML = matches.map(m => `
      <div class="card mb-1 flex-between" onclick="location.href='/dashboard/tenders.html?id=${m.tender_id}'" style="cursor:pointer">
        <div>
          <strong>${escapeHtml(m.tender_title)}</strong>
        </div>
        <span class="badge badge-green">${m.relevance_score || 0}%</span>
      </div>
    `).join('');
  }

  // ── Subscriptions Manager ──────────────────────────────────

  async function loadSubscriptions() {
    if (!requireAuth()) return;
    const container = document.getElementById('subscriptions-list');
    if (!container) return;

    try {
      const subs = await API.getSubscriptions();
      if (subs.length === 0) {
        container.innerHTML = '<div class="empty-state">🔍<h3>Нет подписок</h3><p>Создайте подписку для автоматического подбора тендеров</p></div>';
        return;
      }

      container.innerHTML = subs.map(s => {
        const keywords = JSON.parse(s.keywords || '[]');
        return `
          <div class="subscription-card card">
            <div class="sub-header">
              <h4>${escapeHtml(s.name)}</h4>
              <span class="badge badge-green">${s.match_count} совпадений</span>
            </div>
            ${keywords.length ? `<div class="sub-keywords">${keywords.map(k => `<span class="sub-keyword">${escapeHtml(k)}</span>`).join('')}</div>` : ''}
            <div class="tender-meta">
              ${s.law_type ? `<span>📋 ${escapeHtml(s.law_type)}</span>` : ''}
              ${s.price_min ? `<span>От ${formatPrice(s.price_min)}</span>` : ''}
              ${s.price_max ? `<span>До ${formatPrice(s.price_max)}</span>` : ''}
              ${s.region ? `<span>📍 ${escapeHtml(s.region)}</span>` : ''}
            </div>
            <div class="sub-actions">
              <button class="btn btn-sm btn-secondary" onclick="Dashboard.viewMatches('${s.id}')">Смотреть тендеры</button>
              <button class="btn btn-sm btn-ghost" onclick="Dashboard.deleteSubscription('${s.id}')">Удалить</button>
            </div>
          </div>
        `;
      }).join('');
    } catch (e) {
      container.innerHTML = `<div class="empty-state"><p>Ошибка загрузки: ${e.message}</p></div>`;
    }
  }

  async function createSubscription(event) {
    event.preventDefault();
    const form = event.target;
    const data = {
      name: form.name.value,
      keywords: form.keywords.value.split(',').map(k => k.trim()).filter(Boolean),
      law_type: form.law_type.value || null,
      price_min: form.price_min.value ? parseFloat(form.price_min.value) : null,
      price_max: form.price_max.value ? parseFloat(form.price_max.value) : null,
      region: form.region.value || null,
    };

    try {
      await API.createSubscription(data);
      showToast('Подписка создана', 'success');
      form.reset();
      loadSubscriptions();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function deleteSubscription(id) {
    if (!confirm('Удалить подписку?')) return;
    try {
      await API.deleteSubscription(id);
      showToast('Подписка удалена', 'success');
      loadSubscriptions();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function viewMatches(subId) {
    window.location.href = `/dashboard/tenders.html?subscription=${subId}`;
  }

  // ── Tenders List ───────────────────────────────────────────

  async function loadTenders(page = 1) {
    const container = document.getElementById('tenders-table-body');
    const pagination = document.getElementById('tenders-pagination');
    if (!container) return;

    try {
      const params = new URLSearchParams(window.location.search);
      const query = {};
      if (params.get('q')) query.q = params.get('q');
      if (params.get('law_type')) query.law_type = params.get('law_type');
      if (params.get('subscription')) {
        // Load matches for subscription
        const matches = await API.getSubscriptionMatches(params.get('subscription'));
        renderMatchTendersTable(container, matches, pagination);
        return;
      }
      query.page = page;
      query.page_size = 20;

      const data = await API.getTenders(query);
      if (!data.items || data.items.length === 0) {
        container.innerHTML = '<tr><td colspan="5" class="text-center">Тендеры не найдены</td></tr>';
        pagination.innerHTML = '';
        return;
      }

      container.innerHTML = data.items.map(t => `
        <tr onclick="location.href='/dashboard/tender-detail.html?id=${t.id}'" style="cursor:pointer">
          <td><strong>${escapeHtml(t.title).substring(0, 80)}${t.title.length > 80 ? '...' : ''}</strong></td>
          <td>${escapeHtml(t.customer || '—')}</td>
          <td>${formatPrice(t.price)}</td>
          <td>${formatDate(t.deadline)}</td>
          <td><span class="badge badge-purple">${escapeHtml(t.law_type || '—')}</span></td>
        </tr>
      `).join('');

      // Pagination
      if (pagination && data.pages > 1) {
        pagination.innerHTML = `
          <div class="flex gap-1 mt-2" style="justify-content:center">
            ${Array.from({length: data.pages}, (_, i) => `
              <button class="btn btn-sm ${i+1 === page ? 'btn-primary' : 'btn-secondary'}"
                onclick="Dashboard.loadTenders(${i+1})">${i+1}</button>
            `).join('')}
          </div>
        `;
      } else if (pagination) {
        pagination.innerHTML = '';
      }
    } catch (e) {
      container.innerHTML = `<tr><td colspan="5" class="text-center text-danger">Ошибка: ${e.message}</td></tr>`;
    }
  }

  function renderMatchTendersTable(container, matches, pagination) {
    if (matches.length === 0) {
      container.innerHTML = '<tr><td colspan="5" class="text-center">Нет подобранных тендеров</td></tr>';
      pagination.innerHTML = '';
      return;
    }
    container.innerHTML = matches.map(m => `
      <tr onclick="location.href='/dashboard/tender-detail.html?id=${m.tender_id}'" style="cursor:pointer">
        <td><strong>${escapeHtml(m.tender_title).substring(0, 80)}${m.tender_title.length > 80 ? '...' : ''}</strong></td>
        <td>${escapeHtml(m.tender_customer || '—')}</td>
        <td>${formatPrice(m.tender_price)}</td>
        <td>${formatDate(m.tender_deadline)}</td>
        <td><span class="badge badge-green">${m.relevance_score || 0}%</span></td>
      </tr>
    `).join('');
    pagination.innerHTML = '';
  }

  // ── Tender Detail ──────────────────────────────────────────

  async function loadTenderDetail() {
    if (!requireAuth()) return;
    const params = new URLSearchParams(window.location.search);
    const id = params.get('id');
    if (!id) {
      document.getElementById('tender-content').innerHTML = '<div class="empty-state"><p>Тендер не указан</p></div>';
      return;
    }

    try {
      const tender = await API.getTender(id);
      renderTenderDetail(tender);
      renderAIAnalysis(tender);
    } catch (e) {
      document.getElementById('tender-content').innerHTML =
        `<div class="empty-state"><p class="text-danger">Ошибка: ${e.message}</p></div>`;
    }
  }

  function renderTenderDetail(tender) {
    const container = document.getElementById('tender-content');
    if (!container) return;

    container.innerHTML = `
      <div class="tender-detail-header">
        <a href="/dashboard/tenders.html" class="btn btn-ghost btn-sm mb-2">← Назад к тендерам</a>
        <h2>${escapeHtml(tender.title)}</h2>
        <div class="tender-detail-meta">
          <span class="badge badge-purple">${escapeHtml(tender.law_type || '—')}</span>
          <span class="badge badge-blue">${escapeHtml(tender.status || 'active')}</span>
          ${tender.region ? `<span class="badge badge-orange">📍 ${escapeHtml(tender.region)}</span>` : ''}
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card card">
          <div class="stat-value">${formatPrice(tender.price)}</div>
          <div class="stat-label">Стоимость</div>
        </div>
        <div class="stat-card card">
          <div class="stat-value">${formatDate(tender.deadline)}</div>
          <div class="stat-label">Дедлайн</div>
        </div>
        <div class="stat-card card">
          <div class="stat-value">${escapeHtml(tender.customer || '—')}</div>
          <div class="stat-label">Заказчик</div>
        </div>
        <div class="stat-card card">
          <div class="stat-value">${formatDate(tender.published_at)}</div>
          <div class="stat-label">Опубликован</div>
        </div>
      </div>

      <div class="card mt-3">
        <h3>Описание</h3>
        <p>${escapeHtml(tender.description || 'Описание отсутствует')}</p>
      </div>

      ${tender.documents && tender.documents.length > 0 ? `
        <div class="card mt-2">
          <h3>Документы (${tender.documents.length})</h3>
          <ul style="list-style:none;padding:0">
            ${tender.documents.map(d => `
              <li style="padding:8px 0;border-bottom:1px solid var(--border)">
                📄 ${escapeHtml(d.name)}
                ${d.file_url ? `<a href="${escapeHtml(d.file_url)}" target="_blank" class="btn btn-sm btn-ghost" style="margin-left:12px">Скачать</a>` : ''}
              </li>
            `).join('')}
          </ul>
        </div>
      ` : ''}
    `;
  }

  function renderAIAnalysis(tender) {
    const container = document.getElementById('ai-analysis');
    if (!container) return;

    if (!tender.ai_analysis) {
      container.innerHTML = `
        <div class="ai-analysis-block">
          <h3>🤖 AI-анализ</h3>
          <div class="loading"><span class="spinner"></span> Анализируем тендер...</div>
        </div>`;
      return;
    }

    const relevanceColor = tender.ai_relevance >= 7 ? 'var(--accent-green)' :
                          tender.ai_relevance >= 4 ? 'var(--accent-orange)' : 'var(--accent-red)';

    container.innerHTML = `
      <div class="ai-analysis-block">
        <h3>🤖 AI-анализ</h3>
        <div class="ai-score">
          <span style="font-weight:700">Релевантность:</span>
          <span style="font-size:1.5rem;font-weight:800;color:${relevanceColor}">${tender.ai_relevance}/10</span>
          <div class="ai-score-bar">
            <div class="ai-score-fill" style="width:${tender.ai_relevance * 10}%;background:${relevanceColor}"></div>
          </div>
        </div>
        <p><strong>Анализ:</strong> ${escapeHtml(tender.ai_analysis || '—')}</p>
        <p><strong>⚠️ Риски:</strong> ${escapeHtml(tender.ai_risks || '—')}</p>
        <p><strong>💡 Рекомендация:</strong> ${escapeHtml(tender.ai_recommendation || '—')}</p>
      </div>
    `;
  }

  // ── Profile ────────────────────────────────────────────────

  async function loadProfile() {
    if (!requireAuth()) return;
    try {
      const user = await API.getMe();
      const form = document.getElementById('profile-info');
      if (form) {
        form.innerHTML = `
          <div class="form-group">
            <label>Email</label>
            <input class="form-input" value="${escapeHtml(user.email)}" readonly>
          </div>
          <div class="form-group">
            <label>Имя</label>
            <input class="form-input" value="${escapeHtml(user.name || '')}" readonly>
          </div>
          <div class="form-group">
            <label>Компания</label>
            <input class="form-input" value="${escapeHtml(user.company || '')}" readonly>
          </div>
          <div class="form-group">
            <label>Тариф</label>
            <input class="form-input" value="${escapeHtml(user.tariff || 'free')}" readonly>
          </div>
          <div class="form-group">
            <label>Лимит тендеров</label>
            <input class="form-input" value="${user.tenders_viewed_this_month || 0} / ${user.monthly_limit || 10}" readonly>
          </div>
        `;
      }
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // ── Plan / Billing ─────────────────────────────────────────

  async function loadPlan() {
    if (!requireAuth()) return;
    try {
      const tariffs = await API.getTariffs();
      const user = API.getUser();
      const container = document.getElementById('plan-tariffs');
      if (!container) return;

      container.innerHTML = tariffs.map(t => `
        <div class="pricing-card card ${t.name === 'pro' ? 'popular' : ''} ${user?.tariff === t.name ? 'card-highlight' : ''}">
          <h3>${escapeHtml(t.display_name)}</h3>
          <div class="pricing-price">${t.price_monthly === 0 ? '0' : t.price_monthly.toLocaleString('ru-RU')} ₽</div>
          <div class="pricing-period">/ месяц</div>
          <ul class="pricing-features">
            <li>${t.tender_limit} тендеров/мес</li>
            <li>${t.subscription_limit} подписок</li>
            <li>AI-анализ: ${escapeHtml(t.ai_analysis_type)}</li>
          </ul>
          ${user?.tariff === t.name
            ? '<button class="btn btn-secondary" disabled>Текущий тариф</button>'
            : `<button class="btn btn-primary" onclick="Dashboard.upgradePlan('${t.name}')">Выбрать</button>`
          }
        </div>
      `).join('');
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function upgradePlan(tariffName) {
    try {
      await API.request('/billing/create-payment', {
        method: 'POST',
        body: JSON.stringify({ tariff_name: tariffName }),
      });
      showToast(`Заявка на тариф отправлена`, 'success');
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // ── Auth Forms ─────────────────────────────────────────────

  function initAuthForms() {
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
      loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = loginForm.querySelector('button[type="submit"]');
        btn.disabled = true;
        btn.textContent = 'Входим...';
        try {
          await API.login(loginForm.email.value, loginForm.password.value);
          window.location.href = '/dashboard';
        } catch (err) {
          document.getElementById('login-error').textContent = err.message;
          document.getElementById('login-error').style.display = 'block';
        } finally {
          btn.disabled = false;
          btn.textContent = 'Войти';
        }
      });
    }

    const registerForm = document.getElementById('register-form');
    if (registerForm) {
      registerForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = registerForm.querySelector('button[type="submit"]');
        const pw = registerForm.password.value;
        const pw2 = registerForm.password_confirm.value;

        if (pw !== pw2) {
          document.getElementById('register-error').textContent = 'Пароли не совпадают';
          document.getElementById('register-error').style.display = 'block';
          return;
        }

        btn.disabled = true;
        btn.textContent = 'Регистрируем...';
        try {
          await API.register(
            registerForm.email.value,
            pw,
            registerForm.name.value,
          );
          window.location.href = '/dashboard';
        } catch (err) {
          document.getElementById('register-error').textContent = err.message;
          document.getElementById('register-error').style.display = 'block';
        } finally {
          btn.disabled = false;
          btn.textContent = 'Зарегистрироваться';
        }
      });
    }
  }

  // ── Utility ────────────────────────────────────────────────

  function escapeHtmlAttr(text) {
    if (!text) return '';
    return text.replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ── Init ───────────────────────────────────────────────────

  function init() {
    setAuthNav();
    initAuthForms();
  }

  return {
    init,
    showToast,
    formatPrice,
    formatDate,
    escapeHtml,
    initDashboardHome,
    loadDashboardHome,
    addKeyword,
    removeKeyword,
    doSearch,
    saveAsSubscription,
    loadSubscriptions,
    createSubscription,
    deleteSubscription,
    viewMatches,
    loadTenders,
    loadTenderDetail,
    loadProfile,
    loadPlan,
    upgradePlan,
  };
})();

document.addEventListener('DOMContentLoaded', () => Dashboard.init());
