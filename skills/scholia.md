---
name: scholia
description: Use when working on TeX/LaTeX documents to view PDF output, navigate to lines, or check compile status
---

# scholia - TeX File Watcher Integration

This skill provides integration with the scholia tool for TeX/LaTeX document editing.

## Quick Reference

```bash
# Check if scholia is running and get status
scholia status

# Navigate PDF to a specific line
scholia goto 42

# Navigate to a specific page
scholia goto p5

# Navigate to a section by name
scholia goto "Introduction"

# Force recompile
scholia compile

# Capture PDF page as PNG
scholia capture output.png --page 1 --dpi 150
```

## Commands

### Check Status

Get the current compile state, errors, warnings, and viewer position:

```bash
scholia status
scholia status --json
```

This shows:
- Main file being watched
- Whether compilation is in progress
- Last compile success/failure
- Current errors and warnings
- Viewer page and visible source lines

### Navigate to Source Line

Jump the PDF viewer to show where a specific source line renders:

```bash
scholia goto <target>
```

Examples:
- `scholia goto 42` - Go to line 42 of the main file
- `scholia goto p3` - Go to page 3
- `scholia goto "Introduction"` - Go to section matching "Introduction"

### View Errors

When compilation fails or produces warnings:

```bash
scholia status --json | jq '.errors'
```

Or use the `/errors` endpoint for errors with source context:

```bash
curl localhost:8765/errors
```

### Force Recompile

Trigger recompilation manually (normally automatic on file save):

```bash
scholia compile
```

### Capture PDF Page

Screenshot a PDF page to a PNG file:

```bash
scholia capture output.png
scholia capture output.png --page 2 --dpi 300
```

## MCP Tools

scholia provides an MCP (Model Context Protocol) server that allows Claude Code to interact with the running scholia instance directly.

### Setup

Add to `.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "scholia": {
      "command": "scholia",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

Or with a custom port:

```json
{
  "mcpServers": {
    "scholia": {
      "command": "scholia",
      "args": ["mcp", "--port", "9000"],
      "env": {}
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `scholia_status` | Compilation status, errors, warnings |
| `scholia_context` | What the user is looking at (editor + viewer + section) |
| `scholia_errors` | Errors with source context lines |
| `scholia_structure` | Paper outline: sections, TODOs, inputs, word count |
| `scholia_goto` | Navigate to line, page, or section |
| `scholia_compile` | Trigger recompilation |
| `scholia_capture` | Screenshot current PDF page as PNG |
| `scholia_source` | Read source file content |

All tools accept `port` (default: 8765) and `project` (optional) parameters.

#### scholia_status

Returns JSON with compile state, errors, warnings, viewer position, and editor state.

#### scholia_context

Returns a combined snapshot of what the user is currently working on:
- Editor position (file and line)
- Viewer state (page, total pages, visible lines)
- Current section name (based on editor cursor position)
- Error and warning counts
- Word count

#### scholia_errors

Returns errors and warnings from the last compilation, including source context lines around each error.

#### scholia_structure

Returns the full document structure:
- Sections (with level, title, file, line)
- TODOs found in comments
- Input/include files
- Word count

#### scholia_goto

Navigate the PDF viewer. Accepts exactly one of:
- `line`: Jump to where a source line renders in the PDF
- `page`: Jump to a specific page number
- `section`: Jump to a section by name (case-insensitive substring match)

#### scholia_compile

Triggers a recompilation and returns the result including success status, errors, and warnings.

#### scholia_capture

Screenshots the current PDF page as a PNG image. Parameters:
- `page`: Page number (default: viewer's current page)
- `dpi`: Resolution (default: 150, range: 72-600)

Returns the image as base64-encoded PNG data.

#### scholia_source

Reads source file content from the project. Parameters:
- `file`: File path relative to project root (default: main file)

## Workflow Tips

### Starting a Session

1. Navigate to your TeX project directory
2. Run `scholia serve` (or just `scholia` if .scholia.yaml exists)
3. Open http://localhost:8765 in a browser
4. Edit .tex files - PDF auto-reloads on save

### Using with Claude Code

When editing TeX files:
1. Use `scholia_context` to understand what the user is looking at
2. Use `scholia_errors` to check for compile errors with context
3. Use `scholia_structure` to understand the document outline
4. Use `scholia_goto` to navigate the viewer to specific locations
5. Use `scholia_capture` to see what the PDF looks like

### SyncTeX Navigation

- Click anywhere in the PDF to see the corresponding source line in the status bar
- Use `scholia goto <line>` to jump from source to PDF position
- The viewer reports visible source line ranges for context

## Configuration

scholia uses `.scholia.yaml` in the project root:

```yaml
main: main.tex
watch:
  - "*.tex"
  - "sections/*.tex"
ignore:
  - "*_backup.tex"
compiler: latexmk  # or pdflatex, xelatex, lualatex
port: 8765
```

Create with: `scholia init`

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | JSON with compile state, errors, viewer position |
| `/context` | GET | Editor + viewer state, current section, word count |
| `/errors` | GET | Errors and warnings with source context |
| `/structure` | GET | Document outline: sections, TODOs, inputs, word count |
| `/goto` | POST | Navigate: `{"line": N}`, `{"page": N}`, or `{"section": "..."}` |
| `/compile` | POST | Force recompile, returns result |
| `/capture` | GET | Screenshot PDF page as PNG (params: page, dpi) |
| `/source` | GET | Read source file content (param: file) |
| `/source` | POST | Write source file content |
| `/config` | GET | Current configuration |
| `/pdf` | GET | Serve the compiled PDF |
| `/files` | GET | Project file tree |
| `/projects` | GET | List all projects (multi-project mode) |

All per-project endpoints are also available under `/p/{project_name}/`.

## Troubleshooting

**"No scholia instance running"**
- Start scholia: `scholia serve`

**"Compiler not found"**
- Install latexmk: `sudo apt install latexmk` or `brew install latexmk`
- Or use a different compiler in .scholia.yaml

**PDF not updating**
- Check for compile errors: `scholia status`
- Force recompile: `scholia compile`
- Check browser console for WebSocket issues

**Navigation not working**
- SyncTeX requires compilation with `-synctex=1` (enabled by default)
- Check that .synctex.gz file exists next to the PDF

**MCP server not connecting**
- Ensure scholia HTTP server is running: `scholia serve`
- Check the port matches: `scholia status -p 8765`
- Install MCP dependencies: `pip install scholia[mcp]`
