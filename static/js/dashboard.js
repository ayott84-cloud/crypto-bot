// Phase D dashboard — tab switcher, trade-log sort + search.
// Theme toggle and modal logic land in subsequent D commits.

(function () {
  'use strict';

  // ── Theme toggle (light/dark, persisted to localStorage) ──────────────
  // Applied as early as possible so there's no FOUC into dark mode on a
  // page that the operator has saved as light.
  const THEME_KEY = 'cb-theme';
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.dataset.theme = saved;
    }
  } catch (e) { /* localStorage unavailable — fall back to default */ }

  const toggleBtn = document.querySelector('[data-theme-toggle]');
  const syncTogglePressed = () => {
    if (!toggleBtn) return;
    const isLight = document.documentElement.dataset.theme === 'light';
    toggleBtn.setAttribute('aria-pressed', isLight ? 'true' : 'false');
  };
  syncTogglePressed();
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const current = document.documentElement.dataset.theme || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
      syncTogglePressed();
    });
  }

  // J.1: tab buttons now live in the sidebar (was .tab-nav).
  // Selector covers both so a partial deploy doesn't break navigation.
  const tabs   = Array.from(document.querySelectorAll(
    '.sidebar [role="tab"], .tab-nav [role="tab"]'));
  const panels = Array.from(document.querySelectorAll('[role="tabpanel"]'));

  function activate(tabId) {
    tabs.forEach(t => {
      const isActive = t.dataset.tab === tabId;
      t.classList.toggle('active', isActive);
      t.setAttribute('aria-selected', isActive ? 'true' : 'false');
      if (isActive) t.setAttribute('aria-current', 'page');
      else          t.removeAttribute('aria-current');
    });
    panels.forEach(p => {
      p.hidden = p.id !== `tab-${tabId}`;
    });
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', () => activate(tab.dataset.tab));
  });

  // Keyboard arrow nav (tablist convention).
  // J.1: sidebar is vertical → up/down. Left/right still works for the
  // horizontal sidebar mode on narrow viewports.
  const navRoot = document.querySelector('.sidebar') || document.querySelector('.tab-nav');
  navRoot?.addEventListener('keydown', (e) => {
    const idx = tabs.indexOf(document.activeElement);
    if (idx === -1) return;
    const advance = (e.key === 'ArrowDown' || e.key === 'ArrowRight');
    const retreat = (e.key === 'ArrowUp'   || e.key === 'ArrowLeft');
    if (advance) {
      e.preventDefault();
      const next = tabs[(idx + 1) % tabs.length];
      next.focus();
      activate(next.dataset.tab);
    } else if (retreat) {
      e.preventDefault();
      const prev = tabs[(idx - 1 + tabs.length) % tabs.length];
      prev.focus();
      activate(prev.dataset.tab);
    }
  });

  // ── Trade log: sortable headers + debounced search ────────────────────

  const tradesTable = document.querySelector('.trades-table');
  if (tradesTable) {
    const tbody = tradesTable.querySelector('[data-trades-tbody]');
    const allRows = Array.from(tbody.querySelectorAll('tr'));
    const searchInput = document.querySelector('[data-trades-search]');
    const visibleCount = document.querySelector('[data-trades-visible]');

    const sortRows = (key, type, dir) => {
      const sign = dir === 'asc' ? 1 : -1;
      const cmp = (a, b) => {
        const av = a.dataset[key] || '';
        const bv = b.dataset[key] || '';
        if (type === 'num') {
          return (parseFloat(av) - parseFloat(bv)) * sign;
        }
        return av.localeCompare(bv) * sign;
      };
      const sorted = [...allRows].sort(cmp);
      tbody.replaceChildren(...sorted);
    };

    tradesTable.querySelectorAll('thead th[data-sort]').forEach(th => {
      th.addEventListener('click', () => {
        const key  = th.dataset.sort;
        const type = th.dataset.sortType || 'text';
        // Toggle current header's direction, clear others
        const current = th.dataset.sortActive;
        const nextDir = current === 'asc' ? 'desc' : 'asc';
        tradesTable.querySelectorAll('thead th[data-sort-active]')
          .forEach(other => other.removeAttribute('data-sort-active'));
        th.dataset.sortActive = nextDir;
        sortRows(key, type, nextDir);
      });
    });

    // Debounced search (200ms)
    if (searchInput) {
      let timer;
      const applyFilter = () => {
        const q = searchInput.value.trim().toLowerCase();
        let shown = 0;
        // Walk the current DOM order rather than allRows so sort order
        // is preserved across filters.
        Array.from(tbody.querySelectorAll('tr')).forEach(tr => {
          const ds = tr.dataset;
          const hay = `${ds.symbol} ${ds.strategy} ${ds.bot} ${ds.exit_reason} ${ds.direction} ${ds.result}`;
          const match = !q || hay.includes(q);
          tr.classList.toggle('is-hidden', !match);
          if (match) shown++;
        });
        if (visibleCount) visibleCount.textContent = String(shown);
      };
      searchInput.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(applyFilter, 200);
      });
    }

    // ── CSV export (Phase D.7f) ─────────────────────────────────────────
    // Walks the currently-visible rows in current sort order, builds a
    // CSV, and triggers a Blob download. The filename includes today's
    // date so multiple exports don't clobber each other.
    const exportBtn = document.querySelector('[data-trades-export]');
    if (exportBtn) {
      const CSV_HEADERS = [
        "#", "Date", "Symbol", "Direction", "Bot", "Strategy",
        "Entry", "Exit", "Quantity", "Leverage",
        "Net PnL", "Exit Reason", "Result"
      ];
      const escapeCell = (v) => {
        const s = String(v ?? '');
        return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      };
      exportBtn.addEventListener('click', () => {
        const visibleRows = Array.from(tbody.querySelectorAll('tr'))
          .filter(tr => !tr.classList.contains('is-hidden'));
        const lines = [CSV_HEADERS.join(',')];
        visibleRows.forEach(tr => {
          const ds = tr.dataset;
          lines.push([
            ds.row_num, ds.date_opened, ds.symbol, ds.direction,
            ds.bot, ds.strategy, ds.entry_price, ds.exit_price,
            ds.quantity, ds.leverage, ds.net_pnl,
            ds.exit_reason, ds.result
          ].map(escapeCell).join(','));
        });
        const csv = lines.join('\r\n');
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `trades-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      });
    }
  }
})();
