/**
 * texwatch editor module — CodeMirror 6 with SyncTeX and conflict resolution.
 *
 * Loaded as ES module. Communicates with viewer.js via CustomEvents.
 */

import { EditorView, basicSetup } from "codemirror";
import { EditorState, StateEffect, StateField, Compartment } from "@codemirror/state";
import { keymap, Decoration } from "@codemirror/view";
import { StreamLanguage } from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";
import { oneDark } from "@codemirror/theme-one-dark";

// ─── SyncTeX highlight decoration ────────────────────────────────────────────

const addHighlight = StateEffect.define();
const clearHighlight = StateEffect.define();

const highlightField = StateField.define({
    create() {
        return Decoration.none;
    },
    update(decos, tr) {
        for (const e of tr.effects) {
            if (e.is(addHighlight)) {
                return e.value;
            }
            if (e.is(clearHighlight)) {
                return Decoration.none;
            }
        }
        return decos;
    },
    provide: (f) => EditorView.decorations.from(f),
});

const synctexMark = Decoration.line({ class: "synctex-highlight" });

// ─── Word wrap compartment ───────────────────────────────────────────────────

const wrapCompartment = new Compartment();

// ─── Editor class ────────────────────────────────────────────────────────────

class TexWatchEditor {
    constructor() {
        /** @type {EditorView|null} */
        this.view = null;
        /** @type {string|null} */
        this.currentFile = null;
        /** @type {string|null} */
        this.baseMtimeNs = null;
        /** @type {boolean} */
        this.isDirty = false;
        /** @type {number|null} */
        this._highlightTimer = null;
        /** @type {number|null} */
        this._editorStateTimer = null;
        /** @type {boolean} */
        this.wordWrap = false;
    }

    async init() {
        const container = document.getElementById("editor-container");
        if (!container) return;

        this.view = new EditorView({
            state: EditorState.create({
                doc: "",
                extensions: [
                    basicSetup,
                    StreamLanguage.define(stex),
                    oneDark,
                    highlightField,
                    wrapCompartment.of([]),
                    keymap.of([
                        {
                            key: "Mod-s",
                            run: () => {
                                this.saveFile();
                                return true;
                            },
                        },
                        {
                            key: "Mod-Enter",
                            run: () => {
                                const line = this.view.state.doc.lineAt(
                                    this.view.state.selection.main.head
                                ).number;
                                window.dispatchEvent(
                                    new CustomEvent("texwatch:goto-line", {
                                        detail: { line },
                                    })
                                );
                                return true;
                            },
                        },
                    ]),
                    EditorView.updateListener.of((update) => {
                        if (update.docChanged) {
                            this.onDocChanged();
                        }
                    }),
                    EditorView.updateListener.of((update) => {
                        if (update.selectionSet) {
                            this._debouncedEmitEditorState();
                        }
                    }),
                    EditorView.domEventHandlers({
                        dblclick: (event, view) => {
                            this._handleTextDoubleClick(event, view);
                        },
                    }),
                ],
            }),
            parent: container,
        });

        // Load the default file
        await this.loadFile();

        // Wire up external events
        this._wireEvents();

        // Set up split-pane resizers
        this._setupResizer();
        this._setupTreeResizer();
    }

    async loadFile(file) {
        try {
            const base = window.TEXWATCH_BASE || '';
            // When file is specified, request it explicitly.
            // Otherwise omit ?file= and let the server default to the
            // configured main file.
            const url = file
                ? `${base}/source?file=${encodeURIComponent(file)}`
                : `${base}/source`;
            const resp = await fetch(url);
            if (!resp.ok) {
                console.error("Failed to load source:", resp.status);
                return;
            }

            const data = await resp.json();
            this.currentFile = data.file;
            this.baseMtimeNs = data.mtime_ns;

            // Replace editor content
            this.view.dispatch({
                changes: {
                    from: 0,
                    to: this.view.state.doc.length,
                    insert: data.content,
                },
            });

            this.isDirty = false;
            this._updateStatus();
            this._updateFilename();
            this.hideConflictBar();

            window.dispatchEvent(new CustomEvent('texwatch:file-loaded', {
                detail: { file: this.currentFile }
            }));

            this._emitEditorState();
        } catch (err) {
            console.error("Error loading file:", err);
        }
    }

    async saveFile() {
        if (!this.currentFile || !this.view) return;

        const content = this.view.state.doc.toString();
        const body = {
            file: this.currentFile,
            content: content,
        };
        if (this.baseMtimeNs) {
            body.base_mtime_ns = this.baseMtimeNs;
        }

        try {
            const base = window.TEXWATCH_BASE || '';
            const resp = await fetch(`${base}/source`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });

            if (resp.status === 409) {
                // Conflict
                const data = await resp.json();
                this.showConflictBar();
                return;
            }

            if (!resp.ok) {
                console.error("Save failed:", resp.status);
                return;
            }

            const data = await resp.json();
            this.baseMtimeNs = data.mtime_ns;
            this.isDirty = false;
            this._updateStatus();
        } catch (err) {
            console.error("Error saving file:", err);
        }
    }

    scrollToLine(line) {
        if (window.TEXWATCH_DEBUG) console.log('[texwatch] editor: scrollToLine(' + line + ')');
        if (!this.view) return;
        const doc = this.view.state.doc;
        if (line < 1 || line > doc.lines) return;

        const lineObj = doc.line(line);

        // Scroll into view
        this.view.dispatch({
            effects: EditorView.scrollIntoView(lineObj.from, { y: "center" }),
        });

        // Apply highlight decoration
        const deco = Decoration.set([synctexMark.range(lineObj.from)]);
        this.view.dispatch({
            effects: addHighlight.of(deco),
        });

        // Clear highlight after 2s
        if (this._highlightTimer) {
            clearTimeout(this._highlightTimer);
        }
        this._highlightTimer = setTimeout(() => {
            this.view.dispatch({
                effects: clearHighlight.of(null),
            });
            this._highlightTimer = null;
        }, 2000);
    }

    toggleWordWrap() {
        if (!this.view) return;
        this.wordWrap = !this.wordWrap;
        this.view.dispatch({
            effects: wrapCompartment.reconfigure(
                this.wordWrap ? EditorView.lineWrapping : []
            ),
        });
        const btn = document.getElementById("btn-word-wrap");
        if (btn) btn.classList.toggle("active", this.wordWrap);
    }

    onDocChanged() {
        this.isDirty = true;
        this._updateStatus();

        window.dispatchEvent(new CustomEvent('texwatch:content-changed', {
            detail: {
                file: this.currentFile,
                content: this.view.state.doc.toString(),
            }
        }));
    }

    onSourceUpdated(detail) {
        if (!detail || detail.file !== this.currentFile) return;

        // If mtime matches our base, this is our own save — ignore
        if (detail.mtime_ns === this.baseMtimeNs) return;

        if (!this.isDirty) {
            // Clean editor — auto-reload
            this.loadFile(this.currentFile);
        } else {
            // Dirty editor — show conflict bar
            this.showConflictBar();
        }
    }

    showConflictBar() {
        const bar = document.getElementById("conflict-bar");
        if (bar) bar.classList.remove("hidden");
    }

    hideConflictBar() {
        const bar = document.getElementById("conflict-bar");
        if (bar) bar.classList.add("hidden");
    }

    _emitEditorState() {
        if (!this.view || !this.currentFile) return;
        const pos = this.view.state.selection.main.head;
        const line = this.view.state.doc.lineAt(pos).number;
        window.dispatchEvent(new CustomEvent('texwatch:editor-state', {
            detail: { file: this.currentFile, line }
        }));
    }

    _debouncedEmitEditorState() {
        if (this._editorStateTimer) clearTimeout(this._editorStateTimer);
        this._editorStateTimer = setTimeout(() => {
            this._emitEditorState();
            this._editorStateTimer = null;
        }, 500);
    }

    // ─── Private helpers ──────────────────────────────────────────────────

    _wireEvents() {
        // Reverse SyncTeX: PDF click → editor scroll
        window.addEventListener("texwatch:source-position", (e) => {
            const { line } = e.detail;
            if (line) this.scrollToLine(line);
        });

        // Source updated (from watcher via WebSocket)
        window.addEventListener("texwatch:source-updated", (e) => {
            this.onSourceUpdated(e.detail);
        });

        // Conflict bar buttons
        const btnReload = document.getElementById("btn-reload");
        const btnKeep = document.getElementById("btn-keep");

        if (btnReload) {
            btnReload.addEventListener("click", () => {
                this.loadFile(this.currentFile);
            });
        }

        if (btnKeep) {
            btnKeep.addEventListener("click", () => {
                // Update baseMtimeNs so next save overwrites the external change.
                // We don't know the exact new mtime, but setting to null
                // means the next save won't check (force save).
                this.baseMtimeNs = null;
                this.hideConflictBar();
            });
        }

        const btnWrap = document.getElementById("btn-word-wrap");
        if (btnWrap) {
            btnWrap.addEventListener("click", () => this.toggleWordWrap());
        }
    }

    _handleTextDoubleClick(event, view) {
        // Skip gutter — preserve CM6 line-select on double-click
        if (event.target.closest(".cm-gutters")) return;

        const pos = view.posAtCoords({
            x: event.clientX,
            y: event.clientY,
        });
        if (pos === null) return;

        const line = view.state.doc.lineAt(pos).number;
        if (window.TEXWATCH_DEBUG) console.log('[texwatch] editor: dblclick -> line ' + line);

        // Dispatch forward SyncTeX event
        window.dispatchEvent(
            new CustomEvent("texwatch:goto-line", {
                detail: { line },
            })
        );
    }

    _updateStatus() {
        const el = document.getElementById("editor-status");
        if (!el) return;

        if (this.isDirty) {
            el.textContent = "(Modified)";
            el.classList.add("dirty");
        } else {
            el.textContent = "";
            el.classList.remove("dirty");
        }
    }

    _updateFilename() {
        const el = document.getElementById("editor-filename");
        if (el && this.currentFile) {
            el.textContent = this.currentFile;
        }
    }

    _setupResizer() {
        const handle = document.getElementById("split-handle");
        const editorPane = document.getElementById("editor-pane");
        const viewerPane = document.getElementById("viewer-pane");
        if (!handle || !editorPane || !viewerPane) return;

        let startX = 0;
        let startWidth = 0;

        const onMouseMove = (e) => {
            const dx = e.clientX - startX;
            const newWidth = Math.max(200, startWidth + dx);
            const mainEl = editorPane.parentElement;
            const maxWidth = mainEl.clientWidth - 200 - handle.offsetWidth;
            editorPane.style.flex = "none";
            editorPane.style.width = Math.min(newWidth, maxWidth) + "px";
        };

        const onMouseUp = () => {
            document.removeEventListener("mousemove", onMouseMove);
            document.removeEventListener("mouseup", onMouseUp);
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        };

        handle.addEventListener("mousedown", (e) => {
            e.preventDefault();
            startX = e.clientX;
            startWidth = editorPane.offsetWidth;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
            document.addEventListener("mousemove", onMouseMove);
            document.addEventListener("mouseup", onMouseUp);
        });
    }
    _setupTreeResizer() {
        const handle = document.getElementById("tree-handle");
        const treePane = document.getElementById("file-tree-pane");
        if (!handle || !treePane) return;

        let startX = 0;
        let startWidth = 0;

        const onMouseMove = (e) => {
            const dx = e.clientX - startX;
            const newWidth = Math.max(120, Math.min(startWidth + dx, 500));
            treePane.style.width = newWidth + "px";
        };

        const onMouseUp = () => {
            document.removeEventListener("mousemove", onMouseMove);
            document.removeEventListener("mouseup", onMouseUp);
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        };

        handle.addEventListener("mousedown", (e) => {
            e.preventDefault();
            startX = e.clientX;
            startWidth = treePane.offsetWidth;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
            document.addEventListener("mousemove", onMouseMove);
            document.addEventListener("mouseup", onMouseUp);
        });
    }
}

// ─── Initialize ──────────────────────────────────────────────────────────────

const editor = new TexWatchEditor();
editor.init().catch((err) => console.error("Editor init failed:", err));

window.texwatchEditor = editor;
