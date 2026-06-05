// Phase D dashboard — tab switcher, trade-log sort + search.
// Theme toggle and modal logic land in subsequent D commits.

(function () {
  'use strict';

  const tabs   = Array.from(document.querySelectorAll('.tab-nav [role="tab"]'));
  const panels = Array.from(document.querySelectorAll('[role="tabpanel"]'));

  function activate(tabId) {
    tabs.forEach(t => {
      const isActive = t.dataset.tab === tabId;
      t.classList.toggle('active', isActive);
      t.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    panels.forEach(p => {
      p.hidden = p.id !== `tab-${tabId}`;
    });
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', () => activate(tab.dataset.tab));
  });

  // Keyboard arrow nav (tablist convention)
  document.querySelector('.tab-nav')?.addEventListener('keydown', (e) => {
    const idx = tabs.indexOf(document.activeElement);
    if (idx === -1) return;
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      const next = tabs[(idx + 1) % tabs.length];
      next.focus();
      activate(next.dataset.tab);
    } else if (e.key === 'ArrowLeft') {
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
  }
})();
