# texwatch

**Agentic-first PDF review for LaTeX papers, with Claude Code as the author.**

You read the rendered PDF in your browser. You drop comments on paragraphs, sections, or the paper as a whole. Claude Code reads the queue (via MCP), edits the source, and replies with what changed. The PDF rebuilds in front of you. Repeat until done.

## Why this exists

texwatch is deliberately *not* an editor, *not* an IDE, *not* an Overleaf clone. The agent (Claude Code) is already smarter at reading source, parsing LaTeX, grepping citations, and editing files than any tool we could build. So we don't try.

texwatch is a **substrate** for the agentic-first writing workflow:

- A **live PDF preview** the human can watch and gesture at.
- A **comment queue** anchored to PDF regions, sections, source ranges, or the paper as a whole.
- A **structured-error compile oracle** the agent calls when it wants ground truth.

That's it. Three responsibilities. Anything that re-implements something the agent does well (file parsing, log analysis, semantic understanding) was deliberately removed.

## Install

Requires Python 3.10+ and `latexmk` (or `pdflatex`/`xelatex`/`lualatex`/`pandoc`) on your `PATH`.

```bash
pip install texwatch
pip install texwatch[mcp]   # adds MCP server for Claude Code
```

## Quick start

```bash
cd my-paper/
texwatch init       # writes .texwatch.yaml (configures main file, port)
texwatch            # starts the daemon at http://localhost:8765
```

In the browser:

- The PDF appears on the left, the comments sidebar on the right.
- **Select text in the PDF** to anchor a comment to that region. SyncTeX maps the selection back to a source line range automatically.
- **"+ Note"** in the top bar for a paper-level comment ("the abstract is too long").
- **Paper tab** lists sections with **"+ comment"** buttons for section-level comments.
- **Reply / Resolve / Dismiss** are inline forms in each comment, not modals.

## The Claude Code workflow

`texwatch` auto-registers an MCP server in `.mcp.json` when it starts, exposing **4 tools**:

| Tool | What it does |
|---|---|
| `texwatch_paper(include_comments=True)` | Paper state in one call: sections (with line ranges), the comments queue, last-compile cache, main-file paths. |
| `texwatch_compile()` | Recompile and return structured errors with source context. |
| `texwatch_comment(action, ...)` | `add` / `reply` / `resolve` / `dismiss` / `delete`. |
| `texwatch_goto(target)` | Scroll the running viewer to a section / page / line / label. |

Notice what's absent: there's no `texwatch_labels()`, no `texwatch_citations()`, no `texwatch_environments()`. Use `Grep`. The agent is better at it than we are.

The intended dialogue:

```
You:    [drop 8 comments on the PDF, then in Claude Code]
        "Process the open comments."

Claude: texwatch_paper()             # one call, sees comments + sections
        for each open comment:
          Read source around comment.resolved_source
          Edit the source
          texwatch_comment(action="resolve", id=..., summary="...")
        texwatch_compile()           # verify the build

You:    [PDF rebuilds in your browser; sidebar updates over WebSocket]
        [reply or dismiss anything that needs more work]
```

## Comment anchors

Four kinds, with different staleness behavior:

| Anchor | Use when | Staleness handling |
|---|---|---|
| `pdf_region` | Reading the PDF and pointing at a paragraph. | SyncTeX resolves to source; a content snippet is captured; if Claude rewrites that region, the snippet match fails and the comment is flagged `STALE`. |
| `section` | "Expand the methods section." | Resolved by section title or `\label{...}`. Stale only if the section is removed or renamed. |
| `source_range` | When the agent already knows the lines (most common from MCP). | Snippet-matched, like `pdf_region`. |
| `paper` | Global note about the paper. | Never stale. |

## CLI

```
texwatch                 # serve (default)
texwatch init            # scaffold .texwatch.yaml
texwatch compile         # one-shot compile, structured errors
texwatch goto "Methods"  # tell the running viewer to scroll
texwatch mcp             # run the MCP server (stdio)
```

That's the whole CLI. Comment management lives in the browser (for humans) and in the MCP tools (for the agent). There is no `texwatch comment add` from the shell because nobody types that.

## What changed in v0.5.0

Aggressive simplification with the agentic-first frame:

- **Dropped the CLI comment surface entirely.** The agent and the browser are the only sane places to manage comments.
- **Dropped `tags`, `reopen`, the `Errors` tab.** Tags were noise, reopen was Github-imitation, the Errors tab duplicated information the topbar already shows.
- **Dropped `labels` / `citations` / `inputs` from `texwatch_paper()`.** Use `Grep`.
- **Folded `texwatch_comments` into `texwatch_paper(include_comments=True)`** for one-call orientation.
- **Inline reply / resolve / dismiss forms** in the viewer, not `prompt()` dialogs.
- **Compile lock** prevents the watcher and `texwatch_compile()` from racing.

Net code change: roughly −1200 lines across the project.

## Configuration

`.texwatch.yaml`:

```yaml
main: paper.tex
watch: ["*.tex", "*.bib", "*.md"]
ignore: ["*_backup.tex"]
compiler: auto       # auto | latexmk | pdflatex | xelatex | lualatex | pandoc
port: 8765
```

Comments live in `.texwatch/comments.json`. `git add` it to keep your review history with the paper.

## License

MIT. See `LICENSE`.
