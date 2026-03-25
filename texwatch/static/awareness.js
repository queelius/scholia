/**
 * texwatch awareness module -- focus reporting and highlight/annotation rendering.
 *
 * Reports user cursor, selection, and viewport to the server via WebSocket.
 * Receives highlight and annotation commands from the server.
 */

(function () {
    "use strict";

    const DEBOUNCE_MS = 250;
    let _ws = null;
    let _debounceTimer = null;

    function sendFocus(data) {
        if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
        clearTimeout(_debounceTimer);
        _debounceTimer = setTimeout(() => {
            _ws.send(JSON.stringify(data));
        }, DEBOUNCE_MS);
    }

    function reportCursor(file, line, column) {
        sendFocus({ type: "focus", file, line, column });
    }

    function reportSelection(file, startLine, startCol, endLine, endCol) {
        sendFocus({
            type: "selection", file,
            start: { line: startLine, col: startCol },
            end: { line: endLine, col: endCol },
        });
    }

    function reportVisibleLines(file, start, end) {
        sendFocus({ type: "visible_lines", file, start, end });
    }

    function reportPdfViewport(page, scrollY) {
        sendFocus({ type: "pdf_viewport", page, scroll_y: scrollY });
    }

    // -- Highlight and annotation rendering --

    let _activeHighlights = {};
    let _activeAnnotations = {};

    function handleHighlights(data) {
        _activeHighlights[data.file] = data.ranges || [];
        document.dispatchEvent(new CustomEvent("texwatch:highlights", { detail: data }));
    }

    function handleAnnotations(data) {
        _activeAnnotations[data.file] = data.annotations || [];
        document.dispatchEvent(new CustomEvent("texwatch:annotations", { detail: data }));
    }

    function clearHighlights(file) {
        delete _activeHighlights[file];
        document.dispatchEvent(new CustomEvent("texwatch:highlights", {
            detail: { file, ranges: [] },
        }));
    }

    function handleWsMessage(data) {
        if (data.type === "highlights") handleHighlights(data);
        else if (data.type === "annotations") handleAnnotations(data);
    }

    function init(ws) {
        _ws = ws;
    }

    window.texwatchAwareness = {
        init,
        reportCursor,
        reportSelection,
        reportVisibleLines,
        reportPdfViewport,
        clearHighlights,
        handleWsMessage,
        getHighlights: (file) => _activeHighlights[file] || [],
        getAnnotations: (file) => _activeAnnotations[file] || [],
    };
})();
