"""Command-line interface for texwatch."""

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from . import __version__
from .config import Config, create_config, find_config, load_config, get_main_file
from .compiler import check_compiler_available
from .server import TexWatchServer


def get_status(port: int = 8765) -> dict | None:
    """Get status from running texwatch instance."""
    try:
        with urlopen(f"http://localhost:{port}/status", timeout=5) as response:
            return json.loads(response.read().decode())
    except URLError:
        return None


def send_goto(target: str, port: int = 8765) -> bool:
    """Send goto command to running instance."""
    try:
        # Determine if target is a line number or page
        data: dict
        if target.isdigit():
            data = {"line": int(target)}
        elif target.startswith("p") and target[1:].isdigit():
            data = {"page": int(target[1:])}
        else:
            data = {"section": target}

        req = Request(
            f"http://localhost:{port}/goto",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode())
            return result.get("success", False)
    except URLError:
        return False


def cmd_init(args: argparse.Namespace) -> int:
    """Handle --init command."""
    config_path = Path.cwd() / "texwatch.yaml"

    if config_path.exists() and not args.force:
        print(f"Config already exists: {config_path}")
        print("Use --force to overwrite")
        return 1

    # Find .tex files in current directory
    tex_files = list(Path.cwd().glob("*.tex"))
    main_file = "main.tex"

    if tex_files:
        # Prefer main.tex if it exists
        if Path("main.tex").exists():
            main_file = "main.tex"
        else:
            # Use the first .tex file found
            main_file = tex_files[0].name

    created_path = create_config(
        main=main_file,
        output_path=config_path,
    )

    print(f"Created config: {created_path}")
    print(f"Main file: {main_file}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Handle --status command."""
    status = get_status(args.port)

    if status is None:
        print(f"No texwatch instance running on port {args.port}")
        return 1

    print(f"File: {status['file']}")
    print(f"Compiling: {status['compiling']}")

    if status.get("last_compile"):
        print(f"Last compile: {status['last_compile']}")
        print(f"Success: {status['success']}")

    if status.get("errors"):
        print(f"\nErrors ({len(status['errors'])}):")
        for err in status["errors"]:
            line = f":{err['line']}" if err.get("line") else ""
            print(f"  {err['file']}{line} - {err['message']}")

    if status.get("warnings"):
        print(f"\nWarnings ({len(status['warnings'])}):")
        for warn in status["warnings"][:5]:  # Show first 5
            line = f":{warn['line']}" if warn.get("line") else ""
            print(f"  {warn['file']}{line} - {warn['message']}")
        if len(status["warnings"]) > 5:
            print(f"  ... and {len(status['warnings']) - 5} more")

    viewer = status.get("viewer", {})
    if viewer.get("page"):
        print(f"\nViewer: Page {viewer['page']}/{viewer.get('total_pages', '?')}")
        if viewer.get("visible_lines"):
            print(f"  Lines: {viewer['visible_lines'][0]}-{viewer['visible_lines'][1]}")

    return 0


def cmd_goto(args: argparse.Namespace) -> int:
    """Handle --goto command."""
    if send_goto(args.target, args.port):
        print(f"Navigated to: {args.target}")
        return 0
    else:
        print(f"Failed to navigate to: {args.target}")
        print("Is texwatch running?")
        return 1


def cmd_capture(args: argparse.Namespace) -> int:
    """Handle --capture command."""
    try:
        with urlopen(f"http://localhost:{args.port}/capture", timeout=10) as response:
            data = response.read()
            if response.headers.get("Content-Type") == "image/png":
                with open(args.output, "wb") as f:
                    f.write(data)
                print(f"Saved: {args.output}")
                return 0
            else:
                # It's probably JSON with an error
                result = json.loads(data.decode())
                print(f"Capture not available: {result.get('error', 'Unknown error')}")
                return 1
    except URLError as e:
        print(f"Failed to capture: {e}")
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    """Run the texwatch server."""
    # Load config
    main_file = args.main_file if hasattr(args, "main_file") and args.main_file else None
    config = load_config(main_file=main_file)

    # Override port if specified
    if args.port:
        config.port = args.port

    # Check main file exists
    main_path = get_main_file(config)
    if not main_path.exists():
        print(f"Error: Main file not found: {main_path}")
        print("Run 'texwatch --init' to create a config file")
        return 1

    # Check compiler is available
    if not check_compiler_available(config.compiler):
        print(f"Error: Compiler not found: {config.compiler}")
        print("Install latexmk or specify a different compiler in texwatch.yaml")
        return 1

    print(f"texwatch v{__version__}")
    print(f"Main file: {main_path}")
    print(f"Compiler: {config.compiler}")

    # Run server
    server = TexWatchServer(config)
    server.run(port=config.port)

    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="texwatch",
        description="TeX file watcher with browser-based PDF viewer",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"texwatch {__version__}",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8765,
        help="Server port (default: 8765)",
    )

    # Subcommands via mutually exclusive group
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--init",
        action="store_true",
        help="Create texwatch.yaml in current directory",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Query running texwatch instance",
    )
    group.add_argument(
        "--goto",
        metavar="TARGET",
        dest="goto_target",
        help="Navigate to line number, page (p3), or section",
    )
    group.add_argument(
        "--capture",
        metavar="FILE",
        dest="capture_output",
        help="Screenshot current view to PNG file",
    )

    # Positional argument for main file
    parser.add_argument(
        "main_file",
        nargs="?",
        help="Main .tex file (optional if texwatch.yaml exists)",
    )

    # Hidden args
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.init:
        return cmd_init(args)
    elif args.status:
        return cmd_status(args)
    elif args.goto_target:
        args.target = args.goto_target
        return cmd_goto(args)
    elif args.capture_output:
        args.output = args.capture_output
        return cmd_capture(args)
    else:
        return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
