/**
 * texwatch snippet source for CodeMirror 6.
 *
 * Exports a raw completion source function (NOT wrapped in autocompletion()).
 * editor.js combines this with autocomplete sources and calls autocompletion() once.
 */

import { snippet } from "@codemirror/autocomplete";

// ─── Built-in snippet definitions ────────────────────────────────────────────
//
// Each entry: { trigger, expansion }
// Expansions use #{1}, #{2} for tab stops (CodeMirror 6 snippet syntax).

const BUILTIN_SNIPPETS = [
    {
        trigger: "fig",
        expansion: "\\begin{figure}[#{1:htbp}]\n  \\centering\n  \\includegraphics[width=#{2:0.8}\\linewidth]{#{3:filename}}\n  \\caption{#{4:Caption}}\n  \\label{fig:#{5:label}}\n\\end{figure}",
    },
    {
        trigger: "tab",
        expansion: "\\begin{table}[#{1:htbp}]\n  \\centering\n  \\caption{#{2:Caption}}\n  \\label{tab:#{3:label}}\n  \\begin{tabular}{#{4:cc}}\n    \\hline\n    #{5:Col1} & #{6:Col2} \\\\\\\\\n    \\hline\n    #{7} & #{8} \\\\\\\\\n    \\hline\n  \\end{tabular}\n\\end{table}",
    },
    {
        trigger: "eq",
        expansion: "\\begin{equation}\n  #{1}\n  \\label{eq:#{2:label}}\n\\end{equation}",
    },
    {
        trigger: "eqs",
        expansion: "\\begin{align}\n  #{1} &= #{2} \\label{eq:#{3:label}} \\\\\\\\\n  #{4} &= #{5}\n\\end{align}",
    },
    {
        trigger: "sec",
        expansion: "\\section{#{1:Title}}\n\\label{sec:#{2:label}}\n\n#{3}",
    },
    {
        trigger: "ssec",
        expansion: "\\subsection{#{1:Title}}\n\\label{sec:#{2:label}}\n\n#{3}",
    },
    {
        trigger: "sssec",
        expansion: "\\subsubsection{#{1:Title}}\n\\label{sec:#{2:label}}\n\n#{3}",
    },
    {
        trigger: "enum",
        expansion: "\\begin{enumerate}\n  \\item #{1:First item}\n  \\item #{2:Second item}\n\\end{enumerate}",
    },
    {
        trigger: "item",
        expansion: "\\begin{itemize}\n  \\item #{1:First item}\n  \\item #{2:Second item}\n\\end{itemize}",
    },
    {
        trigger: "mini",
        expansion: "\\begin{minipage}{#{1:0.48}\\linewidth}\n  #{2}\n\\end{minipage}",
    },
];

// ─── Helper: build completion options from snippet definitions ────────────────

/**
 * Convert an array of {trigger, expansion} objects into CodeMirror completion options.
 *
 * @param {Array<{trigger: string, expansion: string}>} defs
 * @returns {Object[]} - array of completion option objects
 */
function _defsToOptions(defs) {
    return defs.map(({ trigger, expansion }) => ({
        label: trigger,
        type: "text",
        detail: "snippet",
        apply: snippet(expansion),
    }));
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Creates a raw completion source function that offers built-in and custom snippets.
 *
 * @param {Array<{trigger: string, expansion: string}>} customSnippets
 * @returns {Function} - raw completion source function for use in autocompletion({ override: [...] })
 */
export function createSnippetSource(customSnippets) {
    const allOptions = _defsToOptions([...BUILTIN_SNIPPETS, ...customSnippets]);

    return function snippetSource(context) {
        const word = context.matchBefore(/\w+/);
        if (!word) return null;
        if (word.from === word.to && !context.explicit) return null;

        return {
            from: word.from,
            options: allOptions,
            validFor: /^\w*$/,
        };
    };
}

/**
 * Fetches custom snippets from the /config endpoint.
 * The config's snippets field is a dict mapping trigger -> expansion string.
 *
 * @param {string} base - API base URL (e.g. "" or "/p/myproject")
 * @returns {Promise<Array<{trigger: string, expansion: string}>>}
 */
export async function fetchCustomSnippets(base) {
    try {
        const resp = await fetch(`${base}/config`);
        if (!resp.ok) return [];
        const config = await resp.json();
        const snippets = config.snippets || {};
        return Object.entries(snippets).map(([trigger, expansion]) => ({
            trigger,
            expansion,
        }));
    } catch (_err) {
        return [];
    }
}
