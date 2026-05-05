# texwatch

**Pull-request-style review for LaTeX papers, with Claude Code as the author.**

You read the rendered PDF in your browser. You drop comments on paragraphs, sections, or the paper as a whole. Claude Code reads the queue, edits the source, and replies with what changed. The PDF rebuilds in front of you. Repeat until done.

## Why this exists

texwatch v0.3.0 was a browser IDE that *also* had Claude Code integration. It tried to be a place you wrote LaTeX. It lost: VS Code, Overleaf, and Vim are all better text editors. The MCP integration was a sidecar.

v0.4.0 inverts the framing. **Claude Code is the only editor.** texwatch is the place you *review* its work. The browser viewer is read-only by design: you point at things, you describe what's wrong, and Claude does the rewriting.

## What it gives you

- **A live PDF preview** that auto-rebuilds on every save (yours or Claude's).
- **Comments anchored to PDF regions, sections, source ranges, or the paper as a whole.** Threaded: every comment is a conversation between you and Claude.
- **Anchor durability.** When Claude edits, line numbers shift; texwatch follows the content via snippet matching and structural anchors, and flags comments as `STALE` when it can't.
- **Structured compile errors** (file, line, message, and 5-line source context) so Claude doesn't have to grep the latexmk log.
- **MCP tools** for paper state, recompile, and the comments queue. Claude Code reads the queue and treats each comment as a task.

## Install

Requires Python 3.10+ and `latexmk` (or `pdflatex`/`xelatex`/`lualatex`/`pandoc`) on your `PATH`.

```bash
pip install texwatch
pip install texwatch[mcp]   # add MCP server for Claude Code
```

## Quick start

```bash
cd my-paper/
texwatch init       # writes .texwatch.yaml (configures main file, port)
texwatch            # starts the daemon, opens at http://localhost:8765
```

In the browser:

- The PDF appears on the left, the comments sidebar on the right.
- **Select text in the PDF** → a "Comment on selection" toolbar pops up. Click it to anchor a comment to that region. SyncTeX resolves the selection back to a source line range.
- **"+ Note"** in the top bar → paper-level comment ("the abstract is too long").
- **Paper tab** → list of sections with **"+ comment"** buttons → section-anchored comments.

## The Claude Code workflow

Once `texwatch` is running, the MCP server auto-registers in `.mcp.json`. Claude Code sees these tools:

| Tool | What it does |
|---|---|
| `texwatch_paper()` | Full structured paper state: sections (with line ranges), labels, citations, comment counts. |
| `texwatch_compile()` | Recompile and return structured errors with source context. |
| `texwatch_comments(status="open")` | Read the review queue. |
| `texwatch_comment(action, ...)` | Add/reply/resolve/dismiss/reopen/delete. |
| `texwatch_goto(target)` | Scroll the user's viewer to a section / page / line. |

The intended dialogue:

```
You:    [drop 8 comments on the PDF, then in Claude Code]
        "Process the open comments."

Claude: [calls texwatch_comments, reads the queue]
        [for each one: edits source via Edit tool, then calls
         texwatch_comment(action="resolve", id=..., summary="...", edits=[...])]
        [calls texwatch_compile to verify]

You:    [PDF rebuilds in your browser; sidebar updates]
        [reply to the ones that need more work]
```

## Comment anchors

Four kinds, with different staleness behavior:

| Anchor | Use when | How staleness is handled |
|---|---|---|
| `pdf_region` | You're reading the PDF and want to point at a paragraph. | SyncTeX resolves to a source range, then a content snippet is captured. If Claude rewrites the paragraph, the snippet match fails and the comment goes `STALE`. |
| `section` | "Expand the methods section." | Resolved by section title or `\label{...}`. Stale only if the section is removed or renamed. |
| `source_range` | You're reading source and know the lines. CLI: `--source intro.tex:42-58` | Same snippet-match as `pdf_region`. |
| `paper` | Global note about the paper. | Never stale. |

## CLI

```
texwatch                        # serve (default)
texwatch init                   # scaffold .texwatch.yaml
texwatch compile                # one-shot compile, structured errors
texwatch goto "Related Work"    # tell the running viewer to scroll
texwatch mcp                    # run the MCP server (stdio)

texwatch comment add "abstract is too long" --paper
texwatch comment add "expand this" --section "Methods"
texwatch comment add "rephrase" --source intro.tex:42-58
texwatch comment list [--status open|resolved|dismissed|all]
texwatch comment show c-7f2a
texwatch comment resolve c-7f2a "broke into two paragraphs" --edit "intro.tex:42-58 -> :42-78"
texwatch comment dismiss c-7f2a "no longer relevant"
texwatch comment reopen c-7f2a
texwatch comment delete c-7f2a
```

Comments live in `.texwatch/comments.json`. `git add` it to keep your review history with the paper.

## What's intentionally missing in v0.4.0

- **No editor pane.** v0.3.0 had CodeMirror, autocomplete, snippets, file tree. Gone. Claude Code is the editor.
- **No multi-project workspace.** One paper per `texwatch` process.
- **No bibliography/environment/digest dashboards.** Folded into one `texwatch_paper()` MCP call. If you want richer analysis, the data is there for Claude to slice.
- **No SQLite compile history.** Each compile result stands on its own; the comments thread captures the review history.
- **No file-save endpoint.** Claude Code edits source files directly via its own `Edit`/`Write` tools. The watcher picks up changes automatically.

## Configuration

`.texwatch.yaml`:

```yaml
main: paper.tex
watch: ["*.tex", "*.bib", "*.md"]
ignore: ["*_backup.tex"]
compiler: auto       # auto | latexmk | pdflatex | xelatex | lualatex | pandoc
port: 8765
```

## License

MIT. See `LICENSE`.
