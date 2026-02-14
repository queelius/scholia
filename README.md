# texwatch

Live-reloading TeX editor with PDF viewer in the browser. Edit LaTeX, see the PDF update instantly, click between source and output.

## Why

Writing LaTeX means compiling, switching windows, scrolling to find your place, checking for errors, switching back, fixing, recompiling. Every cycle costs seconds that add up to hours.

texwatch collapses that loop into one command. Run `texwatch` in your project directory and it watches every `.tex` and `.bib` file, recompiles on save, and serves a split-pane editor+viewer at `localhost:8765`. SyncTeX links source lines to PDF positions bidirectionally — double-click a line to jump the PDF there, click the PDF to jump the editor back. Errors appear inline with surrounding source context, not buried in a log file.

What sets it apart:

- **Bidirectional SyncTeX** in the browser — no IDE plugin required
- **Semantic paper analysis** — bibliography health, environment inventory, document metadata, all available from the CLI or API
- **AI-native** — an MCP server gives Claude Code full read/write access to your paper state, so it can navigate, inspect, and edit your document directly
- **Multi-project** — serve an entire directory of papers from one process, each on its own route

## Install

**Requires Python 3.10+**

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

**Get a paper overview:**

```bash
texwatch dashboard              # unified view: health, sections, issues, bibliography
texwatch dashboard --section bibliography
texwatch bibliography           # shortcut for bibliography analysis
texwatch environments           # list all LaTeX environments
texwatch digest                 # document metadata (title, author, class)
```

**Screenshot a PDF page:**

```bash
texwatch capture output.png --page 1 --dpi 300
```

**Serve multiple projects:**

```bash
texwatch serve --recursive --dir ~/papers/
# Dashboard at http://localhost:8765, each project at /p/{name}/
texwatch current                # show which project is active
texwatch current other-paper    # switch to a different project
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

**Multi-paper projects**: Use the `papers:` key to define multiple compilable documents in one directory:

```yaml
papers:
  - name: main-paper
    main: paper.tex
  - name: supplementary
    main: supplement.tex
    compiler: pdflatex
watch:
  - "*.tex"
  - "*.bib"
```

Each paper appears as a separate project at `/p/{dirname}/{name}/`.

## Web UI features

- **Editor**: CodeMirror 6 with LaTeX syntax highlighting, word wrap toggle, conflict detection when files change on disk
- **Viewer**: PDF.js with continuous or paged scroll, SyncTeX highlight overlay
- **Error panel**: Compile errors/warnings with clickable source locations and surrounding context lines
- **TODO panel**: Collects `% TODO`, `% FIXME`, `\todo{}` annotations from all `.tex` files
- **Log viewer**: Full LaTeX compilation log, toggled from the error panel
- **File tree**: Navigate and open project files (`.tex`, `.bib`, `.cls`, `.sty`, etc.)
- **Structure**: Parses `\section`, `\input`/`\include` tree, word count (via `texcount`)

## Claude Code integration

texwatch includes an MCP server that gives Claude Code full access to your paper's state and source files. When you run `texwatch serve`, it auto-registers itself in your project's `.mcp.json` — no manual configuration needed.

If you prefer manual setup:

```json
// .claude/.mcp.json
{
  "mcpServers": {
    "texwatch": {
      "command": "texwatch",
      "args": ["mcp"]
    }
  }
}
```

### MCP tools

| Tool | Description |
|------|-------------|
| `texwatch` | Get complete paper state (health, sections, issues, bibliography, changes, environments, editor/viewer context, file tree, recent activity) |
| `texwatch_source` | Read source file content |
| `texwatch_history` | Get previous versions of a source file (saved before each write) |
| `texwatch_goto` | Navigate PDF viewer to a line, page, or section |
| `texwatch_compile` | Trigger recompilation |
| `texwatch_write_source` | Write content to a source file with conflict detection |
| `texwatch_capture` | Screenshot current PDF page as PNG (base64) |
| `texwatch_project` | Show or switch the current project |

This lets Claude see your full paper state — sections, compile errors, bibliography, word count — and navigate, recompile, or edit source files directly.

## CLI reference

| Command | Description |
|---------|-------------|
| `texwatch init` | Create `.texwatch.yaml` in current directory |
| `texwatch` / `texwatch serve` | Start the watcher and web server |
| `texwatch status` | Show compile status, errors, viewer state |
| `texwatch view` | Show editor and viewer pane state |
| `texwatch goto <target>` | Navigate to line, page (`p3`), or section name |
| `texwatch compile` | Trigger recompilation |
| `texwatch capture <file>` | Screenshot PDF page to PNG |
| `texwatch config` | View or modify `.texwatch.yaml` |
| `texwatch files` | List project file tree |
| `texwatch dashboard` | Unified paper dashboard (health, sections, issues, bibliography, changes, environments) |
| `texwatch bibliography` | Show bibliography analysis |
| `texwatch environments` | List LaTeX environments |
| `texwatch digest` | Show document metadata (title, author, class) |
| `texwatch activity` | Show recent activity events |
| `texwatch current` | Show or switch the current project |
| `texwatch scan <dir>` | Find projects with `.texwatch.yaml` |
| `texwatch mcp` | Run MCP stdio server for Claude Code |

Most commands accept `--port`, `--json`, and `--project` flags.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General failure (compile error, file not found, etc.) |
| 2 | Argument parsing error |
| 3 | Server not running (for commands that require a running server) |

## HTTP API

When `texwatch serve` is running, the following endpoints are available. For multi-project mode, prefix with `/p/{project_name}`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Compile status, errors, viewer/editor state |
| `/compile` | POST | Trigger recompilation |
| `/goto` | POST | Navigate to `{"line": N}`, `{"page": N}`, or `{"section": "..."}` |
| `/pdf` | GET | Serve the compiled PDF |
| `/source` | GET | Get source file content (`?file=...`) |
| `/source` | POST | Update source file (`{"file": "...", "content": "..."}`) |
| `/files` | GET | Project file tree |
| `/errors` | GET | Current errors and warnings |
| `/context` | GET | Editor/viewer state + current section + word count |
| `/structure` | GET | Document structure (sections, TODOs, inputs) |
| `/capture` | GET | Screenshot PDF page as PNG (`?page=N&dpi=N`) |
| `/dashboard` | GET | Unified paper state (all sections combined) |
| `/bibliography` | GET | Bibliography entries and citation analysis |
| `/environments` | GET | LaTeX environment inventory |
| `/digest` | GET | Document metadata (title, author, document class) |
| `/activity` | GET | Recent activity events |
| `/history/{file}` | GET | Previous versions of a source file |
| `/config` | GET | Project configuration |
| `/current` | GET | Current project name |
| `/current` | POST | Switch current project |
| `/ws` | WebSocket | Real-time updates (compile events, navigation) |
| `/projects` | GET | List all projects (multi-project mode) |

## License

MIT
