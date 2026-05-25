(function () {
  'use strict';

  // ---- State ---------------------------------------------------------------
  var _results = [];   // array of AnalysisResult dicts from /api/ingest/analyze

  // ---- DOM helpers ---------------------------------------------------------
  function $id(id) { return document.getElementById(id); }

  function csrfToken() {
    var el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
  }

  function show(el) { if (el) el.hidden = false; }
  function hide(el) { if (el) el.hidden = true; }

  // ---- Confidence badge ----------------------------------------------------
  function confBadge(pct) {
    var cls = pct >= 80 ? 'conf-high' : pct >= 60 ? 'conf-mid' : 'conf-low';
    return '<span class="conf-badge ' + cls + '">' + pct + '%</span>';
  }

  // ---- Match badge ---------------------------------------------------------
  function matchBadge(pct) {
    var cls = pct >= 70 ? 'high' : pct >= 50 ? 'mid' : 'low';
    return '<span class="match-badge-sm ' + cls + '">' + pct + '%</span>';
  }

  // ---- Status badge --------------------------------------------------------
  var _STATUS_LABELS = {
    new: 'New', reviewed: 'Reviewed', applied: 'Applied',
    rejected: 'Rejected', not_a_fit: 'Not a Fit', not_available: 'Not Available',
  };
  var _SOURCE_LABELS = {
    linkedin: 'LinkedIn', google_jobs: 'Google Jobs', trueup: 'TrueUp', manual: 'Manual',
  };

  function statusBadge(result) {
    if (result.status === 'ready') {
      if (result.already_in_db) {
        var ex = result.existing_job;
        if (ex) {
          var src = _SOURCE_LABELS[ex.source] || ex.source;
          var st  = _STATUS_LABELS[ex.status]  || ex.status;
          return (
            '<span class="status-badge status-duplicate" title="This job is already in your database">' +
            'In DB</span> ' +
            '<span style="font-size:11px;color:var(--text-muted);">' +
            _esc(src) + ' &middot; ' + _esc(st) + ' &middot; ' + ex.match_pct + '%' +
            '</span>'
          );
        }
        // seen_hashes hit but not in qualified_jobs (below-threshold or expired)
        return '<span class="status-badge status-duplicate" title="Previously seen but below score threshold">Seen before</span>';
      }
      return '<span class="status-badge status-ready">Ready</span>';
    }
    if (result.status === 'thin') {
      return '<span class="status-badge status-thin">Content thin — paste JD below</span>';
    }
    return '<span class="status-badge status-failed">Failed</span>';
  }

  // ---- Render results table ------------------------------------------------
  function renderResults(results) {
    var tbody = $id('results-tbody');
    tbody.innerHTML = '';

    results.forEach(function (r, idx) {
      var job = r.scored_job || {};
      var company = job.company || '—';
      var title = job.title || '—';
      var location = job.location || '—';
      var matchPct = (job.match_pct != null) ? job.match_pct : null;

      var checkCell = '';
      if (r.status === 'ready') {
        checkCell = '<input type="checkbox" class="result-chk" data-idx="' + idx + '" checked>';
      }

      var matchCell = matchPct != null ? matchBadge(matchPct) : '<span style="color:var(--text-ghost)">—</span>';

      var companyTitle = '<strong>' + _esc(company) + '</strong><br><span style="color:var(--text-muted);font-size:12px;">' + _esc(title) + '</span>';
      // URL as external-link icon — target="_blank" always opens a new tab
      companyTitle += '&nbsp;<a class="result-url-link" href="' + _esc(r.url) + '" target="_blank" rel="noopener noreferrer" title="' + _esc(r.url) + '">↗</a>';

      // Thin URL: show paste area
      if (r.status === 'thin') {
        companyTitle += '<br><textarea class="manual-text-area" data-url="' + _esc(r.url) + '" placeholder="Paste JD text here for this URL…"></textarea>';
        companyTitle += '<br><button class="reanalyze-btn" data-url="' + _esc(r.url) + '" data-idx="' + idx + '">↻ Re-analyze this URL</button>';
      }

      // Failed URL: show optional paste
      if (r.status === 'failed') {
        var errMsg = r.error_msg ? ' (' + _esc(r.error_msg) + ')' : '';
        companyTitle += '<br><small style="color:var(--red)">Fetch failed' + errMsg + '</small>';
        companyTitle += '<br><textarea class="manual-text-area" data-url="' + _esc(r.url) + '" placeholder="Optionally paste JD text to retry…"></textarea>';
        companyTitle += '<br><button class="reanalyze-btn" data-url="' + _esc(r.url) + '" data-idx="' + idx + '">↻ Re-analyze this URL</button>';
      }

      var row = '<tr data-idx="' + idx + '">' +
        '<td>' + checkCell + '</td>' +
        '<td>' + companyTitle + '</td>' +
        '<td>' + _esc(location) + '</td>' +
        '<td>' + matchCell + '</td>' +
        '<td>' + confBadge(r.confidence_pct || 0) + '</td>' +
        '<td>' + statusBadge(r) + '</td>' +
        '</tr>';

      tbody.insertAdjacentHTML('beforeend', row);
    });

    updateSelCount();
  }

  // ---- Selection count -----------------------------------------------------
  function updateSelCount() {
    var chks = document.querySelectorAll('.result-chk:checked');
    var total = document.querySelectorAll('.result-chk');
    var el = $id('sel-count');
    if (el) el.textContent = chks.length + ' of ' + total.length + ' selected';
  }

  // ---- Collect selected jobs -----------------------------------------------
  function getSelectedJobs() {
    var jobs = [];
    document.querySelectorAll('.result-chk:checked').forEach(function (chk) {
      var idx = parseInt(chk.getAttribute('data-idx'), 10);
      var r = _results[idx];
      if (r && r.status === 'ready' && r.scored_job) {
        jobs.push(r.scored_job);
      }
    });
    return jobs;
  }

  function getAllReadyJobs() {
    return _results
      .filter(function (r) { return r.status === 'ready' && r.scored_job; })
      .map(function (r) { return r.scored_job; });
  }

  // ---- Analyze -------------------------------------------------------------
  function doAnalyze(urls, manualTexts) {
    var analyzeBtn = $id('analyze-btn');
    analyzeBtn.disabled = true;
    hide($id('results-wrap'));
    hide($id('success-msg'));
    hide($id('error-msg'));
    show($id('loading-wrap'));

    fetch('/api/ingest/analyze', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify({ urls: urls, manual_texts: manualTexts }),
    })
      .then(function (resp) { return resp.json(); })
      .then(function (json) {
        hide($id('loading-wrap'));
        analyzeBtn.disabled = false;

        if (json.error) {
          var errEl = $id('error-msg');
          errEl.textContent = json.error.message || 'Analysis failed';
          show(errEl);
          return;
        }

        _results = (json.data && json.data.results) ? json.data.results : [];
        renderResults(_results);
        show($id('results-wrap'));
      })
      .catch(function (err) {
        hide($id('loading-wrap'));
        analyzeBtn.disabled = false;
        var errEl = $id('error-msg');
        errEl.textContent = 'Network error — ' + err.message;
        show(errEl);
      });
  }

  // ---- Confirm -------------------------------------------------------------
  function doConfirm(jobs) {
    if (!jobs.length) {
      alert('No ready jobs selected.');
      return;
    }

    var confirmBtn = $id('confirm-btn');
    var confirmAllBtn = $id('confirm-all-btn');
    confirmBtn.disabled = true;
    if (confirmAllBtn) confirmAllBtn.disabled = true;

    fetch('/api/ingest/confirm', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify({ jobs: jobs }),
    })
      .then(function (resp) { return resp.json(); })
      .then(function (json) {
        confirmBtn.disabled = false;
        if (confirmAllBtn) confirmAllBtn.disabled = false;

        if (json.error) {
          var errEl = $id('error-msg');
          errEl.textContent = json.error.message || 'Ingest failed';
          show(errEl);
          return;
        }

        var ingested = json.data && json.data.ingested != null ? json.data.ingested : '?';
        var skipped = json.data && json.data.skipped != null ? json.data.skipped : 0;
        var msg = ingested + ' job' + (ingested !== 1 ? 's' : '') + ' ingested';
        if (skipped > 0) msg += ' (' + skipped + ' already in DB, skipped)';
        msg += '. <a href="/?status=new">View in dashboard →</a>';

        var successEl = $id('success-msg');
        successEl.innerHTML = msg;
        show(successEl);
        hide($id('results-wrap'));
        hide($id('reanalyze-all-wrap'));
        // Reset input
        $id('url-input').value = '';
        _results = [];
      })
      .catch(function (err) {
        confirmBtn.disabled = false;
        if (confirmAllBtn) confirmAllBtn.disabled = false;
        var errEl = $id('error-msg');
        errEl.textContent = 'Network error — ' + err.message;
        show(errEl);
      });
  }

  // ---- Escape HTML ---------------------------------------------------------
  function _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ---- Show/hide "Re-analyze all" button when any textarea has content -----
  function updateReanalyzeAllVisibility() {
    var anyPasted = false;
    document.querySelectorAll('.manual-text-area').forEach(function (ta) {
      if (ta.value.trim()) anyPasted = true;
    });
    var wrap = $id('reanalyze-all-wrap');
    if (wrap) wrap.hidden = !anyPasted;
  }

  // ---- Re-analyze ALL URLs using pasted text from all textareas -----------
  function reanalyzeAll() {
    var urlArea = $id('url-input');
    var allUrls = (urlArea.value || '').split('\n').map(function (u) { return u.trim(); }).filter(Boolean);

    var manualTexts = {};
    document.querySelectorAll('.manual-text-area').forEach(function (ta) {
      var u = ta.getAttribute('data-url');
      var t = ta.value.trim();
      if (u && t) manualTexts[u] = t;
    });

    if (!allUrls.length) {
      alert('No URLs found. Please paste URLs in the input box above first.');
      return;
    }
    if (!Object.keys(manualTexts).length) {
      alert('No pasted JD text found. Paste the job description text into the fields below each failed URL first.');
      return;
    }
    doAnalyze(allUrls, manualTexts);
  }

  // ---- Re-analyze a single URL with pasted text ---------------------------
  function reanalyzeUrl(url, idx) {
    var textarea = document.querySelector('.manual-text-area[data-url="' + url + '"]');
    var text = textarea ? textarea.value.trim() : '';

    var manualTexts = {};
    if (text) manualTexts[url] = text;

    // Run analyze for just this URL
    var analyzeBtn = $id('analyze-btn');
    analyzeBtn.disabled = true;
    show($id('loading-wrap'));
    hide($id('results-wrap'));

    var urlArea = $id('url-input');
    var allUrls = (urlArea.value || '').split('\n').map(function (u) { return u.trim(); }).filter(Boolean);
    if (!allUrls.length) allUrls = [url];

    // Collect all manual texts currently in the DOM
    document.querySelectorAll('.manual-text-area').forEach(function (ta) {
      var u = ta.getAttribute('data-url');
      var t = ta.value.trim();
      if (u && t) manualTexts[u] = t;
    });

    doAnalyze(allUrls, manualTexts);
  }

  // ---- Init ----------------------------------------------------------------
  function init() {
    var analyzeBtn = $id('analyze-btn');
    if (!analyzeBtn) return;

    analyzeBtn.addEventListener('click', function () {
      var urlArea = $id('url-input');
      var urls = (urlArea.value || '').split('\n').map(function (u) { return u.trim(); }).filter(Boolean);
      if (!urls.length) {
        alert('Please enter at least one URL.');
        return;
      }
      if (urls.length > 20) {
        alert('Maximum 20 URLs per analysis batch.');
        return;
      }
      var badUrls = urls.filter(function (u) { return !/^https:\/\//i.test(u); });
      if (badUrls.length) {
        alert('All URLs must start with https://\n\nInvalid:\n' + badUrls.slice(0, 3).join('\n'));
        return;
      }
      doAnalyze(urls, {});
    });

    document.addEventListener('click', function (e) {
      if (e.target && e.target.classList.contains('reanalyze-btn')) {
        var url = e.target.getAttribute('data-url');
        var idx = parseInt(e.target.getAttribute('data-idx'), 10);
        reanalyzeUrl(url, idx);
      }
    });

    var selectAllChk = $id('select-all-chk');
    if (selectAllChk) {
      selectAllChk.addEventListener('change', function () {
        var checked = this.checked;
        document.querySelectorAll('.result-chk').forEach(function (chk) { chk.checked = checked; });
        updateSelCount();
      });
    }

    document.addEventListener('change', function (e) {
      if (e.target && e.target.classList.contains('result-chk')) {
        updateSelCount();
      }
    });

    var selAll = $id('sel-all-link');
    if (selAll) {
      selAll.addEventListener('click', function () {
        document.querySelectorAll('.result-chk').forEach(function (chk) { chk.checked = true; });
        updateSelCount();
      });
    }

    var selNone = $id('sel-none-link');
    if (selNone) {
      selNone.addEventListener('click', function () {
        document.querySelectorAll('.result-chk').forEach(function (chk) { chk.checked = false; });
        updateSelCount();
      });
    }

    var confirmBtn = $id('confirm-btn');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', function () {
        doConfirm(getSelectedJobs());
      });
    }

    var confirmAllBtn = $id('confirm-all-btn');
    if (confirmAllBtn) {
      confirmAllBtn.addEventListener('click', function () {
        doConfirm(getAllReadyJobs());
      });
    }

    var reanalyzeAllBtn = $id('reanalyze-all-btn');
    if (reanalyzeAllBtn) {
      reanalyzeAllBtn.addEventListener('click', reanalyzeAll);
    }

    // Show "Re-analyze all" button as soon as any textarea gets content
    document.addEventListener('input', function (e) {
      if (e.target && e.target.classList.contains('manual-text-area')) {
        updateReanalyzeAllVisibility();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
