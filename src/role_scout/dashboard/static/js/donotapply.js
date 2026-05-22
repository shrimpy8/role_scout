(function () {
  'use strict';

  var donotapply = [];
  var locked = [];

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

  function renderList() {
    var container = document.getElementById('donotapply-list');
    if (!container) return;
    var lockedItems = locked.map(function (c) {
      return '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">' +
        '<span style="font-size:12px;color:var(--text-muted)">' + escHtml(c) + '</span>' +
        '<span style="font-size:11px;color:var(--text-faint);padding:0 4px" title="Set via DONOTAPPLY_COMPANIES in .env">🔒</span>' +
        '</div>';
    });
    var userItems = donotapply.map(function (c) {
      return '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">' +
        '<span style="font-size:12px">' + escHtml(c) + '</span>' +
        '<button class="dna-remove" data-company="' + escAttr(c) + '" ' +
        'style="background:none;border:none;color:var(--red);cursor:pointer;font-size:14px;padding:0 4px" ' +
        'aria-label="Remove ' + escAttr(c) + ' from do-not-apply list">×</button>' +
        '</div>';
    });
    var all = lockedItems.concat(userItems);
    if (all.length === 0) {
      container.innerHTML = '<div style="font-size:12px;color:var(--text-faint)">No excluded companies yet.</div>';
    } else {
      container.innerHTML = all.join('');
      container.querySelectorAll('.dna-remove').forEach(function (btn) {
        btn.addEventListener('click', function () { removeCompany(btn.dataset.company); });
      });
    }
  }

  function showError(msg) {
    var el = document.getElementById('donotapply-error');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    setTimeout(function () { el.hidden = true; }, 4000);
  }

  async function addCompany(company) {
    if (!donotapply.includes(company)) donotapply.push(company);
    renderList();
    var input = document.getElementById('donotapply-input');
    if (input) input.value = '';

    try {
      var resp = await fetch('/api/donotapply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ company: company }),
      });
      var data = await resp.json();
      if (!resp.ok) {
        donotapply = donotapply.filter(function (c) { return c !== company; });
        renderList();
        showError((data.error && data.error.message) || "Couldn't add company");
        return;
      }
      var payload = (data.data) || {};
      donotapply = payload.donotapply || donotapply;
      if (Array.isArray(payload.locked)) locked = payload.locked;
      renderList();
    } catch (_) {
      donotapply = donotapply.filter(function (c) { return c !== company; });
      renderList();
      showError("Couldn't reach the server");
    }
  }

  async function removeCompany(company) {
    var prev = donotapply.slice();
    donotapply = donotapply.filter(function (c) { return c !== company; });
    renderList();

    try {
      var resp = await fetch('/api/donotapply/' + encodeURIComponent(company), {
        method: 'DELETE',
        headers: { 'X-CSRFToken': csrfToken() },
      });
      var data = await resp.json();
      if (!resp.ok) {
        donotapply = prev;
        renderList();
        showError((data.error && data.error.message) || "Couldn't remove company");
        return;
      }
      var payload = (data.data) || {};
      donotapply = payload.donotapply || donotapply;
      if (Array.isArray(payload.locked)) locked = payload.locked;
      renderList();
    } catch (_) {
      donotapply = prev;
      renderList();
      showError("Couldn't reach the server");
    }
  }

  function init() {
    if (window.RS_CONFIG && Array.isArray(window.RS_CONFIG.donotapply)) {
      donotapply = window.RS_CONFIG.donotapply.slice();
    }
    if (window.RS_CONFIG && Array.isArray(window.RS_CONFIG.donotapplyLocked)) {
      locked = window.RS_CONFIG.donotapplyLocked.slice();
    }

    var addBtn = document.getElementById('donotapply-add-btn');
    var input = document.getElementById('donotapply-input');

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

    renderList();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
