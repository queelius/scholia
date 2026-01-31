# texwatch

Live-reloading TeX editor with PDF viewer in the browser. Edit LaTeX, see the PDF update instantly, click between source and output.

## Why

LaTeX toolchains are slow to set up and painful to iterate on. texwatch gives you a single command that watches your `.tex` files, recompiles on save, and serves a split-pane editor+viewer at `localhost:8765`. SyncTeX links source lines to PDF positions bidirectionally. Errors show up inline with surrounding source context. No IDE plugins to configure.

## Install

```bash
pip install texwatch
```

Optional extras:

```bash
pip install texwatch[capture]   # PDF screenshot support (pymupdf)
pip install texwatch[mcp]       # Claude Code integration (MCP server)
```

Requires `latexmk` (or `pdflatex`/`xelatex`/`lualatex`) on your PATH.

## Quick start

```bash
cd my-paper/
texwatch init          # creates .texwatch.yaml
texwatch               # starts server, opens at http://localhost:8765
```

The browser UI has three panes: file tree, CodeMirror editor, and PDF viewer. Save a file anywhere in the project and the PDF reloads automatically.

## Common use cases

**Navigate from source to PDF:**

Double-click a line in the editor, or press `Ctrl+Enter` to jump the PDF to that position.

**Navigate from PDF to source:**

Click anywhere in the PDF to scroll the editor to the corresponding source line.

**Jump to a section:**

```bash
texwatch goto "Related Work"    # fuzzy matches section titles
texwatch goto 42                # jump to line 42
texwatch goto p3                # jump to page 3
```

**Check compilation errors (from another terminal):**

```bash
texwatch status
texwatch compile --json
```

**Screenshot a PDF page:**

```bash
texwatch capture output.png --page 1 --dpi 300
```

**Serve multiple projects:**

```bash
texwatch serve --recursive --dir ~/papers/
# Dashboard at http://localhost:8765, each project at /p/{name}/
```

## Configuration

`.texwatch.yaml` in your project root:

```yaml
main: paper.tex
watch:
  - "*.tex"
  - "*.bib"
ignore:
  - "old_*.tex"
compiler: latexmk     # auto | latexmk | pdflatex | xelatex | lualatex
port: 8765
page_limit: 8         # optional: flag when PDF exceeds N pages
```

## Web UI features

- **Editor**: CodeMirror 6 with LaTeX syntax highlighting, word wrap toggle, conflict detection when files change on disk
- **Viewer**: PDF.js with continuous or paged scroll, SyncTeX highlight overlay
- **Error panel**: Compile errors/warnings with clickable source locations and surrounding context lines
- **TODO panel**: Collects `% TODO`, `% FIXME`, `\todo{}` annotations from all `.tex` files
- **Log viewer**: Full LaTeX compilation log, toggled from the error panel
- **File tree**: Navigate and open project files (`.tex`, `.bib`, `.cls`, `.sty`, etc.)
- **Structure**: Parses `\section`, `\input`/`\include` tree, word count (via `texcount`)

## Claude Code integration

texwatch includes an MCP server that exposes your document state to Claude Code.

```bash
# Add to .claude/.mcp.json:
{
  "mcpServers": {
    "texwatch": {
      "command": "texwatch",
      "args": ["mcp"]
    }
  }
}
```

Available tools: `texwatch_status`, `texwatch_context`, `texwatch_errors`, `texwatch_structure`, `texwatch_goto`, `texwatch_compile`, `texwatch_capture`, `texwatch_source`.

This lets Claude see your current page, section, compile errors (with source context), and word count -- and navigate or recompile for you.

## CLI reference

| Command | Description |
|---------|-------------|
| `texwatch init` | Create `.texwatch.yaml` in current directory |
| `texwatch` / `texwatch serve` | Start the watcher and web server |
| `texwatch status` | Show compile status, errors, viewer state |
| `texwatch goto <target>` | Navigate to line, page (`p3`), or section name |
| `texwatch compile` | Trigger recompilation |
| `texwatch capture <file>` | Screenshot PDF page to PNG |
| `texwatch config` | View or modify `.texwatch.yaml` |
| `texwatch files` | List project file tree |
| `texwatch scan <dir>` | Find projects with `.texwatch.yaml` |
| `texwatch mcp` | Run MCP stdio server for Claude Code |

Most commands accept `--port`, `--json`, and `--project` flags.

## License

MIT
