---
name: texwatch
description: Use when working on TeX/LaTeX documents to view PDF output, navigate to lines, or check compile status
---

# texwatch - TeX File Watcher Integration

This skill provides integration with the texwatch tool for TeX/LaTeX document editing.

## Quick Reference

```bash
# Check if texwatch is running and get status
texwatch --status

# Navigate PDF to a specific line
texwatch --goto 42

# Navigate to a specific page
texwatch --goto p5

# Force recompile
curl -X POST localhost:8765/compile

# Get full status as JSON
curl localhost:8765/status
```

## Commands

### Check Status

Get the current compile state, errors, warnings, and viewer position:

```bash
texwatch --status
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
texwatch --goto <line_number>
```

Examples:
- `texwatch --goto 42` - Go to line 42 of the main file
- `texwatch --goto p3` - Go to page 3
- `texwatch --goto "Introduction"` - Search for section (not yet implemented)

### View Errors

When compilation fails or produces warnings, check the error panel:

```bash
texwatch --status | grep -A5 "Errors\|Warnings"
```

Or view in the browser UI - the error panel at the bottom shows all issues with clickable line numbers.

### Force Recompile

Trigger recompilation manually (normally automatic on file save):

```bash
curl -X POST localhost:8765/compile
```

## Workflow Tips

### Starting a Session

1. Navigate to your TeX project directory
2. Run `texwatch main.tex` (or just `texwatch` if .texwatch.yaml exists)
3. Open http://localhost:8765 in a browser
4. Edit .tex files - PDF auto-reloads on save

### Using with Claude Code

When editing TeX files:
1. Use `/texwatch status` to check if there are compile errors
2. Use `/texwatch goto <line>` to see what a specific line looks like in the PDF
3. Check the viewer position to understand what the user is looking at

### SyncTeX Navigation

- Click anywhere in the PDF to see the corresponding source line in the status bar
- Use `texwatch --goto <line>` to jump from source to PDF position
- The viewer reports visible source line ranges for context

## Configuration

texwatch uses `.texwatch.yaml` in the project root:

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

Create with: `texwatch --init`

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | JSON with compile state, errors, viewer position |
| `/goto` | POST | Navigate: `{"line": N}`, `{"page": N}`, or `{"section": "..."}` |
| `/compile` | POST | Force recompile |
| `/config` | GET | Current configuration |
| `/pdf` | GET | Serve the compiled PDF |

## Troubleshooting

**"No texwatch instance running"**
- Start texwatch: `texwatch main.tex`

**"Compiler not found"**
- Install latexmk: `sudo apt install latexmk` or `brew install latexmk`
- Or use a different compiler in .texwatch.yaml

**PDF not updating**
- Check for compile errors: `texwatch --status`
- Force recompile: `curl -X POST localhost:8765/compile`
- Check browser console for WebSocket issues

**Navigation not working**
- SyncTeX requires compilation with `-synctex=1` (enabled by default)
- Check that .synctex.gz file exists next to the PDF
