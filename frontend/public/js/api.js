/* TenderSearch API Client */
const API = (() => {
  const BASE = '/api';

  function getToken() {
    return localStorage.getItem('token');
  }

  function setToken(token) {
    localStorage.setItem('token', token);
  }

  function clearToken() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
  }

  function getUser() {
    try {
      return JSON.parse(localStorage.getItem('user'));
    } catch {
      return null;
    }
  }

  function setUser(user) {
    localStorage.setItem('user', JSON.stringify(user));
  }

  function isLoggedIn() {
    return !!getToken();
  }

  async function request(path, options = {}) {
    const url = `${BASE}${path}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };

    const token = getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const resp = await fetch(url, { ...options, headers });

    if (resp.status === 401) {
      clearToken();
      if (!window.location.pathname.includes('/login')) {
        window.location.href = '/login';
      }
      throw new Error('Unauthorized');
    }

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || 'Request failed');
    }

    return resp.json();
  }

  return {
    // Auth
    async register(email, password, name) {
      const data = await request('/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password, name }),
      });
      setToken(data.access_token);
      setUser(data.user);
      return data;
    },

    async login(email, password) {
      const data = await request('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      setToken(data.access_token);
      setUser(data.user);
      return data;
    },

    async getMe() {
      return request('/auth/me');
    },

    logout() {
      clearToken();
      window.location.href = '/';
    },

    // Tenders
    async getTenders(params = {}) {
      const q = new URLSearchParams(params).toString();
      return request(`/tenders${q ? '?' + q : ''}`);
    },

    async getTender(id) {
      return request(`/tenders/${id}`);
    },

    async getTenderDocuments(id) {
      return request(`/tenders/${id}/documents`);
    },

    // Subscriptions
    async createSubscription(data) {
      return request('/subscriptions', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async getSubscriptions() {
      return request('/subscriptions');
    },

    async deleteSubscription(id) {
      return request(`/subscriptions/${id}`, { method: 'DELETE' });
    },

    async getSubscriptionMatches(id) {
      return request(`/subscriptions/${id}/matches`);
    },

    // Tariffs
    async getTariffs() {
      return request('/tariffs');
    },

    // Admin
    async getStats() {
      return request('/admin/stats');
    },

    async getUsers(params = {}) {
      const q = new URLSearchParams(params).toString();
      return request(`/admin/users${q ? '?' + q : ''}`);
    },

    async getUser(id) {
      return request(`/admin/users/${id}`);
    },

    async updateUser(id, data) {
      return request(`/admin/users/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      });
    },

    async resetUserPassword(id) {
      return request(`/admin/users/${id}/reset-password`, { method: 'POST' });
    },

    // Tariffs (admin)
    async getAdminTariffs() {
      return request('/admin/tariffs');
    },

    async createTariff(data) {
      return request('/admin/tariffs', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async updateTariff(id, data) {
      return request(`/admin/tariffs/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      });
    },

    async deleteTariff(id) {
      return request(`/admin/tariffs/${id}`, { method: 'DELETE' });
    },

    // Admin settings (Platega)
    async getAdminSettings() {
      return request('/admin/settings');
    },

    async updateAdminSettings(data) {
      return request('/admin/settings', {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    // Activation guide
    async getActivationGuide() {
      return request('/admin/activation-guide');
    },

    // Helpers
    getToken,
    setToken,
    clearToken,
    getUser,
    setUser,
    isLoggedIn,
  };
})();
