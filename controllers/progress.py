"""
Console progress helpers shared by the controller scripts.

Two output modes, chosen automatically:

* **Standalone (stdout is a tty)** — frames are redrawn in place with a
  carriage return, i.e. a normal animated progress bar.
* **Spawned by app.py (stdout is a pipe)** — the GUI reads our stdout
  line-by-line, so a bare '\\r' would never arrive until the process exits.
  Frames are instead tagged with PROGRESS_PREFIX and sent as ordinary lines;
  app.py strips the tag and does the in-place redraw on its own console.

ASCII only: the child's stdout encoding is the system locale (cp1252 on a
default Windows install), which cannot encode block-drawing characters.
"""
import sys
import time

BAR_WIDTH = 30
PROGRESS_PREFIX = "PROGRESS:"


def bar(fraction, width=BAR_WIDTH):
    """'[#########.....................]' for fraction in [0, 1]."""
    fraction = min(max(fraction, 0.0), 1.0)
    filled = int(round(width * fraction))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _emit(frame):
    """Render one animation frame."""
    if sys.stdout.isatty():
        sys.stdout.write("\r" + frame)
        sys.stdout.flush()
    else:
        print(f"{PROGRESS_PREFIX} {frame}", flush=True)


def _finish():
    """Close the animated line so the next print starts fresh."""
    if sys.stdout.isatty():
        sys.stdout.write("\n")
        sys.stdout.flush()


def countdown(seconds, label="Waiting", tick=0.1):
    """
    Sleep for `seconds`, animating a bar that fills as the time elapses and
    showing the time remaining. Uses time.monotonic() so the total wait is
    accurate regardless of how long each frame takes to draw.
    """
    if seconds <= 0:
        return

    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        _emit(f"{label} {bar(1 - remaining / seconds)} {remaining:5.1f}s left")
        time.sleep(min(tick, remaining))

    _emit(f"{label} {bar(1.0)}  done")
    _finish()


if __name__ == "__main__":
    countdown(3, "Demo")
