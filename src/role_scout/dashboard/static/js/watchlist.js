(function () {
  'use strict';

  let watchlist = [];

  function csrfToken() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function renderWatchlist() {
    const container = document.getElementById('watchlist-list');
    if (!container) return;
    if (watchlist.length === 0) {
      container.innerHTML = '<div class="text-muted small">No watchlist companies yet. Add one to highlight their jobs with ★.</div>';
    } else {
      container.innerHTML = watchlist.map(function (c) {
        return `<div class="d-flex justify-content-between align-items-center mb-1">
          <span class="small">${escHtml(c)}</span>
          <button class="btn btn-sm btn-link text-danger p-0 watchlist-remove" data-company="${escAttr(c)}" aria-label="Remove ${escAttr(c)} from watchlist">×</button>
        </div>`;
      }).join('');
      container.querySelectorAll('.watchlist-remove').forEach(function (btn) {
        btn.addEventListener('click', function () { removeCompany(btn.dataset.company); });
      });
    }
    updateStars();
  }

  function updateStars() {
    document.querySelectorAll('.watchlist-star').forEach(function (star) {
      const co = star.dataset.company;
      const watched = watchlist.some(function (c) { return c.toLowerCase() === co; });
      star.hidden = !watched;
    });
  }

  function escHtml(str) {
    return String(str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function escAttr(str) {
    return String(str).replace(/"/g, '&quot;');
  }

  function showError(msg) {
    const el = document.getElementById('watchlist-error');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    setTimeout(function () { el.hidden = true; }, 4000);
  }

  async function addCompany(company) {
    // Optimistic add
    if (!watchlist.includes(company)) watchlist.push(company);
    renderWatchlist();
    const input = document.getElementById('watchlist-input');
    if (input) input.value = '';

    try {
      const resp = await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ company }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        watchlist = watchlist.filter(function (c) { return c !== company; });
        renderWatchlist();
        showError(data.error?.message || "Couldn't save — please retry");
        return;
      }
      watchlist = data.watchlist || watchlist;
      renderWatchlist();
    } catch (e) {
      watchlist = watchlist.filter(function (c) { return c !== company; });
      renderWatchlist();
      showError("Couldn't save — please retry");
    }
  }

  async function removeCompany(company) {
    // Optimistic remove
    const prev = watchlist.slice();
    watchlist = watchlist.filter(function (c) { return c !== company; });
    renderWatchlist();

    try {
      const resp = await fetch('/api/watchlist/' + encodeURIComponent(company), {
        method: 'DELETE',
        headers: { 'X-CSRFToken': csrfToken() },
      });
      const data = await resp.json();
      if (!resp.ok) {
        watchlist = prev;
        renderWatchlist();
        showError("Couldn't remove — please retry");
        return;
      }
      watchlist = data.watchlist || watchlist;
      renderWatchlist();
    } catch (e) {
      watchlist = prev;
      renderWatchlist();
      showError("Couldn't remove — please retry");
    }
  }

  function initWatchlist() {
    const addBtn = document.getElementById('watchlist-add-btn');
    const input = document.getElementById('watchlist-input');

    if (addBtn) {
      addBtn.addEventListener('click', function () {
        const val = (input?.value || '').trim();
        if (val) addCompany(val);
      });
    }

    if (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          const val = input.value.trim();
          if (val) addCompany(val);
        }
      });
    }
  }

  function initTailorButtons() {
    document.querySelectorAll('.tailor-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const hashId = btn.dataset.hashId;
        toggleTailor(hashId, false);
      });
    });

    document.querySelectorAll('.refresh-tailor').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const hashId = btn.dataset.hashId;
        toggleTailor(hashId, true);
      });
    });
  }

  async function toggleTailor(hashId, force) {
    const panelRow = document.querySelector(`tr.tailor-panel-row[data-for="${hashId}"]`);
    const panel = document.getElementById('tailor-' + hashId);
    if (!panelRow || !panel) return;

    panelRow.classList.remove('d-none');

    const loading = panel.querySelector('.tailor-loading');
    const result = panel.querySelector('.tailor-result');
    const errEl = panel.querySelector('.tailor-error');
    const btn = document.querySelector(`.tailor-btn[data-hash-id="${hashId}"]`);

    if (loading) loading.hidden = false;
    if (result) result.hidden = true;
    if (errEl) errEl.hidden = true;
    if (btn) btn.disabled = true;

    try {
      const resp = await fetch('/api/tailor/' + hashId, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ force: !!force }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        if (loading) loading.hidden = true;
        if (errEl) { errEl.textContent = 'Tailoring failed — try again'; errEl.hidden = false; }
        if (btn) btn.disabled = false;
        return;
      }
      if (loading) loading.hidden = true;
      if (result) {
        result.hidden = false;
        const badge = result.querySelector('.tailor-badge');
        if (badge) {
          badge.textContent = data.cached ? 'cached' : 'fresh';
          badge.className = 'badge ms-1 ' + (data.cached ? 'badge-cached' : 'badge-fresh');
        }
        const summaryEl = result.querySelector('.tailor-summary-text');
        if (summaryEl) summaryEl.textContent = data.tailored_summary || '';
        const bulletsList = result.querySelector('.tailor-bullets-list');
        if (bulletsList && Array.isArray(data.tailored_bullets)) {
          bulletsList.innerHTML = data.tailored_bullets.map(function (b) {
            return '<li>' + escHtml(b) + '</li>';
          }).join('');
        }
        const kwEl = result.querySelector('.tailor-keywords');
        if (kwEl && Array.isArray(data.keywords_incorporated)) {
          kwEl.textContent = data.keywords_incorporated.join(' · ');
        }
        result.querySelector('.copy-summary')?.addEventListener('click', function () {
          navigator.clipboard?.writeText(data.tailored_summary || '');
        });
        result.querySelector('.copy-bullets')?.addEventListener('click', function () {
          const text = (data.tailored_bullets || []).join('\n');
          navigator.clipboard?.writeText(text);
        });
      }
    } catch (e) {
      if (loading) loading.hidden = true;
      if (errEl) { errEl.textContent = 'Tailoring failed — try again'; errEl.hidden = false; }
    }
    if (btn) btn.disabled = false;
  }

  // Update watchlist from status poll
  window.rsUpdateWatchlist = function (list) {
    watchlist = list || [];
    renderWatchlist();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initWatchlist();
      initTailorButtons();
      renderWatchlist();
    });
  } else {
    initWatchlist();
    initTailorButtons();
    renderWatchlist();
  }
})();
