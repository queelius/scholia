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
        this._pdfHighlightTimer = null;
        this._textHighlightTimer = null;

        // DOM elements
        this.canvas = document.getElementById('pdf-canvas');
        this.ctx = this.canvas.getContext('2d');
        this.container = document.getElementById('pdf-container');
        this.textLayer = document.getElementById('text-layer');
        this.markdownPreview = document.getElementById('markdown-preview');

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

        // Error panel toggle
        document.getElementById('error-header').addEventListener('click', () => {
            this.errorPanel.classList.toggle('collapsed');
        });

        // PDF double-click for SyncTeX reverse (target container, not canvas,
        // because the text layer sits on top of the canvas)
        this.container.addEventListener('dblclick', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            _dbg(`dblclick: pixel(${x.toFixed(1)}, ${y.toFixed(1)}) -> pdf(${(x / this.scale).toFixed(1)}, ${(y / this.scale).toFixed(1)}) page=${this.currentPage}`);

            // Send click to server for reverse sync
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'click',
                    page: this.currentPage,
                    x: x / this.scale,
                    y: y / this.scale
                }));
            }
        });

        // Keyboard navigation (guard against editor focus)
        document.addEventListener('keydown', (e) => {
            if (e.target.closest('.cm-editor')) return;
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

        // Forward SyncTeX: editor double-click → PDF navigates
        window.addEventListener('texwatch:goto-line', (e) => {
            this.gotoLine(e.detail.line);
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

            await this.renderPage(this.currentPage);

            // Highlight change effect
            this.container.classList.add('highlight-change');
            setTimeout(() => {
                this.container.classList.remove('highlight-change');
            }, 2000);

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
        this.pageInfo.textContent = `Page ${this.currentPage}/${this.totalPages}`;
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

    goToPage(pageNum, y = null, x = null, width = null, height = null) {
        _dbg(`goToPage: page=${pageNum} y=${y} x=${x} w=${width} h=${height}`);
        this.renderPage(pageNum).then(() => {
            if (y !== null) {
                const textHighlighted = this._highlightTextAtPosition(x, y, width, height);
                _dbg(`goToPage: textHighlighted=${textHighlighted}`);
                // Always call for scroll-into-view; skip the overlay visual when text spans matched
                this._showPdfHighlight(x, y, width, height, textHighlighted);
            }
        });
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
            void this.pdfHighlight.offsetWidth;  // force reflow
            this.pdfHighlight.classList.add('active');

            if (this._pdfHighlightTimer) clearTimeout(this._pdfHighlightTimer);
            this._pdfHighlightTimer = setTimeout(() => {
                this.pdfHighlight.classList.remove('active');
                this._pdfHighlightTimer = null;
            }, 2000);
        }

        // Scroll viewer pane to show the highlight (always, regardless of skipVisual)
        const viewerPane = document.getElementById('viewer-pane');
        if (viewerPane) {
            const containerTop = this.container.offsetTop;
            const scrollTarget = parseFloat(this.pdfHighlight.style.top);
            viewerPane.scrollTo({
                top: containerTop + scrollTarget - viewerPane.clientHeight / 2,
                behavior: 'smooth'
            });
        }
    }

    _highlightTextAtPosition(x, y, width, height) {
        if (!this.textLayer) {
            _dbg('highlight: textLayer not available');
            return false;
        }

        // Clear any previous text highlights
        this.textLayer.querySelectorAll('.synctex-text-highlight')
            .forEach(el => el.classList.remove('synctex-text-highlight'));

        if (this._textHighlightTimer) {
            clearTimeout(this._textHighlightTimer);
            this._textHighlightTimer = null;
        }

        const s = this.scale;
        const spans = this.textLayer.querySelectorAll(
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
            this._textHighlightTimer = setTimeout(() => {
                this.textLayer.querySelectorAll('.synctex-text-highlight')
                    .forEach(el => el.classList.remove('synctex-text-highlight'));
                this._textHighlightTimer = null;
            }, 2000);
        }

        return matched > 0;
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

    gotoLine(line) {
        _dbg(`gotoLine: requesting forward sync for line=${line}`);
        const base = window.TEXWATCH_BASE || '';
        fetch(`${base}/goto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ line: line })
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
