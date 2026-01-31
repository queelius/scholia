"""File system watcher for TeX files using watchdog."""

import asyncio
import fnmatch
import logging
from pathlib import Path
from typing import Callable, Coroutine

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class TexFileHandler(FileSystemEventHandler):
    """Handle file system events for TeX files."""

    def __init__(
        self,
        watch_patterns: list[str],
        ignore_patterns: list[str],
        callback: Callable[[str], Coroutine],
        loop: asyncio.AbstractEventLoop,
        debounce_seconds: float = 0.5,
    ):
        """Initialize handler.

        Args:
            watch_patterns: Glob patterns for files to watch.
            ignore_patterns: Glob patterns for files to ignore.
            callback: Async callback to invoke on changes.
            loop: Event loop to schedule callbacks on.
            debounce_seconds: Minimum time between callbacks.
        """
        super().__init__()
        self.watch_patterns = watch_patterns
        self.ignore_patterns = ignore_patterns
        self.callback = callback
        self.loop = loop
        self.debounce_seconds = debounce_seconds
        self._last_event_time: float = 0
        self._pending_task: asyncio.Task | None = None
        self._pending_path: str | None = None

    def _matches_patterns(self, path: str, patterns: list[str]) -> bool:
        """Check if path matches any of the patterns."""
        path_obj = Path(path)
        name = path_obj.name
        relative = str(path_obj)

        for pattern in patterns:
            # Try matching against filename and relative path
            if fnmatch.fnmatch(name, pattern):
                return True
            if fnmatch.fnmatch(relative, pattern):
                return True
            # Handle ** patterns
            if "**" in pattern:
                if fnmatch.fnmatch(relative, pattern):
                    return True

        return False

    def _should_process(self, path: str) -> bool:
        """Check if a file change should trigger recompilation."""
        # Ignore auxiliary files
        path_obj = Path(path)
        name = path_obj.name

        # Simple extensions (checked via suffix)
        aux_extensions = {
            ".aux", ".log", ".out", ".toc", ".lof", ".lot",
            ".bbl", ".blg", ".idx", ".ind", ".ilg",
            ".fls", ".fdb_latexmk", ".synctex",
            ".pdf", ".dvi", ".ps", ".gz",
        }
        if path_obj.suffix in aux_extensions:
            return False

        # Compound extensions (checked via endswith on full name)
        compound_extensions = (".synctex.gz",)
        if name.endswith(compound_extensions):
            return False

        # Check ignore patterns
        if self._matches_patterns(path, self.ignore_patterns):
            logger.debug(f"Ignoring {path} (matches ignore pattern)")
            return False

        # Check watch patterns
        if self._matches_patterns(path, self.watch_patterns):
            return True

        return False

    def _schedule_callback(self, src_path: str):
        """Schedule the callback with debouncing."""
        import time

        current_time = time.time()
        self._pending_path = src_path

        # Cancel any pending callback
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        async def delayed_callback():
            await asyncio.sleep(self.debounce_seconds)
            try:
                await self.callback(self._pending_path)
            except Exception as e:
                logger.error(f"Callback error: {e}")

        self._pending_task = asyncio.run_coroutine_threadsafe(
            delayed_callback(), self.loop
        ).result()

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification."""
        if event.is_directory:
            return
        if self._should_process(event.src_path):
            logger.info(f"File modified: {event.src_path}")
            self._schedule_callback(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation."""
        if event.is_directory:
            return
        if self._should_process(event.src_path):
            logger.info(f"File created: {event.src_path}")
            self._schedule_callback(event.src_path)


class TexWatcher:
    """Watch TeX files for changes and trigger recompilation."""

    def __init__(
        self,
        watch_dir: Path,
        watch_patterns: list[str],
        ignore_patterns: list[str],
        on_change: Callable[[str], Coroutine],
        debounce_seconds: float = 0.5,
    ):
        """Initialize watcher.

        Args:
            watch_dir: Directory to watch.
            watch_patterns: Glob patterns for files to watch.
            ignore_patterns: Glob patterns for files to ignore.
            on_change: Async callback when files change.
            debounce_seconds: Minimum time between callbacks.
        """
        self.watch_dir = watch_dir
        self.watch_patterns = watch_patterns
        self.ignore_patterns = ignore_patterns
        self.on_change = on_change
        self.debounce_seconds = debounce_seconds
        self._observer: Observer | None = None
        self._handler: TexFileHandler | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start watching for file changes.

        Args:
            loop: Event loop for scheduling async callbacks.
        """
        self._handler = TexFileHandler(
            watch_patterns=self.watch_patterns,
            ignore_patterns=self.ignore_patterns,
            callback=self.on_change,
            loop=loop,
            debounce_seconds=self.debounce_seconds,
        )

        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(self.watch_dir),
            recursive=True,
        )
        self._observer.start()
        logger.info(f"Started watching {self.watch_dir} for {self.watch_patterns}")

    def stop(self) -> None:
        """Stop watching for file changes."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("Stopped file watcher")

    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._observer is not None and self._observer.is_alive()
