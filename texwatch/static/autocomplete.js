/**
 * texwatch autocomplete sources for CodeMirror 6.
 *
 * Exports raw completion source functions (NOT wrapped in autocompletion()).
 * editor.js combines these with the snippet source and calls autocompletion() once.
 */

import { completeFromList } from "@codemirror/autocomplete";

// ─── Common LaTeX commands ────────────────────────────────────────────────────

const LATEX_COMMANDS = [
    // Document structure
    "documentclass", "usepackage", "begin", "end",
    "title", "author", "date", "maketitle",
    "tableofcontents", "listoffigures", "listoftables",
    "appendix", "bibliography", "bibliographystyle",
    // Sectioning
    "part", "chapter", "section", "subsection", "subsubsection",
    "paragraph", "subparagraph",
    // Text formatting
    "textbf", "textit", "texttt", "textrm", "textsf", "textsc",
    "emph", "underline", "overline", "strikethrough",
    "tiny", "scriptsize", "footnotesize", "small", "normalsize",
    "large", "Large", "LARGE", "huge", "Huge",
    // Math
    "frac", "sqrt", "sum", "prod", "int", "oint",
    "lim", "inf", "sup", "min", "max",
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
    "theta", "iota", "kappa", "lambda", "mu", "nu", "xi",
    "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma",
    "Upsilon", "Phi", "Psi", "Omega",
    "mathbb", "mathbf", "mathit", "mathcal", "mathfrak", "mathrm",
    "left", "right", "cdot", "cdots", "ldots", "vdots", "ddots",
    "infty", "partial", "nabla", "forall", "exists", "in", "notin",
    "subset", "supset", "subseteq", "supseteq", "cup", "cap",
    "times", "div", "pm", "mp", "leq", "geq", "neq", "approx",
    "equiv", "sim", "simeq", "propto",
    "hat", "bar", "dot", "ddot", "tilde", "vec", "overrightarrow",
    // References
    "label", "ref", "eqref", "autoref", "pageref",
    "cite", "citep", "citet", "citeauthor", "citeyear",
    // Floats and cross-references
    "caption", "includegraphics",
    // Misc
    "newcommand", "renewcommand", "newenvironment",
    "hline", "cline", "multicolumn", "multirow",
    "hspace", "vspace", "hfill", "vfill",
    "noindent", "indent", "newline", "linebreak", "pagebreak", "newpage",
    "footnote", "footnotemark", "footnotetext",
    "textcolor", "colorbox", "fbox", "mbox", "makebox",
    "item", "setlength", "addtolength",
];

// Build completions list from the command array — triggers on backslash
const _commandCompletions = LATEX_COMMANDS.map((cmd) => ({
    label: `\\${cmd}`,
    type: "keyword",
}));

// ─── Cache for bibliography and label data ────────────────────────────────────

let _citationCache = null;
let _labelCache = null;

async function _fetchCitations(base) {
    try {
        const resp = await fetch(`${base}/bibliography`);
        if (!resp.ok) return [];
        const data = await resp.json();
        const entries = data.entries || [];
        return entries.map((e) => ({
            label: e.key,
            detail: (e.fields && e.fields.title) || e.entry_type || "",
            type: "variable",
        }));
    } catch (_err) {
        return [];
    }
}

async function _fetchLabels(base) {
    try {
        const resp = await fetch(`${base}/labels`);
        if (!resp.ok) return [];
        const items = await resp.json();
        return items.map((l) => ({
            label: l.key,
            detail: l.context || l.file || "",
            type: "variable",
        }));
    } catch (_err) {
        return [];
    }
}

// ─── Known LaTeX environments ─────────────────────────────────────────────────

const ENVIRONMENTS = [
    "document", "abstract", "titlepage",
    "figure", "figure*", "table", "table*",
    "equation", "equation*", "align", "align*",
    "gather", "gather*", "multline", "multline*",
    "split", "cases",
    "array", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
    "itemize", "enumerate", "description",
    "verbatim", "lstlisting", "minted",
    "proof", "theorem", "lemma", "corollary", "proposition",
    "definition", "remark", "example", "exercise",
    "minipage", "center", "flushleft", "flushright",
    "tabular", "tabular*", "tabularx",
    "tikzpicture", "scope",
    "frame", "block", "alertblock", "exampleblock",
    "columns", "column",
    "thebibliography",
];

// ─── Completion sources ───────────────────────────────────────────────────────

/**
 * Completion source for LaTeX commands.
 * Triggered when the cursor follows a backslash.
 */
function commandCompletion(context) {
    const word = context.matchBefore(/\\[a-zA-Z]*/);
    if (!word) return null;
    if (word.from === word.to && !context.explicit) return null;

    return {
        from: word.from,
        options: _commandCompletions,
        validFor: /^\\[a-zA-Z]*$/,
    };
}

/**
 * Walk backwards through text and find the innermost unmatched \begin{env}.
 * Uses a stack to handle nesting properly.
 */
function _findNearestUnmatchedBegin(text) {
    const pattern = /\\(begin|end)\{([^}]*)\}/g;
    const tokens = [];
    let m;
    while ((m = pattern.exec(text)) !== null) {
        tokens.push({ kind: m[1], env: m[2] });
    }

    const stack = [];
    for (let i = tokens.length - 1; i >= 0; i--) {
        const { kind, env } = tokens[i];
        if (kind === "end") {
            stack.push(env);
        } else {
            if (stack.length > 0 && stack[stack.length - 1] === env) {
                stack.pop();
            } else {
                return env;
            }
        }
    }
    return null;
}

/**
 * Completion source for LaTeX environments.
 * Triggered inside \begin{...} or \end{...}.
 */
function environmentCompletion(context) {
    const { state } = context;
    const pos = context.pos;
    const textBefore = state.sliceDoc(0, pos);

    const beginMatch = textBefore.match(/\\begin\{([^}]*)$/);
    const endMatch = textBefore.match(/\\end\{([^}]*)$/);

    if (!beginMatch && !endMatch) return null;

    const typed = beginMatch ? beginMatch[1] : endMatch[1];
    const from = pos - typed.length;

    if (endMatch) {
        const nearestEnv = _findNearestUnmatchedBegin(textBefore);
        const sortedEnvs = nearestEnv
            ? [nearestEnv, ...ENVIRONMENTS.filter((e) => e !== nearestEnv)]
            : ENVIRONMENTS;

        return {
            from,
            options: sortedEnvs.map((env, i) => ({
                label: env,
                type: "class",
                boost: i === 0 ? 99 : 0,
            })),
            validFor: /^[a-zA-Z*]*$/,
        };
    }

    // \begin{ case — auto-insert \end{envname} on accept
    return {
        from,
        options: ENVIRONMENTS.map((env) => ({
            label: env,
            type: "class",
            apply: (view, _completion, start, end) => {
                const insertText = `${env}}\n\n\\end{${env}}`;
                view.dispatch({
                    changes: { from: start, to: end, insert: insertText },
                    selection: { anchor: start + env.length + 2 },
                });
            },
        })),
        validFor: /^[a-zA-Z*]*$/,
    };
}

/**
 * Completion source for citation keys.
 * Triggered inside \cite{, \citep{, \citet{.
 */
function citationCompletion(context) {
    const { state } = context;
    const textBefore = state.sliceDoc(0, context.pos);
    const citeMatch = textBefore.match(/\\cite[pt]?\{([^}]*)$/);
    if (!citeMatch) return null;

    const keysTyped = citeMatch[1];
    const lastComma = keysTyped.lastIndexOf(",");
    const fragment = lastComma >= 0 ? keysTyped.slice(lastComma + 1).trimStart() : keysTyped;
    const from = context.pos - fragment.length;

    if (_citationCache === null) return null;

    return {
        from,
        options: _citationCache,
        validFor: /^[^},]*$/,
    };
}

/**
 * Completion source for label keys.
 * Triggered inside \ref{, \eqref{, \autoref{.
 */
function refCompletion(context) {
    const { state } = context;
    const textBefore = state.sliceDoc(0, context.pos);
    const refMatch = textBefore.match(/\\(?:ref|eqref|autoref)\{([^}]*)$/);
    if (!refMatch) return null;

    const fragment = refMatch[1];
    const from = context.pos - fragment.length;

    if (_labelCache === null) return null;

    return {
        from,
        options: _labelCache,
        validFor: /^[^}]*$/,
    };
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Returns array of all 4 raw completion source functions.
 * Triggers initial cache load for citations and labels.
 *
 * @param {string} base - API base URL (e.g. "" or "/p/myproject")
 * @returns {Function[]}
 */
export function getCompletionSources(base) {
    _fetchCitations(base).then((items) => { _citationCache = items; });
    _fetchLabels(base).then((items) => { _labelCache = items; });

    return [commandCompletion, environmentCompletion, citationCompletion, refCompletion];
}

/**
 * Re-fetches citation and label data, updating the caches.
 * Call this after a successful compile.
 *
 * @param {string} base - API base URL
 */
export async function refreshCaches(base) {
    const [citations, labels] = await Promise.all([
        _fetchCitations(base),
        _fetchLabels(base),
    ]);
    _citationCache = citations;
    _labelCache = labels;
}
