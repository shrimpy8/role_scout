(function () {
  'use strict';

  function escHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function csrfToken() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function getPanel(hashId) {
    return document.getElementById('tailor-' + hashId);
  }

  function getPanelRow(hashId) {
    return document.querySelector('tr.tailor-panel-row[data-for="' + hashId + '"]');
  }

  function showLoading(panel) {
    panel.querySelector('.tailor-loading').hidden = false;
    panel.querySelector('.tailor-result').hidden = true;
    panel.querySelector('.tailor-error').hidden = true;
  }

  function showError(panel, msg) {
    panel.querySelector('.tailor-loading').hidden = true;
    panel.querySelector('.tailor-result').hidden = true;
    const errEl = panel.querySelector('.tailor-error');
    errEl.textContent = msg;
    errEl.hidden = false;
  }

  function showResult(panel, data) {
    panel.querySelector('.tailor-loading').hidden = true;
    panel.querySelector('.tailor-error').hidden = true;

    const badge = panel.querySelector('.tailor-badge');
    badge.textContent = data.cached ? 'cached' : 'fresh';
    badge.className = 'badge ms-1 ' + (data.cached ? 'badge-cached' : 'badge-fresh');

    panel.querySelector('.tailor-summary-text').textContent = data.tailored_summary || '';

    const ul = panel.querySelector('.tailor-bullets-list');
    ul.innerHTML = (data.tailored_bullets || [])
      .map(b => '<li>' + escHtml(b) + '</li>')
      .join('');

    panel.querySelector('.tailor-keywords').textContent =
      (data.keywords_incorporated || []).join(', ');

    panel.querySelector('.tailor-result').hidden = false;
  }

  async function fetchTailor(hashId, force, panel) {
    showLoading(panel);
    try {
      const resp = await fetch('/api/tailor/' + hashId, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({ force: force }),
      });
      const json = await resp.json();
      if (!resp.ok) {
        const msg = json?.error?.message || 'Tailoring failed — server error.';
        showError(panel, msg);
        return;
      }
      showResult(panel, json);
    } catch (e) {
      showError(panel, 'Tailoring failed — network error, try again.');
    }
  }

  function copyText(text) {
    navigator.clipboard.writeText(text).catch(() => {});
  }

  document.addEventListener('click', function (e) {
    // Tailor button — toggle panel + fetch if not yet loaded
    const tailorBtn = e.target.closest('.tailor-btn');
    if (tailorBtn) {
      const hashId = tailorBtn.dataset.hashId;
      const panelRow = getPanelRow(hashId);
      const panel = getPanel(hashId);
      if (!panelRow || !panel) return;

      const isOpen = !panelRow.classList.contains('d-none');
      if (isOpen) {
        panelRow.classList.add('d-none');
        return;
      }

      panelRow.classList.remove('d-none');
      // Only fetch if result not already shown
      if (panel.querySelector('.tailor-result').hidden &&
          panel.querySelector('.tailor-error').hidden) {
        fetchTailor(hashId, false, panel);
      }
      return;
    }

    // Copy summary
    const copySum = e.target.closest('.copy-summary');
    if (copySum) {
      const panel = copySum.closest('.tailor-panel');
      const text = panel?.querySelector('.tailor-summary-text')?.textContent || '';
      copyText(text);
      copySum.textContent = 'Copied!';
      setTimeout(() => { copySum.textContent = 'Copy'; }, 1500);
      return;
    }

    // Copy bullets
    const copyBul = e.target.closest('.copy-bullets');
    if (copyBul) {
      const panel = copyBul.closest('.tailor-panel');
      const items = panel?.querySelectorAll('.tailor-bullets-list li') || [];
      const text = Array.from(items).map(li => '• ' + li.textContent).join('\n');
      copyText(text);
      copyBul.textContent = 'Copied!';
      setTimeout(() => { copyBul.textContent = 'Copy all'; }, 1500);
      return;
    }

    // Refresh tailor
    const refreshBtn = e.target.closest('.refresh-tailor');
    if (refreshBtn) {
      const hashId = refreshBtn.dataset.hashId;
      const panel = getPanel(hashId);
      if (panel) fetchTailor(hashId, true, panel);
      return;
    }
  });
})();
