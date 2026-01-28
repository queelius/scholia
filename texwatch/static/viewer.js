/**
 * texwatch PDF viewer with WebSocket live reload
 */

// PDF.js configuration
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

class TexWatchViewer {
    constructor() {
        this.pdfDoc = null;
        this.currentPage = 1;
        this.totalPages = 0;
        this.scale = 1.5;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;

        // DOM elements
        this.canvas = document.getElementById('pdf-canvas');
        this.ctx = this.canvas.getContext('2d');
        this.container = document.getElementById('pdf-container');
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

        // PDF click for SyncTeX reverse
        this.canvas.addEventListener('click', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

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

        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
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

        // Scroll-based page navigation
        this.container.addEventListener('wheel', (e) => {
            // Only if at top/bottom of container
            const atTop = this.container.scrollTop === 0;
            const atBottom = this.container.scrollTop + this.container.clientHeight >= this.container.scrollHeight;

            if (e.deltaY < 0 && atTop) {
                this.prevPage();
            } else if (e.deltaY > 0 && atBottom) {
                this.nextPage();
            }
        });
    }

    connectWebSocket() {
        const wsUrl = `ws://${window.location.host}/ws`;
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
                this.goToPage(data.page, data.y);
                break;
            case 'source_position':
                this.showSourcePosition(data);
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
            // Add cache-busting parameter
            const url = `/pdf?t=${Date.now()}`;
            this.pdfDoc = await pdfjsLib.getDocument(url).promise;
            this.totalPages = this.pdfDoc.numPages;

            // Get filename from status
            const response = await fetch('/status');
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
        this.lineInfo.textContent = `${data.file}:${data.line}`;
    }

    goToPage(pageNum, y = null) {
        this.renderPage(pageNum);
        // TODO: scroll to y position if provided
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
        fetch('/goto', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ line: line })
        });
    }

    async forceCompile() {
        try {
            await fetch('/compile', { method: 'POST' });
        } catch (err) {
            console.error('Failed to trigger compile:', err);
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
    window.viewer = new TexWatchViewer();
});
