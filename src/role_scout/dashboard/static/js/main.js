(function () {
  'use strict';

  // ── Theme ────────────────────────────────────────────────────────────────
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '☀' : '☽';
  }

  function initTheme() {
    var saved = localStorage.getItem('rs-theme') || 'light';
    applyTheme(saved);
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        var current = document.documentElement.getAttribute('data-theme') || 'light';
        var next = current === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        localStorage.setItem('rs-theme', next);
      });
    }
  }

  // ── Row expand / collapse ────────────────────────────────────────────────
  window.rsToggleExpand = function (hashId, event) {
    // Don't expand when clicking on interactive elements
    if (event && event.target) {
      var tag = event.target.tagName;
      if (tag === 'SELECT' || tag === 'BUTTON' || tag === 'A' || tag === 'INPUT') return;
      if (event.target.closest('select, button, a, input')) return;
    }

    var jobRow = document.querySelector('tr.job-row[data-hash-id="' + hashId + '"]');
    var expandRow = document.getElementById('expand-' + hashId);
    if (!expandRow) return;

    var isOpen = expandRow.classList.contains('open');

    if (isOpen) {
      expandRow.classList.remove('open');
      if (jobRow) {
        jobRow.classList.remove('expanded');
        jobRow.setAttribute('aria-expanded', 'false');
      }
    } else {
      expandRow.classList.add('open');
      if (jobRow) {
        jobRow.classList.add('expanded');
        jobRow.setAttribute('aria-expanded', 'true');
      }
      animateExpand(hashId);
    }
  };

  function animateExpand(hashId) {
    // Animate score ring
    var ring = document.getElementById('ring-' + hashId);
    if (ring) {
      var pct = parseInt(ring.getAttribute('data-pct'), 10) || 0;
      var circumference = 2 * Math.PI * 26;
      var offset = circumference * (1 - pct / 100);
      requestAnimationFrame(function () {
        ring.style.strokeDashoffset = offset;
      });
    }

    // Animate score bars
    var expandRow = document.getElementById('expand-' + hashId);
    if (!expandRow) return;
    var bars = expandRow.querySelectorAll('.score-bar-fill');
    bars.forEach(function (bar) {
      var pct = parseInt(bar.getAttribute('data-pct'), 10) || 0;
      requestAnimationFrame(function () {
        bar.style.width = pct + '%';
      });
    });
  }

  // ── Total meta in topbar ─────────────────────────────────────────────────
  function updateTopbarMeta() {
    var el = document.getElementById('rs-total-meta');
    if (!el || !window.RS_CONFIG) return;
    var total = window.RS_CONFIG.totalJobs || 0;
    el.textContent = total + ' total job' + (total !== 1 ? 's' : '') + ' tracked';
  }

  // ── Threshold slider (extracted from threshold.js into main for shared layout) ──
  // threshold.js already handles this — no duplication needed

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    initTheme();
    updateTopbarMeta();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
