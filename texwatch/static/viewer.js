/**
 * texwatch PDF viewer with WebSocket live reload
 */

// PDF.js configuration
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

function _dbg(...args) {
    if (window.TEXWATCH_DEBUG) console.log('[texwatch]', ...args);
}

class TexWatchViewer {
    constructor() {
        this.pdfDoc = null;
        this.currentPage = 1;
        this.totalPages = 0;
        this.scale = 1.5;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this._wheelCooldown = false;
        this.pdfHighlight = document.getElementById('pdf-highlight');
        this._highlightFadeTimer = null;

        // DOM elements
        this.canvas = document.getElementById('pdf-canvas');
        this.ctx = this.canvas.getContext('2d');
        this.container = document.getElementById('pdf-container');
        this.textLayer = document.getElementById('text-layer');
        this.markdownPreview = document.getElementById('markdown-preview');
        this.viewerPane = document.getElementById('viewer-pane');

        // Viewer mode: 'pdf' or 'markdown'
        this.viewerMode = 'pdf';
        this._markdownDebounceTimer = null;
        this.filename = document.getElementById('filename');
        this.statusIndicator = document.getElementById('status-indicator');
        this.pageInfo = document.getElementById('page-info');
        this.lineInfo = document.getElementById('line-info');
        this.compileInfo = document.getElementById('compile-info');
        this.errorSummary = document.getElementById('error-summary');
        this.errorList = document.getElementById('error-list');
        this.errorPanel = document.getElementById('error-panel');

        // Cached config (fetched once)
        this._cachedConfig = null;
        // Last compilation log output
        this._lastLogOutput = '';

        // Log viewer elements
        this.logViewer = document.getElementById('log-viewer');
        this.logContent = document.getElementById('log-content');
        this.todoList = document.getElementById('todo-list');

        // Continuous scroll state
        this.scrollMode = 'page';  // 'page' or 'continuous'
        this.pageElements = [];    // per-page wrapper elements
        this.renderedPages = new Set();
        this.observer = null;
        this._scrollTrackingTimer = null;
        this._renderQueue = new Set();
        this._rendering = false;

        this.init();
    }

    async init() {
        this.setupEventListeners();
        this.connectWebSocket();
        await this.loadPDF();
    }

    setupEventListeners() {
        // Refresh button
        document.getElementById('btn-refresh').addEventListener('click', () => {
            this.forceCompile();
        });

        // Scroll mode toggle
        document.getElementById('btn-scroll-mode').addEventListener('click', () => {
            this.toggleScrollMode();
        });

        // Error panel toggle
        document.getElementById('error-header').addEventListener('click', () => {
            this.errorPanel.classList.toggle('collapsed');
        });

        // Log button toggle
        const btnLog = document.getElementById('btn-show-log');
        if (btnLog) {
            btnLog.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleLogViewer();
            });
        }

        // PDF double-click for SyncTeX reverse (target container, not canvas,
        // because the text layer sits on top of the canvas)
        this.container.addEventListener('dblclick', (e) => {
            if (this.scrollMode === 'continuous') {
                this._handleContinuousDblClick(e);
            } else {
                const rect = this.canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;

                _dbg(`dblclick: pixel(${x.toFixed(1)}, ${y.toFixed(1)}) -> pdf(${(x / this.scale).toFixed(1)}, ${(y / this.scale).toFixed(1)}) page=${this.currentPage}`);

                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({
                        type: 'click',
                        page: this.currentPage,
                        x: x / this.scale,
                        y: y / this.scale
                    }));
                }
            }
        });

        // Keyboard navigation (guard against editor focus)
        document.addEventListener('keydown', (e) => {
            if (e.target.closest('.cm-editor')) return;
            if (this.scrollMode === 'continuous') {
                // In continuous mode, let natural scroll work; only handle Home/End
                if (e.key === 'Home') {
                    this.goToPage(1);
                } else if (e.key === 'End') {
                    this.goToPage(this.totalPages);
                }
                return;
            }
            if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
                this.prevPage();
            } else if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
                this.nextPage();
            } else if (e.key === 'Home') {
                this.goToPage(1);
            } else if (e.key === 'End') {
                this.goToPage(this.totalPages);
            }
        });

        // Forward SyncTeX: editor double-click -> PDF navigates
        window.addEventListener('texwatch:goto-line', (e) => {
            this.gotoLine(e.detail.line, e.detail.file);
        });

        // File loaded: switch viewer mode based on extension
        window.addEventListener('texwatch:file-loaded', (e) => {
            const file = e.detail.file;
            if (file && (file.endsWith('.md') || file.endsWith('.markdown') || file.endsWith('.txt'))) {
                this.setViewerMode('markdown');
                if (window.texwatchEditor && window.texwatchEditor.view) {
                    this.renderMarkdown(window.texwatchEditor.view.state.doc.toString());
                }
            } else {
                this.setViewerMode('pdf');
            }
        });

        // Content changed: live-update markdown preview (debounced 300ms)
        window.addEventListener('texwatch:content-changed', (e) => {
            if (this.viewerMode !== 'markdown') return;
            if (this._markdownDebounceTimer) clearTimeout(this._markdownDebounceTimer);
            this._markdownDebounceTimer = setTimeout(() => {
                this.renderMarkdown(e.detail.content);
                this._markdownDebounceTimer = null;
            }, 300);
        });

        // Editor state: forward to server via WebSocket
        window.addEventListener('texwatch:editor-state', (e) => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'editor_state',
                    state: { file: e.detail.file, line: e.detail.line }
                }));
            }
        });

        // Scroll-based page navigation (with cooldown to prevent trackpad rapid-fire)
        this.container.addEventListener('wheel', (e) => {
            if (this.scrollMode === 'continuous') return;  // let natural scroll work
            if (this._wheelCooldown) return;

            const atTop = this.container.scrollTop === 0;
            const atBottom = this.container.scrollTop + this.container.clientHeight >= this.container.scrollHeight;

            if (e.deltaY < 0 && atTop) {
                this.prevPage();
                this._startWheelCooldown();
            } else if (e.deltaY > 0 && atBottom) {
                this.nextPage();
                this._startWheelCooldown();
            }
        });
    }

    connectWebSocket() {
        const base = window.TEXWATCH_BASE || '';
        const wsUrl = `ws://${window.location.host}${base}/ws`;
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.setStatus('watching');
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.setStatus('error');
            this.scheduleReconnect();
        };

        this.ws.onerror = (e) => {
            console.error('WebSocket error:', e);
        };

        this.ws.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                this.handleMessage(data);
            } catch (err) {
                console.error('Failed to parse WebSocket message:', err);
            }
        };
    }

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('Max reconnect attempts reached');
            return;
        }

        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
        this.reconnectAttempts++;

        console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        setTimeout(() => this.connectWebSocket(), delay);
    }

    handleMessage(data) {
        if (data.type === 'goto' || data.type === 'source_position') {
            _dbg('handleMessage:', data.type, JSON.stringify(data));
        }
        switch (data.type) {
            case 'state':
                this.handleState(data);
                break;
            case 'compiling':
                this.setStatus(data.status ? 'compiling' : 'watching');
                if (data.status) {
                    this.compileInfo.textContent = 'Compiling...';
                    this.compileInfo.className = '';
                }
                break;
            case 'compiled':
                if (data.log_output) {
                    this._lastLogOutput = data.log_output;
                }
                this.handleCompiled(data.result);
                break;
            case 'goto':
                if (data.page != null) {
                    this.goToPage(data.page, data.y, data.x, data.width, data.height);
                }
                break;
            case 'source_position':
                this.showSourcePosition(data);
                break;
            case 'source_updated':
                window.dispatchEvent(new CustomEvent('texwatch:source-updated', {
                    detail: { file: data.file, mtime_ns: data.mtime_ns }
                }));
                break;
        }
    }

    handleState(data) {
        if (data.compiling) {
            this.setStatus('compiling');
        }
        if (data.result) {
            this.handleCompiled(data.result);
        }
        this._ensureConfig();
    }

    handleCompiled(result) {
        if (!result) return;

        this.setStatus(result.success ? 'watching' : 'error');

        // Update compile info
        const ago = this.formatTimeAgo(new Date(result.timestamp));
        if (result.success) {
            this.compileInfo.textContent = `Last compile: ${ago} ✓`;
            this.compileInfo.className = 'success';
        } else {
            this.compileInfo.textContent = `Compile failed: ${ago}`;
            this.compileInfo.className = 'error';
        }

        // Update error panel
        this.updateErrors(result.errors, result.warnings);

        // Reload PDF
        if (result.success) {
            this.loadPDF();
        }

        // Fetch config (for page limit) and structure (for TODOs)
        this._ensureConfig().then(() => this.updatePageInfo());
        this._fetchTodos();
    }

    updateErrors(errors, warnings) {
        const total = errors.length + warnings.length;

        if (total === 0) {
            this.errorSummary.textContent = 'No issues';
            this.errorSummary.className = '';
        } else if (errors.length > 0) {
            this.errorSummary.textContent = `${errors.length} error${errors.length > 1 ? 's' : ''}, ${warnings.length} warning${warnings.length !== 1 ? 's' : ''}`;
            this.errorSummary.className = 'has-errors';
        } else {
            this.errorSummary.textContent = `${warnings.length} warning${warnings.length !== 1 ? 's' : ''}`;
            this.errorSummary.className = 'has-warnings';
        }

        // Build error list
        this.errorList.innerHTML = '';

        for (const err of errors) {
            this.errorList.appendChild(this.createErrorItem(err, 'error'));
        }

        for (const warn of warnings) {
            this.errorList.appendChild(this.createErrorItem(warn, 'warning'));
        }
    }

    createErrorItem(item, type) {
        const div = document.createElement('div');
        div.className = `error-item ${type}`;

        const icon = document.createElement('span');
        icon.className = 'icon';
        icon.textContent = type === 'error' ? '✖' : '⚠';

        const location = document.createElement('span');
        location.className = 'location';
        location.textContent = item.line ? `${item.file}:${item.line}` : item.file;

        const message = document.createElement('span');
        message.className = 'message';
        message.textContent = item.message;

        div.appendChild(icon);
        div.appendChild(location);
        div.appendChild(message);

        // Click to navigate
        if (item.line) {
            location.addEventListener('click', () => {
                this.gotoLine(item.line);
            });
        }

        return div;
    }

    setStatus(status) {
        this.statusIndicator.className = `status ${status}`;
        this.statusIndicator.textContent = {
            watching: 'Watching',
            compiling: 'Compiling',
            error: 'Error'
        }[status] || status;
    }

    async loadPDF() {
        try {
            const base = window.TEXWATCH_BASE || '';
            // Add cache-busting parameter
            const url = `${base}/pdf?t=${Date.now()}`;
            this.pdfDoc = await pdfjsLib.getDocument(url).promise;
            this.totalPages = this.pdfDoc.numPages;

            // Get filename from status
            const response = await fetch(`${base}/status`);
            const status = await response.json();
            this.filename.textContent = status.file;

            if (this.scrollMode === 'continuous') {
                await this.initContinuousMode();
            } else {
                await this.renderPage(this.currentPage);

                // Highlight change effect
                this.container.classList.add('highlight-change');
                setTimeout(() => {
                    this.container.classList.remove('highlight-change');
                }, 2000);
            }

        } catch (err) {
            console.error('Failed to load PDF:', err);
        }
    }

    async renderPage(pageNum) {
        if (!this.pdfDoc) return;

        // Clamp page number
        pageNum = Math.max(1, Math.min(pageNum, this.totalPages));
        this.currentPage = pageNum;

        const page = await this.pdfDoc.getPage(pageNum);
        const viewport = page.getViewport({ scale: this.scale });

        this.canvas.height = viewport.height;
        this.canvas.width = viewport.width;

        await page.render({
            canvasContext: this.ctx,
            viewport: viewport
        }).promise;

        // Render text layer for selection/copy support
        if (this.textLayer) {
            this.textLayer.innerHTML = '';
            this.textLayer.style.width = viewport.width + 'px';
            this.textLayer.style.height = viewport.height + 'px';
            this.textLayer.style.setProperty('--scale-factor', viewport.scale);

            const textContent = await page.getTextContent();
            try {
                await pdfjsLib.renderTextLayer({
                    textContentSource: textContent,
                    container: this.textLayer,
                    viewport: viewport,
                }).promise;
            } catch (err) {
                console.error('Failed to render text layer:', err);
            }
        }

        this.updatePageInfo();
        this.sendViewerState();
    }

    updatePageInfo() {
        const limit = this._cachedConfig && this._cachedConfig.page_limit;
        if (limit && this.totalPages > limit) {
            this.pageInfo.textContent = `Page ${this.currentPage}/${this.totalPages} (limit: ${limit})`;
            this.pageInfo.classList.add('over-limit');
        } else {
            this.pageInfo.textContent = `Page ${this.currentPage}/${this.totalPages}`;
            this.pageInfo.classList.remove('over-limit');
        }
    }

    sendViewerState() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'viewer_state',
                state: {
                    page: this.currentPage,
                    total_pages: this.totalPages
                }
            }));
        }
    }

    showSourcePosition(data) {
        _dbg(`showSourcePosition: ${data.file}:${data.line} col=${data.column || 0}`);
        this.lineInfo.textContent = `${data.file}:${data.line}`;
        window.dispatchEvent(new CustomEvent('texwatch:source-position', {
            detail: { file: data.file, line: data.line, column: data.column || 0 }
        }));
    }

    _startWheelCooldown() {
        this._wheelCooldown = true;
        setTimeout(() => { this._wheelCooldown = false; }, 300);
    }

    // -----------------------------------------------------------------------
    // Page navigation — dispatches to continuous or page mode
    // -----------------------------------------------------------------------

    _clearAllHighlights() {
        if (this._highlightFadeTimer) {
            clearTimeout(this._highlightFadeTimer);
            this._highlightFadeTimer = null;
        }
        // Clear text highlights in all layers (single-page + per-page)
        document.querySelectorAll('.synctex-text-highlight')
            .forEach(el => el.classList.remove('synctex-text-highlight'));
        // Clear PDF overlay highlight (single-page mode)
        if (this.pdfHighlight) this.pdfHighlight.classList.remove('active');
        // Clear per-page highlights (continuous mode)
        document.querySelectorAll('.page-highlight.active')
            .forEach(el => el.classList.remove('active'));
    }

    goToPage(pageNum, y = null, x = null, width = null, height = null) {
        _dbg(`goToPage: page=${pageNum} y=${y} x=${x} w=${width} h=${height} mode=${this.scrollMode}`);
        this._clearAllHighlights();
        if (this.scrollMode === 'continuous') {
            this.goToPageContinuous(pageNum, y, x, width, height);
        } else {
            this.renderPage(pageNum).then(() => {
                if (y !== null) {
                    const textHighlighted = this._highlightTextInLayer(this.textLayer, x, y, width, height);
                    _dbg(`goToPage: textHighlighted=${textHighlighted}`);
                    this._showPdfHighlight(x, y, width, height, textHighlighted);
                }
            });
        }
    }

    _showPdfHighlight(x, y, width, height, skipVisual = false) {
        if (!this.pdfHighlight) return;

        const s = this.scale;
        const hasPreciseBox = (width && width > 0 && height && height > 0);
        _dbg(`showPdfHighlight: hasPreciseBox=${hasPreciseBox}, skipVisual=${skipVisual}`);

        if (hasPreciseBox) {
            // SyncTeX y = baseline, box top = y - height
            this.pdfHighlight.style.top = ((y - height) * s) + 'px';
            this.pdfHighlight.style.left = (x * s) + 'px';
            this.pdfHighlight.style.width = (width * s) + 'px';
            this.pdfHighlight.style.height = (height * s * 1.3) + 'px'; // pad for descenders
        } else {
            // Fallback: full-width bar
            this.pdfHighlight.style.top = (y * s - 10) + 'px';
            this.pdfHighlight.style.left = '0';
            this.pdfHighlight.style.width = '100%';
            this.pdfHighlight.style.height = '20px';
        }

        // Only show the overlay rectangle when text-span highlighting didn't match
        if (!skipVisual) {
            this.pdfHighlight.classList.remove('active');
            void this.pdfHighlight.offsetWidth;  // force reflow to restart animation
            this.pdfHighlight.classList.add('active');
        }

        // Scroll viewer pane to show the highlight (always, regardless of skipVisual)
        if (this.viewerPane) {
            const containerTop = this.container.offsetTop;
            const scrollTarget = parseFloat(this.pdfHighlight.style.top);
            this.viewerPane.scrollTo({
                top: containerTop + scrollTarget - this.viewerPane.clientHeight / 2,
                behavior: 'smooth'
            });
        }
    }

    // -----------------------------------------------------------------------
    // Shared text-layer highlighting — works with both #text-layer and
    // per-page .page-text-layer elements
    // -----------------------------------------------------------------------

    _highlightTextInLayer(layer, x, y, width, height) {
        if (!layer) {
            _dbg('highlight: layer not available');
            return false;
        }

        // Clear any previous text highlights in this layer
        layer.querySelectorAll('.synctex-text-highlight')
            .forEach(el => el.classList.remove('synctex-text-highlight'));

        const s = this.scale;
        const spans = layer.querySelectorAll(
            'span:not(.markedContent):not(.endOfContent)');
        if (spans.length === 0) {
            _dbg('highlight: 0 text spans');
            return false;
        }

        const hasPreciseBox = (width && width > 0 && height && height > 0);
        _dbg(`highlight: ${spans.length} spans, hasPreciseBox=${hasPreciseBox} (w=${width}, h=${height}), scale=${s.toFixed(2)}`);
        let matched = 0;

        if (hasPreciseBox) {
            // PATH A: Precise bounding box — use box overlap
            const boxTop = (y - height) * s;
            const boxLeft = x * s;
            const boxRight = boxLeft + width * s;
            const boxBottom = boxTop + height * s * 1.3;  // pad for descenders

            _dbg(`highlight: PATH A — box: top=${boxTop.toFixed(1)} left=${boxLeft.toFixed(1)} right=${boxRight.toFixed(1)} bottom=${boxBottom.toFixed(1)}`);

            for (const span of spans) {
                const top = parseFloat(span.style.top) || 0;
                const left = parseFloat(span.style.left) || 0;
                const spanHeight = span.offsetHeight;
                const scaleX = parseFloat(
                    span.style.transform?.match(/scaleX\(([^)]+)\)/)?.[1]) || 1;
                const spanWidth = span.offsetWidth * scaleX;

                if (top < boxBottom && (top + spanHeight) > boxTop &&
                    left < boxRight && (left + spanWidth) > boxLeft) {
                    span.classList.add('synctex-text-highlight');
                    matched++;
                }
            }

            _dbg(`highlight: PATH A matched ${matched}/${spans.length} spans`);
        } else {
            // PATH B: Only y (and maybe x) — line-based matching
            const targetY = y * s;

            // Compute tolerance from actual span heights
            let avgHeight = 0;
            let count = 0;
            for (const span of spans) {
                const h = span.offsetHeight;
                if (h > 0) { avgHeight += h; count++; }
            }
            const tolerance = count > 0 ? (avgHeight / count) * 0.6 : 15;

            const targetX = (x && x > 0) ? x * s : 0;

            _dbg(`highlight: PATH B — targetY=${targetY.toFixed(1)}, tolerance=${tolerance.toFixed(1)}, targetX=${targetX.toFixed(1)}`);

            for (const span of spans) {
                const top = parseFloat(span.style.top) || 0;
                const spanHeight = span.offsetHeight;
                const spanMidY = top + spanHeight / 2;

                if (Math.abs(spanMidY - targetY) > tolerance) continue;

                // If x is specified, only highlight from that point onward
                if (targetX > 0) {
                    const left = parseFloat(span.style.left) || 0;
                    if (left + span.offsetWidth < targetX) continue;
                }

                span.classList.add('synctex-text-highlight');
                matched++;
            }

            _dbg(`highlight: PATH B matched ${matched}/${spans.length} spans`);
        }

        // Log sample span positions when nothing matched
        if (matched === 0) {
            const sample = Math.min(5, spans.length);
            const positions = [];
            for (let i = 0; i < sample; i++) {
                const sp = spans[i];
                positions.push(`top=${parseFloat(sp.style.top) || 0} left=${parseFloat(sp.style.left) || 0} h=${sp.offsetHeight}`);
            }
            _dbg(`highlight: 0 matches — first ${sample} span positions:`, positions.join('; '));
        }

        if (matched > 0) {
            if (this._highlightFadeTimer) clearTimeout(this._highlightFadeTimer);
            this._highlightFadeTimer = setTimeout(() => {
                layer.querySelectorAll('.synctex-text-highlight')
                    .forEach(el => el.classList.remove('synctex-text-highlight'));
                this._highlightFadeTimer = null;
            }, 8000);
        }

        return matched > 0;
    }

    // Legacy wrapper — old name calls new shared implementation
    _highlightTextAtPosition(x, y, width, height) {
        return this._highlightTextInLayer(this.textLayer, x, y, width, height);
    }

    prevPage() {
        if (this.currentPage > 1) {
            this.renderPage(this.currentPage - 1);
        }
    }

    nextPage() {
        if (this.currentPage < this.totalPages) {
            this.renderPage(this.currentPage + 1);
        }
    }

    gotoLine(line, file) {
        _dbg(`gotoLine: requesting forward sync for line=${line} file=${file || '(default)'}`);
        const base = window.TEXWATCH_BASE || '';
        const body = { line: line };
        if (file) body.file = file;
        fetch(`${base}/goto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(resp => {
            if (!resp.ok) {
                resp.json().then(data => {
                    console.warn('Forward SyncTeX failed:', data.error || resp.statusText);
                }).catch(() => {
                    console.warn('Forward SyncTeX failed:', resp.statusText);
                });
            }
        }).catch(err => {
            console.error('Forward SyncTeX request failed:', err);
        });
    }

    async forceCompile() {
        try {
            const base = window.TEXWATCH_BASE || '';
            await fetch(`${base}/compile`, { method: 'POST' });
        } catch (err) {
            console.error('Failed to trigger compile:', err);
        }
    }

    setViewerMode(mode) {
        this.viewerMode = mode;
        if (mode === 'markdown') {
            this.container.style.display = 'none';
            if (this.markdownPreview) this.markdownPreview.classList.remove('hidden');
        } else {
            this.container.style.display = '';
            if (this.markdownPreview) this.markdownPreview.classList.add('hidden');
        }
    }

    renderMarkdown(content) {
        if (!this.markdownPreview || typeof marked === 'undefined') return;
        try {
            this.markdownPreview.innerHTML = marked.parse(content);
        } catch (err) {
            console.error('Markdown rendering failed:', err);
        }
    }

    formatTimeAgo(date) {
        const seconds = Math.floor((new Date() - date) / 1000);

        if (seconds < 5) return 'just now';
        if (seconds < 60) return `${seconds}s ago`;
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
        return `${Math.floor(seconds / 3600)}h ago`;
    }

    // ===================================================================
    // Config cache, log viewer, and TODO panel
    // ===================================================================

    async _ensureConfig() {
        if (this._cachedConfig) return;
        try {
            const base = window.TEXWATCH_BASE || '';
            const resp = await fetch(`${base}/config`);
            if (resp.ok) {
                this._cachedConfig = await resp.json();
            }
        } catch (err) {
            console.error('Failed to fetch config:', err);
        }
    }

    toggleLogViewer() {
        if (!this.logViewer) return;
        const isHidden = this.logViewer.classList.contains('hidden');
        if (isHidden) {
            // Show log
            if (this.logContent) {
                this.logContent.textContent = this._lastLogOutput || '(no log output)';
            }
            this.logViewer.classList.remove('hidden');
        } else {
            this.logViewer.classList.add('hidden');
        }
    }

    async _fetchTodos() {
        try {
            const base = window.TEXWATCH_BASE || '';
            const resp = await fetch(`${base}/structure`);
            if (!resp.ok) return;
            const data = await resp.json();
            this._renderTodos(data.todos || []);
        } catch (err) {
            console.error('Failed to fetch structure:', err);
        }
    }

    _renderTodos(todos) {
        if (!this.todoList) return;
        this.todoList.innerHTML = '';
        if (todos.length === 0) return;

        for (const todo of todos) {
            const div = document.createElement('div');
            div.className = 'todo-item';

            const icon = document.createElement('span');
            icon.className = 'icon';
            icon.textContent = '\u2610';  // ballot box

            const text = document.createElement('span');
            text.className = 'message';
            text.textContent = `[${todo.tag}] ${todo.text}`;

            const location = document.createElement('span');
            location.className = 'location';
            location.textContent = `${todo.file}:${todo.line}`;

            div.appendChild(icon);
            div.appendChild(text);
            div.appendChild(location);

            // Click to navigate to file:line
            div.addEventListener('click', () => {
                // Load the file in the editor and scroll to line
                if (window.texwatchEditor) {
                    window.texwatchEditor.loadFile(todo.file).then(() => {
                        window.texwatchEditor.scrollToLine(todo.line);
                    });
                }
            });

            this.todoList.appendChild(div);
        }
    }

    // ===================================================================
    // Continuous scroll mode
    // ===================================================================

    toggleScrollMode() {
        if (this.scrollMode === 'page') {
            this.scrollMode = 'continuous';
            document.getElementById('btn-scroll-mode').classList.add('active');
            if (this.pdfDoc) this.initContinuousMode();
        } else {
            this.exitContinuousMode();
            this.scrollMode = 'page';
            document.getElementById('btn-scroll-mode').classList.remove('active');
            if (this.pdfDoc) this.renderPage(this.currentPage);
        }
    }

    async initContinuousMode() {
        if (!this.pdfDoc) return;
        _dbg(`initContinuousMode: ${this.totalPages} pages`);

        // Tear down any previous continuous state
        this._teardownContinuous();

        // Add continuous class to container
        this.container.classList.add('continuous');

        // Create page wrappers with correct dimensions (but no rendered content yet)
        this.pageElements = [];
        this.renderedPages = new Set();

        for (let i = 1; i <= this.totalPages; i++) {
            const page = await this.pdfDoc.getPage(i);
            const viewport = page.getViewport({ scale: this.scale });

            const wrapper = document.createElement('div');
            wrapper.className = 'page-wrapper';
            wrapper.dataset.pageNum = i;
            wrapper.style.width = viewport.width + 'px';
            wrapper.style.height = viewport.height + 'px';

            // Page number label (visible on hover)
            const label = document.createElement('div');
            label.className = 'page-number-label';
            label.textContent = `${i}`;
            wrapper.appendChild(label);

            this.container.appendChild(wrapper);
            this.pageElements.push(wrapper);
        }

        // Set up IntersectionObserver for lazy rendering
        this.setupIntersectionObserver();

        // Set up scroll tracking to update currentPage
        this.setupScrollTracking();

        // Scroll to current page
        if (this.currentPage > 1 && this.pageElements[this.currentPage - 1]) {
            this.pageElements[this.currentPage - 1].scrollIntoView({ behavior: 'instant' });
        }

        this.updatePageInfo();
    }

    setupIntersectionObserver() {
        if (this.observer) this.observer.disconnect();

        this.observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                const pageNum = parseInt(entry.target.dataset.pageNum, 10);
                if (entry.isIntersecting) {
                    this._queueRender(pageNum);
                    // Pre-render buffer pages
                    for (let delta = 1; delta <= 2; delta++) {
                        if (pageNum - delta >= 1) this._queueRender(pageNum - delta);
                        if (pageNum + delta <= this.totalPages) this._queueRender(pageNum + delta);
                    }
                }
            }
            // Unload distant pages for memory management
            this._unloadDistantPages();
        }, {
            root: this.viewerPane,
            rootMargin: '200px 0px',
            threshold: 0.01,
        });

        for (const wrapper of this.pageElements) {
            this.observer.observe(wrapper);
        }
    }

    _queueRender(pageNum) {
        if (this.renderedPages.has(pageNum)) return;
        this._renderQueue.add(pageNum);
        if (!this._rendering) this._processRenderQueue();
    }

    async _processRenderQueue() {
        this._rendering = true;
        while (this._renderQueue.size > 0) {
            const pageNum = this._renderQueue.values().next().value;
            this._renderQueue.delete(pageNum);
            if (!this.renderedPages.has(pageNum)) {
                await this.renderPageContinuous(pageNum);
            }
        }
        this._rendering = false;
    }

    async renderPageContinuous(pageNum) {
        if (!this.pdfDoc || this.renderedPages.has(pageNum)) return;

        const wrapper = this.pageElements[pageNum - 1];
        if (!wrapper) return;

        _dbg(`renderPageContinuous: page=${pageNum}`);

        const page = await this.pdfDoc.getPage(pageNum);
        const viewport = page.getViewport({ scale: this.scale });

        // Create canvas
        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;

        await page.render({
            canvasContext: canvas.getContext('2d'),
            viewport: viewport
        }).promise;

        // Create text layer
        const textLayerDiv = document.createElement('div');
        textLayerDiv.className = 'page-text-layer';
        textLayerDiv.style.width = viewport.width + 'px';
        textLayerDiv.style.height = viewport.height + 'px';
        textLayerDiv.style.setProperty('--scale-factor', viewport.scale);

        const textContent = await page.getTextContent();
        try {
            await pdfjsLib.renderTextLayer({
                textContentSource: textContent,
                container: textLayerDiv,
                viewport: viewport,
            }).promise;
        } catch (err) {
            console.error(`Failed to render text layer for page ${pageNum}:`, err);
        }

        // Create highlight overlay
        const highlight = document.createElement('div');
        highlight.className = 'page-highlight';

        // Insert rendered content (keep the page-number-label that's already there)
        wrapper.appendChild(canvas);
        wrapper.appendChild(textLayerDiv);
        wrapper.appendChild(highlight);

        this.renderedPages.add(pageNum);
    }

    _unloadDistantPages() {
        const maxDistance = 6;
        for (const pageNum of [...this.renderedPages]) {
            if (Math.abs(pageNum - this.currentPage) > maxDistance) {
                this.unloadPage(pageNum);
            }
        }
    }

    unloadPage(pageNum) {
        const wrapper = this.pageElements[pageNum - 1];
        if (!wrapper) return;

        // Remove rendered content but keep the wrapper (preserves height) and label
        const canvas = wrapper.querySelector('canvas');
        const textLayer = wrapper.querySelector('.page-text-layer');
        const highlight = wrapper.querySelector('.page-highlight');
        if (canvas) canvas.remove();
        if (textLayer) textLayer.remove();
        if (highlight) highlight.remove();

        this.renderedPages.delete(pageNum);
        _dbg(`unloadPage: page=${pageNum}`);
    }

    setupScrollTracking() {
        if (!this.viewerPane) return;

        const handler = () => {
            if (this._scrollTrackingTimer) return;
            this._scrollTrackingTimer = setTimeout(() => {
                this._scrollTrackingTimer = null;
                this.updateCurrentPageFromScroll();
            }, 100);
        };

        this.viewerPane.addEventListener('scroll', handler);
        // Store reference for cleanup
        this._scrollHandler = handler;
    }

    updateCurrentPageFromScroll() {
        if (!this.viewerPane || this.pageElements.length === 0) return;

        const viewportMid = this.viewerPane.scrollTop + this.viewerPane.clientHeight / 2;

        let best = 1;
        let bestDist = Infinity;

        for (let i = 0; i < this.pageElements.length; i++) {
            const wrapper = this.pageElements[i];
            const wrapperMid = wrapper.offsetTop + wrapper.offsetHeight / 2;
            const dist = Math.abs(wrapperMid - viewportMid);
            if (dist < bestDist) {
                bestDist = dist;
                best = i + 1;
            }
        }

        if (best !== this.currentPage) {
            this.currentPage = best;
            this.updatePageInfo();
            this.sendViewerState();
        }
    }

    goToPageContinuous(pageNum, y = null, x = null, width = null, height = null) {
        pageNum = Math.max(1, Math.min(pageNum, this.totalPages));
        const wrapper = this.pageElements[pageNum - 1];
        if (!wrapper) return;

        _dbg(`goToPageContinuous: page=${pageNum} y=${y}`);

        // Ensure page is rendered before scrolling
        const doScroll = () => {
            if (y !== null) {
                // Scroll to specific y position within the page
                const s = this.scale;
                const targetTop = wrapper.offsetTop + (y * s);
                this.viewerPane.scrollTo({
                    top: targetTop - this.viewerPane.clientHeight / 2,
                    behavior: 'smooth'
                });

                // Highlight on the specific page
                this._highlightOnPage(pageNum, x, y, width, height);
            } else {
                wrapper.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }

            this.currentPage = pageNum;
            this.updatePageInfo();
            this.sendViewerState();
        };

        if (this.renderedPages.has(pageNum)) {
            doScroll();
        } else {
            this.renderPageContinuous(pageNum).then(doScroll);
        }
    }

    _highlightOnPage(pageNum, x, y, width, height) {
        const wrapper = this.pageElements[pageNum - 1];
        if (!wrapper) return;

        const textLayer = wrapper.querySelector('.page-text-layer');
        const highlightDiv = wrapper.querySelector('.page-highlight');

        // Text-layer highlighting
        const textHighlighted = this._highlightTextInLayer(textLayer, x, y, width, height);

        // Overlay highlight
        if (highlightDiv) {
            const s = this.scale;
            const hasPreciseBox = (width && width > 0 && height && height > 0);

            if (hasPreciseBox) {
                highlightDiv.style.top = ((y - height) * s) + 'px';
                highlightDiv.style.left = (x * s) + 'px';
                highlightDiv.style.width = (width * s) + 'px';
                highlightDiv.style.height = (height * s * 1.3) + 'px';
            } else {
                highlightDiv.style.top = (y * s - 10) + 'px';
                highlightDiv.style.left = '0';
                highlightDiv.style.width = '100%';
                highlightDiv.style.height = '20px';
            }

            if (!textHighlighted) {
                highlightDiv.classList.remove('active');
                void highlightDiv.offsetWidth;  // force reflow to restart animation
                highlightDiv.classList.add('active');
            }
        }
    }

    _handleContinuousDblClick(e) {
        // Find which page-wrapper was clicked
        const wrapper = e.target.closest('.page-wrapper');
        if (!wrapper) return;

        const pageNum = parseInt(wrapper.dataset.pageNum, 10);
        const canvas = wrapper.querySelector('canvas');
        if (!canvas) return;

        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        _dbg(`continuous dblclick: page=${pageNum} pixel(${x.toFixed(1)}, ${y.toFixed(1)}) -> pdf(${(x / this.scale).toFixed(1)}, ${(y / this.scale).toFixed(1)})`);

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'click',
                page: pageNum,
                x: x / this.scale,
                y: y / this.scale
            }));
        }
    }

    _teardownContinuous() {
        // Disconnect observer
        if (this.observer) {
            this.observer.disconnect();
            this.observer = null;
        }

        // Remove scroll handler
        if (this._scrollHandler && this.viewerPane) {
            this.viewerPane.removeEventListener('scroll', this._scrollHandler);
            this._scrollHandler = null;
        }

        if (this._scrollTrackingTimer) {
            clearTimeout(this._scrollTrackingTimer);
            this._scrollTrackingTimer = null;
        }

        // Remove all page wrappers
        for (const wrapper of this.pageElements) {
            wrapper.remove();
        }
        this.pageElements = [];
        this.renderedPages.clear();
        this._renderQueue.clear();
    }

    exitContinuousMode() {
        _dbg('exitContinuousMode');
        this._teardownContinuous();
        this.container.classList.remove('continuous');
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Show back-link when in multi-project mode
    if (window.TEXWATCH_BASE) {
        const backLink = document.getElementById('back-link');
        if (backLink) backLink.style.display = '';
    }

    window.viewer = new TexWatchViewer();
});
