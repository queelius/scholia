/**
 * texwatch Dashboard — unified paper state sidebar
 */
(function () {
    'use strict';

    let dashboardData = null;
    let activeTab = 'files';
    let kbPanelIdx = -1;
    let kbItemIdx = -1;

    // ---------------------------------------------------------------
    // Tab switching
    // ---------------------------------------------------------------

    function initTabs() {
        const tabs = document.querySelectorAll('.sidebar-tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.dataset.tab;
                if (target) switchTab(target);
            });
        });
    }

    function switchTab(tab) {
        activeTab = tab;
        const tabs = document.querySelectorAll('.sidebar-tab');
        tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === tab));

        const treeContainer = document.getElementById('tree-container');
        const dashContainer = document.getElementById('dashboard-container');
        if (treeContainer) treeContainer.style.display = tab === 'files' ? '' : 'none';
        if (dashContainer) dashContainer.style.display = tab === 'dashboard' ? '' : 'none';

        if (tab === 'dashboard' && !dashboardData) {
            fetchDashboard();
        }
    }

    // ---------------------------------------------------------------
    // Data fetching
    // ---------------------------------------------------------------

    async function fetchDashboard() {
        try {
            const base = window.TEXWATCH_BASE || '';
            const resp = await fetch(base + '/dashboard');
            if (!resp.ok) return;
            dashboardData = await resp.json();
            renderAll(dashboardData);
        } catch (err) {
            console.error('Dashboard fetch failed:', err);
        }
    }

    // ---------------------------------------------------------------
    // Render all panels
    // ---------------------------------------------------------------

    function renderAll(data) {
        renderHealth(data);
        renderSections(data);
        renderIssues(data);
        renderBibliography(data);
        renderChanges(data);
        renderEnvironments(data);
    }

    // ---------------------------------------------------------------
    // Health panel
    // ---------------------------------------------------------------

    function renderHealth(data) {
        const el = document.getElementById('content-health');
        if (!el) return;
        el.textContent = '';

        const h = data.health || {};
        const rows = [
            ['Title', h.title || 'Untitled'],
            ['Author', h.author || '\u2014'],
            ['Class', h.documentclass || '?'],
            ['Words', (h.word_count || 0).toLocaleString()],
            ['Pages', h.page_limit
                ? (h.page_count || 0) + ' / ' + h.page_limit
                : String(h.page_count || 0)],
            ['Compile', h.compile_status || 'none'],
        ];

        for (const [label, value] of rows) {
            const row = document.createElement('div');
            row.className = 'health-row';

            const lbl = document.createElement('span');
            lbl.textContent = label;

            const val = document.createElement('span');
            val.className = 'health-value';
            if (label === 'Compile') {
                if (h.compile_status === 'success') val.classList.add('success');
                else if (h.compile_status === 'error') val.classList.add('error');
            }
            val.textContent = value;

            row.appendChild(lbl);
            row.appendChild(val);
            el.appendChild(row);
        }

        // Badge
        const badge = document.getElementById('badge-health');
        if (badge) {
            const errs = h.error_count || 0;
            const warns = h.warning_count || 0;
            if (errs > 0) {
                badge.textContent = errs + 'E';
                badge.className = 'badge error';
            } else if (warns > 0) {
                badge.textContent = warns + 'W';
                badge.className = 'badge warning';
            } else {
                badge.textContent = '';
                badge.className = 'badge';
            }
        }
    }

    // ---------------------------------------------------------------
    // Sections panel
    // ---------------------------------------------------------------

    function renderSections(data) {
        const el = document.getElementById('content-sections');
        if (!el) return;
        el.textContent = '';

        const sections = data.sections || [];
        const badge = document.getElementById('badge-sections');
        if (badge) {
            badge.textContent = sections.length ? String(sections.length) : '';
            badge.className = 'badge';
        }

        for (const sec of sections) {
            const row = document.createElement('div');
            row.className = 'section-row';

            const dot = document.createElement('span');
            dot.className = 'dirty-dot' + (sec.is_dirty ? ' dirty' : '');

            const title = document.createElement('span');
            title.className = 'section-title';
            if (sec.level === 'subsection') title.classList.add('subsection');
            if (sec.level === 'subsubsection') title.classList.add('subsubsection');
            title.textContent = sec.title;

            const stats = document.createElement('span');
            stats.className = 'section-stats';
            const parts = [(sec.word_count || 0).toLocaleString() + 'w'];
            if (sec.citation_count) parts.push(sec.citation_count + 'c');
            if (sec.todo_count) parts.push(sec.todo_count + 'T');
            if (sec.figure_count) parts.push(sec.figure_count + 'f');
            stats.textContent = parts.join(' ');

            row.appendChild(dot);
            row.appendChild(title);
            row.appendChild(stats);

            row.addEventListener('click', () => gotoFileLine(sec.file, sec.line));
            el.appendChild(row);
        }
    }

    // ---------------------------------------------------------------
    // Issues panel
    // ---------------------------------------------------------------

    function renderIssues(data) {
        const el = document.getElementById('content-issues');
        if (!el) return;
        el.textContent = '';

        const issues = data.issues || [];
        const badge = document.getElementById('badge-issues');
        if (badge) {
            if (issues.length > 0) {
                const errors = issues.filter(i => i.type === 'error').length;
                badge.textContent = String(issues.length);
                badge.className = 'badge ' + (errors > 0 ? 'error' : 'warning');
            } else {
                badge.textContent = '';
                badge.className = 'badge';
            }
        }

        for (const iss of issues) {
            const row = document.createElement('div');
            row.className = 'issue-row';

            const icon = document.createElement('span');
            icon.className = 'issue-icon';
            if (iss.type === 'error') {
                icon.textContent = '\u2716';
                icon.classList.add('error');
            } else if (iss.type === 'warning') {
                icon.textContent = '\u26A0';
                icon.classList.add('warning');
            } else if (iss.type === 'undefined_citation') {
                icon.textContent = '\u2716';
                icon.classList.add('error');
            } else {
                icon.textContent = '\u2610';
                icon.classList.add('info');
            }

            const text = document.createElement('span');
            text.className = 'issue-text';
            text.textContent = iss.message || iss.text || iss.key || '';

            const loc = document.createElement('span');
            loc.className = 'issue-location';
            if (iss.file) {
                loc.textContent = iss.file + (iss.line ? ':' + iss.line : '');
            }

            row.appendChild(icon);
            row.appendChild(text);
            row.appendChild(loc);

            if (iss.file && iss.line) {
                row.addEventListener('click', () => gotoFileLine(iss.file, iss.line));
            }
            el.appendChild(row);
        }
    }

    // ---------------------------------------------------------------
    // Bibliography panel
    // ---------------------------------------------------------------

    function renderBibliography(data) {
        const el = document.getElementById('content-bibliography');
        if (!el) return;
        el.textContent = '';

        const bib = data.bibliography || {};
        const defined = bib.defined || 0;
        const cited = bib.cited || 0;

        const badge = document.getElementById('badge-bibliography');
        if (badge) {
            const undef = (bib.undefined_keys || []).length;
            if (undef > 0) {
                badge.textContent = undef + ' undef';
                badge.className = 'badge error';
            } else {
                badge.textContent = defined ? defined + '/' + cited : '';
                badge.className = 'badge';
            }
        }

        // Summary
        const summary = document.createElement('div');
        summary.className = 'bib-summary';

        const defStat = document.createElement('span');
        defStat.className = 'bib-stat';
        defStat.textContent = defined + ' defined';

        const citeStat = document.createElement('span');
        citeStat.className = 'bib-stat';
        citeStat.textContent = cited + ' cited';

        summary.appendChild(defStat);
        summary.appendChild(citeStat);
        el.appendChild(summary);

        // Undefined keys
        if (bib.undefined_keys && bib.undefined_keys.length) {
            const section = document.createElement('div');
            section.className = 'bib-keys';
            const header = document.createElement('div');
            header.textContent = 'Undefined:';
            header.style.color = 'var(--error)';
            header.style.marginBottom = '2px';
            section.appendChild(header);

            for (const key of bib.undefined_keys) {
                const chip = document.createElement('span');
                chip.className = 'bib-key undefined';
                chip.textContent = key;
                section.appendChild(chip);
            }
            el.appendChild(section);
        }

        // Uncited keys
        if (bib.uncited_keys && bib.uncited_keys.length) {
            const section = document.createElement('div');
            section.className = 'bib-keys';
            const header = document.createElement('div');
            header.textContent = 'Uncited:';
            header.style.color = 'var(--warning)';
            header.style.marginBottom = '2px';
            section.appendChild(header);

            for (const key of bib.uncited_keys) {
                const chip = document.createElement('span');
                chip.className = 'bib-key uncited';
                chip.textContent = key;
                section.appendChild(chip);
            }
            el.appendChild(section);
        }
    }

    // ---------------------------------------------------------------
    // Changes panel
    // ---------------------------------------------------------------

    function renderChanges(data) {
        const el = document.getElementById('content-changes');
        if (!el) return;
        el.textContent = '';

        const changes = data.changes || [];
        const badge = document.getElementById('badge-changes');
        if (badge) {
            badge.textContent = changes.length ? String(changes.length) : '';
            badge.className = 'badge' + (changes.length ? ' info' : '');
        }

        if (changes.length === 0) {
            const msg = document.createElement('div');
            msg.style.color = 'var(--text-secondary)';
            msg.textContent = 'No changes since last compile';
            el.appendChild(msg);
            return;
        }

        for (const ch of changes) {
            const row = document.createElement('div');
            row.className = 'change-row';

            const header = document.createElement('div');
            header.className = 'change-header';

            const title = document.createElement('span');
            title.className = 'change-title';
            title.textContent = ch.section_title;

            const stats = document.createElement('span');
            stats.className = 'change-stats';

            if (ch.words_added > 0) {
                const added = document.createElement('span');
                added.className = 'added';
                added.textContent = '+' + ch.words_added;
                stats.appendChild(added);
            }
            if (ch.words_removed > 0) {
                if (ch.words_added > 0) {
                    const sep = document.createTextNode(' ');
                    stats.appendChild(sep);
                }
                const removed = document.createElement('span');
                removed.className = 'removed';
                removed.textContent = '-' + ch.words_removed;
                stats.appendChild(removed);
            }

            header.appendChild(title);
            header.appendChild(stats);
            row.appendChild(header);

            // Diff snippet (expandable)
            if (ch.diff_snippet) {
                const snippet = document.createElement('div');
                snippet.className = 'diff-snippet';

                const lines = ch.diff_snippet.split('\n');
                for (const line of lines) {
                    const span = document.createElement('div');
                    if (line.startsWith('+')) span.className = 'diff-add';
                    else if (line.startsWith('-')) span.className = 'diff-del';
                    else if (line.startsWith('@')) span.className = 'diff-hdr';
                    span.textContent = line;
                    snippet.appendChild(span);
                }

                row.appendChild(snippet);

                header.style.cursor = 'pointer';
                header.addEventListener('click', () => {
                    snippet.classList.toggle('visible');
                });
            }

            el.appendChild(row);
        }
    }

    // ---------------------------------------------------------------
    // Environments panel
    // ---------------------------------------------------------------

    function renderEnvironments(data) {
        const el = document.getElementById('content-environments');
        if (!el) return;
        el.textContent = '';

        const envData = data.environments || {};
        const items = envData.items || [];

        const badge = document.getElementById('badge-environments');
        if (badge) {
            badge.textContent = items.length ? String(items.length) : '';
            badge.className = 'badge';
        }

        // Group by type
        const groups = {};
        for (const item of items) {
            const type = item.env_type;
            if (!groups[type]) groups[type] = [];
            groups[type].push(item);
        }

        for (const [type, groupItems] of Object.entries(groups)) {
            const group = document.createElement('div');
            group.className = 'env-group';

            const hdr = document.createElement('div');
            hdr.className = 'env-group-header';
            const typeName = document.createElement('span');
            typeName.textContent = type;
            const count = document.createElement('span');
            count.className = 'env-count';
            count.textContent = '(' + groupItems.length + ')';
            hdr.appendChild(typeName);
            hdr.appendChild(count);

            const list = document.createElement('div');
            list.style.display = 'none';

            hdr.addEventListener('click', () => {
                list.style.display = list.style.display === 'none' ? '' : 'none';
            });

            for (const item of groupItems) {
                const row = document.createElement('div');
                row.className = 'env-item';

                if (item.label) {
                    const lbl = document.createElement('span');
                    lbl.className = 'env-label';
                    lbl.textContent = item.label;
                    row.appendChild(lbl);
                }

                if (item.name || item.caption) {
                    const name = document.createElement('span');
                    name.textContent = item.name || item.caption || '';
                    row.appendChild(name);
                }

                const loc = document.createElement('span');
                loc.className = 'env-location';
                loc.textContent = item.file + ':' + item.start_line;
                row.appendChild(loc);

                row.addEventListener('click', () => gotoFileLine(item.file, item.start_line));
                list.appendChild(row);
            }

            group.appendChild(hdr);
            group.appendChild(list);
            el.appendChild(group);
        }
    }

    // ---------------------------------------------------------------
    // Navigation helper
    // ---------------------------------------------------------------

    function gotoFileLine(file, line) {
        // Load file in editor
        if (window.texwatchEditor) {
            window.texwatchEditor.loadFile(file).then(() => {
                window.texwatchEditor.scrollToLine(line);
            }).catch(err => {
                console.error('Dashboard: failed to navigate to', file, err);
            });
        }
        // Forward sync to PDF
        window.dispatchEvent(new CustomEvent('texwatch:goto-line', {
            detail: { file: file, line: line }
        }));
    }

    // ---------------------------------------------------------------
    // Keyboard navigation
    // ---------------------------------------------------------------

    function setupKeyboard() {
        document.addEventListener('keydown', (e) => {
            // Only handle when no interactive element is focused
            if (e.target.closest('.cm-editor')) return;
            const tag = e.target.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON') return;
            if (activeTab !== 'dashboard') return;

            const panels = document.querySelectorAll('.dashboard-panel');
            if (panels.length === 0) return;

            switch (e.key) {
                case 'j': // next item
                    e.preventDefault();
                    moveHighlight(1, panels);
                    break;
                case 'k': // prev item
                    e.preventDefault();
                    moveHighlight(-1, panels);
                    break;
                case ']': // next panel
                    e.preventDefault();
                    movePanelFocus(1, panels);
                    break;
                case '[': // prev panel
                    e.preventDefault();
                    movePanelFocus(-1, panels);
                    break;
                case 'Enter': // navigate to highlighted item
                    e.preventDefault();
                    activateHighlighted();
                    break;
                case 'o': // toggle panel open/close
                    e.preventDefault();
                    toggleCurrentPanel(panels);
                    break;
            }
        });
    }

    function moveHighlight(dir, panels) {
        const items = getClickableItems();
        if (items.length === 0) return;

        // Remove old highlight
        const old = document.querySelector('.kb-highlight');
        if (old) old.classList.remove('kb-highlight');

        kbItemIdx += dir;
        if (kbItemIdx < 0) kbItemIdx = items.length - 1;
        if (kbItemIdx >= items.length) kbItemIdx = 0;

        items[kbItemIdx].classList.add('kb-highlight');
        items[kbItemIdx].scrollIntoView({ block: 'nearest' });
    }

    function movePanelFocus(dir, panels) {
        kbPanelIdx += dir;
        if (kbPanelIdx < 0) kbPanelIdx = panels.length - 1;
        if (kbPanelIdx >= panels.length) kbPanelIdx = 0;

        panels[kbPanelIdx].scrollIntoView({ block: 'nearest' });
        // Open the panel
        panels[kbPanelIdx].open = true;
        // Reset item index
        kbItemIdx = -1;
    }

    function activateHighlighted() {
        const highlighted = document.querySelector('.kb-highlight');
        if (highlighted) highlighted.click();
    }

    function toggleCurrentPanel(panels) {
        if (kbPanelIdx >= 0 && kbPanelIdx < panels.length) {
            panels[kbPanelIdx].open = !panels[kbPanelIdx].open;
        }
    }

    function getClickableItems() {
        const container = document.getElementById('dashboard-container');
        if (!container) return [];
        return Array.from(container.querySelectorAll(
            '.section-row, .issue-row, .change-header, .env-item'
        ));
    }

    // ---------------------------------------------------------------
    // Init
    // ---------------------------------------------------------------

    function init() {
        initTabs();
        setupKeyboard();

        // Listen for dashboard update events from WebSocket
        window.addEventListener('texwatch:dashboard-updated', () => {
            if (activeTab === 'dashboard') {
                fetchDashboard();
            } else {
                dashboardData = null; // invalidate cache, will re-fetch on tab switch
            }
        });
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
