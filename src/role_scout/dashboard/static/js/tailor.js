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
        body: JSON.stringify({ force: !!force }),
      });
      let json = null;
      try {
        json = await resp.json();
      } catch (_) {
        showError(panel, 'Tailoring failed — server returned HTTP ' + resp.status + '. Check terminal for details.');
        return;
      }
      if (!resp.ok) {
        showError(panel, (json && json.error && json.error.message) || ('Tailoring failed — HTTP ' + resp.status + '.'));
        return;
      }
      showResult(panel, json);
    } catch (e) {
      showError(panel, 'Tailoring failed — could not reach server. Is it still running?');
    }
  }

  function copyText(text) {
    navigator.clipboard.writeText(text).catch(function () {});
  }

  document.addEventListener('click', function (e) {
    // Tailor button — use DOM traversal to find paired panel row
    var tailorBtn = e.target.closest('.tailor-btn');
    if (tailorBtn) {
      var hashId = tailorBtn.getAttribute('data-hash-id');
      var jobRow = tailorBtn.closest('tr');
      if (!jobRow) return;
      var panelRow = jobRow.nextElementSibling;
      if (!panelRow || !panelRow.classList.contains('tailor-panel-row')) return;
      var panel = panelRow.querySelector('.tailor-panel');
      if (!panel) return;

      var isOpen = !panelRow.classList.contains('d-none');
      if (isOpen) {
        panelRow.classList.add('d-none');
        return;
      }

      panelRow.classList.remove('d-none');
      var resultShown = !panel.querySelector('.tailor-result').hidden;
      var errorShown = !panel.querySelector('.tailor-error').hidden;
      if (!resultShown && !errorShown) {
        fetchTailor(hashId, false, panel);
      }
      return;
    }

    // Copy summary
    var copySum = e.target.closest('.copy-summary');
    if (copySum) {
      var panel2 = copySum.closest('.tailor-panel');
      var text = (panel2 && panel2.querySelector('.tailor-summary-text').textContent) || '';
      copyText(text);
      copySum.textContent = 'Copied!';
      setTimeout(function () { copySum.textContent = 'Copy'; }, 1500);
      return;
    }

    // Copy bullets
    var copyBul = e.target.closest('.copy-bullets');
    if (copyBul) {
      var panel3 = copyBul.closest('.tailor-panel');
      var items = panel3 ? panel3.querySelectorAll('.tailor-bullets-list li') : [];
      var lines = Array.from(items).map(function (li) { return '• ' + li.textContent; }).join('\n');
      copyText(lines);
      copyBul.textContent = 'Copied!';
      setTimeout(function () { copyBul.textContent = 'Copy all'; }, 1500);
      return;
    }

    // Refresh tailor
    var refreshBtn = e.target.closest('.refresh-tailor');
    if (refreshBtn) {
      var hashId2 = refreshBtn.getAttribute('data-hash-id');
      var jobRow2 = refreshBtn.closest('tr');
      var panelRow2 = jobRow2 && jobRow2.previousElementSibling;
      // refresh-tailor is inside the panel row, find panel from parent
      var panel4 = refreshBtn.closest('.tailor-panel');
      if (panel4) fetchTailor(hashId2, true, panel4);
      return;
    }
  });
})();
