/**
 * texwatch file tree component — project file browser in left pane.
 */

class TexWatchFileTree {
    constructor() {
        this.container = document.getElementById('tree-container');
        this.treePane = document.getElementById('file-tree-pane');
        this.toggleBtn = document.getElementById('btn-toggle-tree');
        this._activeFile = null;
        this._refreshTimer = null;
    }

    async init() {
        if (!this.container) return;

        // Toggle collapse
        if (this.toggleBtn) {
            this.toggleBtn.addEventListener('click', () => {
                this.treePane.classList.toggle('collapsed');
                this.toggleBtn.textContent = this.treePane.classList.contains('collapsed') ? '\u25b6' : '\u25c0';
            });
        }

        // Observe editor filename changes to track active file
        const filenameEl = document.getElementById('editor-filename');
        if (filenameEl) {
            const observer = new MutationObserver(() => {
                this.setActiveFile(filenameEl.textContent);
            });
            observer.observe(filenameEl, { childList: true, characterData: true, subtree: true });
        }

        // Refresh tree when source is updated (file changes on disk)
        window.addEventListener('texwatch:source-updated', () => {
            this._debouncedRefresh();
        });

        await this.refresh();
    }

    async refresh() {
        try {
            const base = window.TEXWATCH_BASE || '';
            const resp = await fetch(`${base}/files`);
            if (!resp.ok) return;

            const data = await resp.json();
            this.container.innerHTML = '';
            this._renderChildren(data.children, this.container, 0);

            // Re-apply active highlight
            if (this._activeFile) {
                this.setActiveFile(this._activeFile);
            }
        } catch (err) {
            console.error('Failed to load file tree:', err);
        }
    }

    _debouncedRefresh() {
        if (this._refreshTimer) clearTimeout(this._refreshTimer);
        this._refreshTimer = setTimeout(() => {
            this.refresh();
            this._refreshTimer = null;
        }, 500);
    }

    _renderChildren(nodes, parent, depth) {
        if (!nodes) return;
        for (const node of nodes) {
            if (node.type === 'directory') {
                this._renderDirectory(node, parent, depth);
            } else {
                this._renderFile(node, parent, depth);
            }
        }
    }

    _renderFile(node, parent, depth) {
        const item = document.createElement('div');
        item.className = 'tree-item tree-file';
        item.style.paddingLeft = (8 + depth * 16) + 'px';
        item.dataset.path = node.path;

        const icon = document.createElement('span');
        icon.className = 'tree-icon';
        icon.textContent = this._fileIcon(node.name);

        const label = document.createElement('span');
        label.textContent = node.name;

        item.appendChild(icon);
        item.appendChild(label);

        item.addEventListener('click', () => {
            if (window.texwatchEditor) {
                window.texwatchEditor.loadFile(node.path);
            }
        });

        parent.appendChild(item);
    }

    _renderDirectory(node, parent, depth) {
        const wrapper = document.createElement('div');
        wrapper.className = 'tree-dir';

        const item = document.createElement('div');
        item.className = 'tree-item';
        item.style.paddingLeft = (8 + depth * 16) + 'px';

        const icon = document.createElement('span');
        icon.className = 'tree-icon dir-icon';
        icon.textContent = '\u25be';

        const label = document.createElement('span');
        label.textContent = node.name;

        item.appendChild(icon);
        item.appendChild(label);

        item.addEventListener('click', () => {
            wrapper.classList.toggle('collapsed');
            icon.textContent = wrapper.classList.contains('collapsed') ? '\u25b8' : '\u25be';
        });

        wrapper.appendChild(item);

        const children = document.createElement('div');
        children.className = 'tree-children';
        this._renderChildren(node.children, children, depth + 1);
        wrapper.appendChild(children);

        parent.appendChild(wrapper);
    }

    setActiveFile(path) {
        this._activeFile = path;
        // Remove existing active
        const prev = this.container.querySelector('.tree-item.active');
        if (prev) prev.classList.remove('active');

        // Find and highlight matching file
        const items = this.container.querySelectorAll('.tree-file');
        for (const item of items) {
            if (item.dataset.path === path) {
                item.classList.add('active');
                break;
            }
        }
    }

    _fileIcon(name) {
        const ext = name.substring(name.lastIndexOf('.')).toLowerCase();
        switch (ext) {
            case '.tex': return 'T';
            case '.md':
            case '.markdown': return 'M';
            case '.txt': return '\u00b6';
            case '.bib': return 'B';
            case '.sty':
            case '.cls': return 'S';
            case '.lua': return 'L';
            default: return '\u00b7';
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const fileTree = new TexWatchFileTree();
    fileTree.init().catch(err => console.error('File tree init failed:', err));
    window.texwatchFileTree = fileTree;
});
