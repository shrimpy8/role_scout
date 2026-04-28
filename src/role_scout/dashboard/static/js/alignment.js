(function () {
  'use strict';

  function csrfToken() {
    var el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function escHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function renderAlignment(container, data) {
    var al = data;
    var html = '';

    if (al.strong_matches && al.strong_matches.length) {
      html += '<div class="alignment-match">Strong Matches</div><ul class="alignment-list">';
      al.strong_matches.forEach(function (item) { html += '<li>' + escHtml(item) + '</li>'; });
      html += '</ul>';
    }
    if (al.reframing_opportunities && al.reframing_opportunities.length) {
      html += '<div class="alignment-reframe mt-2">Reframing Opportunities</div><ul class="alignment-list">';
      al.reframing_opportunities.forEach(function (item) { html += '<li>' + escHtml(item) + '</li>'; });
      html += '</ul>';
    }
    if (al.genuine_gaps && al.genuine_gaps.length) {
      html += '<div class="alignment-gap mt-2">Gaps</div><ul class="alignment-list">';
      al.genuine_gaps.forEach(function (item) { html += '<li>' + escHtml(item) + '</li>'; });
      html += '</ul>';
    }
    if (al.overall_take) {
      html += '<div class="alignment-overall mt-2">' + escHtml(al.overall_take) + '</div>';
    }
    container.innerHTML = html || '<div style="font-size:12px;color:var(--text-faint)">No alignment data returned.</div>';
  }

  async function runAlignment(btn) {
    var hashId = btn.getAttribute('data-hash-id');
    var isRerun = btn.textContent.trim().startsWith('↻');
    var resultEl = document.getElementById('alignment-result-' + hashId);
    if (!resultEl) return;

    var originalText = btn.textContent;
    btn.textContent = '…';
    btn.disabled = true;

    try {
      var resp = await fetch('/api/alignment/' + hashId, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({ force: isRerun }),
      });
      var json = await resp.json();

      if (!resp.ok) {
        resultEl.innerHTML = '<div style="font-size:12px;color:var(--red)">' +
          escHtml((json.error && json.error.message) || 'Alignment failed — try again') + '</div>';
        btn.textContent = originalText;
        btn.disabled = false;
        return;
      }

      var alignmentStr = json.data && json.data.jd_alignment;
      var parsed;
      try {
        parsed = JSON.parse(alignmentStr);
      } catch (_) {
        resultEl.innerHTML = '<div style="font-size:12px;color:var(--red)">Couldn\'t parse alignment result — try again</div>';
        btn.textContent = originalText;
        btn.disabled = false;
        return;
      }

      renderAlignment(resultEl, parsed);
      btn.textContent = '↻ Re-run';
      btn.disabled = false;

    } catch (_) {
      resultEl.innerHTML = '<div style="font-size:12px;color:var(--red)">Network error — is the server still running?</div>';
      btn.textContent = originalText;
      btn.disabled = false;
    }
  }

  function init() {
    document.addEventListener('click', function (e) {
      var btn = e.target.closest('.alignment-btn');
      if (btn) runAlignment(btn);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
