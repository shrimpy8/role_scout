(function () {
  'use strict';

  var watchlist = [];

  function csrfToken() {
    var el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function escHtml(str) {
    return String(str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function escAttr(str) {
    return String(str).replace(/"/g, '&quot;');
  }

  function updateStars() {
    document.querySelectorAll('.watchlist-star').forEach(function (star) {
      var co = star.dataset.company;
      var watched = watchlist.some(function (c) { return c.toLowerCase() === co; });
      star.hidden = !watched;
    });
  }

  function renderWatchlist() {
    var container = document.getElementById('watchlist-list');
    if (!container) return;
    if (watchlist.length === 0) {
      container.innerHTML = '<div style="font-size:12px;color:var(--text-faint)">No watchlist companies yet.</div>';
    } else {
      container.innerHTML = watchlist.map(function (c) {
        return '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">' +
          '<span style="font-size:12px">' + escHtml(c) + '</span>' +
          '<button class="watchlist-remove" data-company="' + escAttr(c) + '" ' +
          'style="background:none;border:none;color:var(--red);cursor:pointer;font-size:14px;padding:0 4px" ' +
          'aria-label="Remove ' + escAttr(c) + ' from watchlist">×</button>' +
          '</div>';
      }).join('');
      container.querySelectorAll('.watchlist-remove').forEach(function (btn) {
        btn.addEventListener('click', function () { removeCompany(btn.dataset.company); });
      });
    }
    updateStars();
  }

  function showError(msg) {
    var el = document.getElementById('watchlist-error');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    setTimeout(function () { el.hidden = true; }, 4000);
  }

  async function addCompany(company) {
    if (!watchlist.includes(company)) watchlist.push(company);
    renderWatchlist();
    var input = document.getElementById('watchlist-input');
    if (input) input.value = '';

    try {
      var resp = await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ company: company }),
      });
      var data = await resp.json();
      if (!resp.ok) {
        watchlist = watchlist.filter(function (c) { return c !== company; });
        renderWatchlist();
        showError((data.error && data.error.message) || "Couldn't add company");
        return;
      }
      watchlist = data.watchlist || watchlist;
      renderWatchlist();
    } catch (_) {
      watchlist = watchlist.filter(function (c) { return c !== company; });
      renderWatchlist();
      showError("Couldn't reach the server");
    }
  }

  async function removeCompany(company) {
    var prev = watchlist.slice();
    watchlist = watchlist.filter(function (c) { return c !== company; });
    renderWatchlist();

    try {
      var resp = await fetch('/api/watchlist/' + encodeURIComponent(company), {
        method: 'DELETE',
        headers: { 'X-CSRFToken': csrfToken() },
      });
      var data = await resp.json();
      if (!resp.ok) {
        watchlist = prev;
        renderWatchlist();
        showError((data.error && data.error.message) || "Couldn't remove company");
        return;
      }
      watchlist = data.watchlist || watchlist;
      renderWatchlist();
    } catch (_) {
      watchlist = prev;
      renderWatchlist();
      showError("Couldn't reach the server");
    }
  }

  // Called by banner.js when watchlist revision changes
  window.rsRefreshWatchlist = async function () {
    try {
      var resp = await fetch('/api/watchlist');
      if (!resp.ok) return;
      var data = await resp.json();
      watchlist = data.watchlist || [];
      renderWatchlist();
    } catch (_) {}
  };

  // Allow banner.js to push a new list directly
  window.rsUpdateWatchlist = function (list) {
    watchlist = list || [];
    renderWatchlist();
  };

  function init() {
    // Seed from server-rendered list so the panel is populated on first load
    if (window.RS_CONFIG && Array.isArray(window.RS_CONFIG.watchlist)) {
      watchlist = window.RS_CONFIG.watchlist.slice();
    }

    var addBtn = document.getElementById('watchlist-add-btn');
    var input = document.getElementById('watchlist-input');

    if (addBtn) {
      addBtn.addEventListener('click', function () {
        var val = (input ? input.value : '').trim();
        if (!val) { showError('Company name cannot be blank'); return; }
        addCompany(val);
      });
    }

    if (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          var val = input.value.trim();
          if (!val) { showError('Company name cannot be blank'); return; }
          addCompany(val);
        }
      });
    }

    renderWatchlist();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
