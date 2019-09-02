import asyncio
import gc
import itertools
import logging
import os
import sys
import threading
import warnings
from types import TracebackType
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type, TypeVar


_T = TypeVar("_T")
logger = logging.getLogger(__name__)


def run(main: Awaitable[_T], *, debug: bool = False) -> _T:
    # Backport from python 3.7

    """Run a coroutine.

    This function runs the passed coroutine, taking care of
    managing the asyncio event loop and finalizing asynchronous
    generators.

    This function cannot be called when another asyncio event loop is
    running in the same thread.

    If debug is True, the event loop will be run in debug mode.

    This function always creates a new event loop and closes it at the end.
    It should be used as a main entry point for asyncio programs, and should
    ideally only be called once.

    Example:

        async def main():
            await asyncio.sleep(1)
            print('hello')

        asyncio.run(main())
    """
    try:
        current_loop = asyncio.get_event_loop()
        if current_loop.is_running():
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop"
            )
    except RuntimeError:
        # there is no current loop
        pass

    if not asyncio.iscoroutine(main):
        raise ValueError("a coroutine was expected, got {!r}".format(main))

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.set_debug(debug)
        main_task = loop.create_task(main)
        return loop.run_until_complete(main_task)
    finally:
        try:
            _cancel_all_tasks(loop, main_task)
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            # simple workaround for:
            # http://docs.aiohttp.org/en/stable/client_advanced.html#graceful-shutdown
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                loop.close()
                del loop
                gc.collect()


def _cancel_all_tasks(
    loop: asyncio.AbstractEventLoop, main_task: "asyncio.Task[_T]"
) -> None:
    if sys.version_info >= (3, 7):
        to_cancel = asyncio.all_tasks(loop)
    else:
        to_cancel = asyncio.Task.all_tasks(loop)
    if not to_cancel:
        return

    for task in to_cancel:
        task.cancel()

    loop.run_until_complete(
        asyncio.gather(*to_cancel, loop=loop, return_exceptions=True)
    )

    # temporary shut up the logger until aiohttp will be fixed
    # the message scares people :)
    return
    for task in to_cancel:
        if task.cancelled():
            continue
        if task.exception() is not None:
            if task is main_task:
                continue
            loop.call_exception_handler(
                {
                    "message": "unhandled exception during asyncio.run() shutdown",
                    "exception": task.exception(),
                    "task": task,
                }
            )


if sys.platform != "win32":
    from asyncio.unix_events import AbstractChildWatcher  # type: ignore

    _Callback = Callable[..., None]

    class ThreadedChildWatcher(AbstractChildWatcher):
        # Backport from Python 3.8

        """Threaded child watcher implementation.

        The watcher uses a thread per process
        for waiting for the process finish.

        It doesn't require subscription on POSIX signal
        but a thread creation is not free.

        The watcher has O(1) complexity, its performance doesn't depend
        on amount of spawn processes.
        """

        def __init__(self) -> None:
            self._pid_counter = itertools.count(0)
            self._threads: Dict[int, threading.Thread] = {}

        def close(self) -> None:
            pass

        def __enter__(self) -> "ThreadedChildWatcher":
            return self

        def __exit__(
            self,
            exc_type: Optional[Type[BaseException]],
            exc_val: Optional[BaseException],
            exc_tb: Optional[TracebackType],
        ) -> None:
            pass

        def __del__(self, _warn: Any = warnings.warn) -> None:
            threads = [
                thread for thread in list(self._threads.values()) if thread.is_alive()
            ]
            if threads:
                _warn(
                    f"{self.__class__} has registered but not finished child processes",
                    ResourceWarning,
                    source=self,
                )

        def add_child_handler(self, pid: int, callback: _Callback, *args: Any) -> None:
            loop = asyncio.get_event_loop()
            thread = threading.Thread(
                target=self._do_waitpid,
                name=f"waitpid-{next(self._pid_counter)}",
                args=(loop, pid, callback, args),
                daemon=True,
            )
            self._threads[pid] = thread
            thread.start()

        def remove_child_handler(self, pid: int) -> bool:
            # asyncio never calls remove_child_handler() !!!
            # The method is no-op but is implemented because
            # abstract base classe requires it
            return True

        def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
            pass

        def _do_waitpid(
            self,
            loop: asyncio.AbstractEventLoop,
            expected_pid: int,
            callback: _Callback,
            args: List[Any],
        ) -> None:
            assert expected_pid > 0

            try:
                pid, status = os.waitpid(expected_pid, 0)
            except ChildProcessError:
                # The child process is already reaped
                # (may happen if waitpid() is called elsewhere).
                pid = expected_pid
                returncode = 255
                logger.warning(
                    "Unknown child process pid %d, will report returncode 255", pid
                )
            else:
                returncode = _compute_returncode(status)
                if loop.get_debug():
                    logger.debug(
                        "process %s exited with returncode %s", expected_pid, returncode
                    )

            if loop.is_closed():
                logger.warning("Loop %r that handles pid %r is closed", loop, pid)
            else:
                loop.call_soon_threadsafe(callback, pid, returncode, *args)

            self._threads.pop(expected_pid)

    def _compute_returncode(status: int) -> int:
        if os.WIFSIGNALED(status):
            # The child process died because of a signal.
            return -os.WTERMSIG(status)
        elif os.WIFEXITED(status):
            # The child process exited (e.g sys.exit()).
            return os.WEXITSTATUS(status)
        else:
            # The child exited, but we don't understand its status.
            # This shouldn't happen, but if it does, let's just
            # return that status; perhaps that helps debug it.
            return status