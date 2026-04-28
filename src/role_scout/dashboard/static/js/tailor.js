(function () {
  'use strict';

  var currentHashId = null;

  function csrfToken() {
    var el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function escHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function getModal() {
    return document.getElementById('tailorModal');
  }

  function showLoading() {
    document.getElementById('tailor-modal-loading').hidden = false;
    document.getElementById('tailor-modal-error').hidden = true;
    document.getElementById('tailor-modal-result').hidden = true;
  }

  function showError(msg) {
    document.getElementById('tailor-modal-loading').hidden = true;
    document.getElementById('tailor-modal-error').hidden = false;
    document.getElementById('tailor-modal-error').textContent = msg;
    document.getElementById('tailor-modal-result').hidden = true;
  }

  function showResult(data, jobTitle, company) {
    document.getElementById('tailor-modal-loading').hidden = true;
    document.getElementById('tailor-modal-error').hidden = true;

    var badge = document.getElementById('tailor-modal-badge');
    badge.textContent = data.cached ? 'cached' : 'fresh';
    badge.className = 'badge me-2 ' + (data.cached ? 'bg-secondary' : 'bg-success');

    document.getElementById('tailor-modal-job-title').textContent = jobTitle + ' @ ' + company;
    document.getElementById('tailor-modal-summary').textContent = data.tailored_summary || '';

    var ul = document.getElementById('tailor-modal-bullets');
    ul.innerHTML = (data.tailored_bullets || [])
      .map(function (b) { return '<li class="mb-1">' + escHtml(b) + '</li>'; })
      .join('');

    document.getElementById('tailor-modal-keywords').textContent =
      (data.keywords_incorporated || []).join(', ');

    document.getElementById('tailor-modal-result').hidden = false;
  }

  function copyText(text) {
    navigator.clipboard.writeText(text).catch(function () {});
  }

  async function fetchTailor(hashId, force, jobTitle, company) {
    showLoading();
    try {
      var resp = await fetch('/api/tailor/' + hashId, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({ force: !!force }),
      });
      var json;
      try {
        json = await resp.json();
      } catch (_) {
        showError('Server returned HTTP ' + resp.status + ' (non-JSON). Check terminal.');
        return;
      }
      if (!resp.ok) {
        showError((json && json.error && json.error.message) || 'HTTP ' + resp.status);
        return;
      }
      showResult(json, jobTitle, company);
    } catch (e) {
      showError('Network error — is the server still running?');
    }
  }

  function openModal(hashId, jobTitle, company, force) {
    currentHashId = hashId;
    var modalEl = getModal();
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    document.getElementById('tailorModalLabel').textContent =
      'Tailored Resume — ' + company;
    modal.show();
    fetchTailor(hashId, force, jobTitle, company);
  }

  document.addEventListener('click', function (e) {
    // Tailor button on job row
    var tailorBtn = e.target.closest('.tailor-btn');
    if (tailorBtn) {
      var hashId = tailorBtn.getAttribute('data-hash-id');
      var row = tailorBtn.closest('tr');
      var jobTitle = row ? row.querySelector('td:first-child').textContent.trim() : '';
      var company = row ? row.getAttribute('data-company') || '' : '';
      openModal(hashId, jobTitle, company, false);
      return;
    }

    // Copy summary
    if (e.target.id === 'tailor-copy-summary') {
      var text = document.getElementById('tailor-modal-summary').textContent;
      copyText(text);
      e.target.textContent = 'Copied!';
      setTimeout(function () { e.target.textContent = 'Copy'; }, 1500);
      return;
    }

    // Copy bullets
    if (e.target.id === 'tailor-copy-bullets') {
      var items = document.querySelectorAll('#tailor-modal-bullets li');
      var lines = Array.from(items).map(function (li) { return '• ' + li.textContent; }).join('\n');
      copyText(lines);
      e.target.textContent = 'Copied!';
      setTimeout(function () { e.target.textContent = 'Copy all'; }, 1500);
      return;
    }

    // Refresh
    if (e.target.id === 'tailor-refresh-btn') {
      if (currentHashId) {
        var title = document.getElementById('tailor-modal-job-title').textContent;
        var parts = title.split(' @ ');
        openModal(currentHashId, parts[0] || '', parts[1] || '', true);
      }
      return;
    }
  });
})();
