"""LaTeX compiler wrapper with error/warning parsing."""

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


@dataclass
class CompileMessage:
    """A compiler error or warning."""

    file: str
    line: int | None
    message: str
    type: Literal["error", "warning"]


@dataclass
class CompileResult:
    """Result of a compilation run."""

    success: bool
    errors: list[CompileMessage] = field(default_factory=list)
    warnings: list[CompileMessage] = field(default_factory=list)
    output_file: Path | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.0
    log_output: str = ""


# Patterns for parsing LaTeX log output
ERROR_PATTERN = re.compile(
    r"^(?:!|(?P<file>[^\s:]+):(?P<line>\d+):)\s*(?P<msg>.+?)$",
    re.MULTILINE,
)
WARNING_PATTERN = re.compile(
    r"^(?:LaTeX|Package|Class)\s+(?:\w+\s+)?Warning:\s*(?P<msg>.+?)$",
    re.MULTILINE | re.IGNORECASE,
)
LINE_WARNING_PATTERN = re.compile(
    r"(?P<msg>.+?)\s+on\s+input\s+line\s+(?P<line>\d+)",
    re.IGNORECASE,
)
UNDERFULL_OVERFULL_PATTERN = re.compile(
    r"^(?P<type>Underfull|Overfull)\s+\\[hv]box\s+.+?at\s+lines?\s+(?P<line>\d+)",
    re.MULTILINE,
)


def _get_compiler_command(compiler: str, main_file: Path, work_dir: Path) -> list[str]:
    """Build the compiler command."""
    main_file_relative = main_file.relative_to(work_dir) if main_file.is_absolute() else main_file

    if compiler == "latexmk":
        return [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-synctex=1",
            "-file-line-error",
            str(main_file_relative),
        ]
    elif compiler in ("pdflatex", "xelatex", "lualatex"):
        return [
            compiler,
            "-interaction=nonstopmode",
            "-synctex=1",
            "-file-line-error",
            str(main_file_relative),
        ]
    else:
        raise ValueError(f"Unknown compiler: {compiler}")


def _parse_errors(log_output: str, main_file: str) -> list[CompileMessage]:
    """Parse errors from LaTeX log output."""
    errors = []

    # Look for standard LaTeX errors (! Error message)
    for match in re.finditer(r"^!\s*(.+?)$", log_output, re.MULTILINE):
        msg = match.group(1).strip()
        # Try to find the line number from context
        line = None
        context = log_output[max(0, match.start() - 200) : match.end() + 200]
        line_match = re.search(r"l\.(\d+)", context)
        if line_match:
            line = int(line_match.group(1))

        errors.append(CompileMessage(file=main_file, line=line, message=msg, type="error"))

    # Look for file:line:error format
    for match in re.finditer(r"^([^\s:]+\.tex):(\d+):\s*(.+?)$", log_output, re.MULTILINE):
        errors.append(
            CompileMessage(
                file=match.group(1),
                line=int(match.group(2)),
                message=match.group(3).strip(),
                type="error",
            )
        )

    return errors


def _parse_warnings(log_output: str, main_file: str) -> list[CompileMessage]:
    """Parse warnings from LaTeX log output."""
    warnings = []

    # Standard LaTeX/Package warnings
    for match in WARNING_PATTERN.finditer(log_output):
        msg = match.group("msg").strip()
        line = None
        line_match = LINE_WARNING_PATTERN.search(msg)
        if line_match:
            line = int(line_match.group("line"))
            msg = line_match.group("msg").strip()

        warnings.append(CompileMessage(file=main_file, line=line, message=msg, type="warning"))

    # Underfull/Overfull box warnings
    for match in UNDERFULL_OVERFULL_PATTERN.finditer(log_output):
        warnings.append(
            CompileMessage(
                file=main_file,
                line=int(match.group("line")),
                message=f"{match.group('type')} box",
                type="warning",
            )
        )

    return warnings


async def compile_tex(
    main_file: Path,
    compiler: str = "latexmk",
    work_dir: Path | None = None,
) -> CompileResult:
    """Compile a TeX file asynchronously.

    Args:
        main_file: Path to main .tex file.
        compiler: Compiler to use (latexmk, pdflatex, xelatex, lualatex).
        work_dir: Working directory for compilation. Defaults to main_file's parent.

    Returns:
        CompileResult with success status, errors, warnings, and output info.
    """
    if work_dir is None:
        work_dir = main_file.parent

    # Check if compiler exists
    if shutil.which(compiler.split()[0] if " " in compiler else compiler) is None:
        return CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file=str(main_file),
                    line=None,
                    message=f"Compiler '{compiler}' not found in PATH",
                    type="error",
                )
            ],
        )

    cmd = _get_compiler_command(compiler, main_file, work_dir)
    start_time = datetime.now(timezone.utc)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        stdout, _ = await process.communicate()
        log_output = stdout.decode("utf-8", errors="replace")
        end_time = datetime.now(timezone.utc)

        # Determine output PDF path
        pdf_name = main_file.stem + ".pdf"
        pdf_path = work_dir / pdf_name

        success = process.returncode == 0 and pdf_path.exists()

        errors = _parse_errors(log_output, str(main_file.name))
        warnings = _parse_warnings(log_output, str(main_file.name))

        return CompileResult(
            success=success,
            errors=errors,
            warnings=warnings,
            output_file=pdf_path if pdf_path.exists() else None,
            timestamp=end_time,
            duration_seconds=(end_time - start_time).total_seconds(),
            log_output=log_output,
        )

    except Exception as e:
        return CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file=str(main_file),
                    line=None,
                    message=f"Compilation failed: {e}",
                    type="error",
                )
            ],
        )


def check_compiler_available(compiler: str) -> bool:
    """Check if a compiler is available in PATH."""
    cmd = compiler.split()[0] if " " in compiler else compiler
    return shutil.which(cmd) is not None
