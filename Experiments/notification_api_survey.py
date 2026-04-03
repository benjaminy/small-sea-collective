"""Survey of OS notification APIs on macOS.

Run with:
    python Experiments/notification_api_survey.py

Each approach is tried in sequence. Results are printed. You should see which
ones produce a visible notification and which fail silently or raise an error.
"""

import subprocess
import sys
import time


TITLE = "Small Sea Test"
MESSAGE = "PIN: 042 — notification survey"


def section(name: str):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def ok(msg=""):
    print(f"  [OK] {msg}")


def fail(msg=""):
    print(f"  [FAIL] {msg}")


def wait():
    """Give the OS a moment to display before moving on."""
    time.sleep(1.5)


# ------------------------------------------------------------------
# 1. plyer
# ------------------------------------------------------------------
section("1. plyer")
try:
    import plyer
    plyer.notification.notify(
        title=TITLE,
        message=MESSAGE,
        app_name="Small Sea",
        timeout=5,
    )
    wait()
    ok("plyer.notification.notify() returned without exception")
except ImportError:
    fail("plyer not installed — pip install plyer")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 2. osascript (display notification)
# ------------------------------------------------------------------
section("2. osascript — display notification")
try:
    result = subprocess.run(
        [
            "osascript", "-e",
            f'display notification "{MESSAGE}" with title "{TITLE}"',
        ],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        wait()
        ok("exit 0")
    else:
        fail(f"exit {result.returncode}: {result.stderr.strip()}")
except FileNotFoundError:
    fail("osascript not found (not macOS?)")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 3. osascript (display dialog — modal, always visible)
# ------------------------------------------------------------------
section("3. osascript — display dialog (modal, always visible)")
print("  NOTE: this will block until you click OK")
try:
    result = subprocess.run(
        [
            "osascript", "-e",
            f'display dialog "{MESSAGE}" with title "{TITLE}" buttons {{"OK"}} default button "OK"',
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        ok("dismissed")
    else:
        fail(f"exit {result.returncode}: {result.stderr.strip()}")
except FileNotFoundError:
    fail("osascript not found")
except subprocess.TimeoutExpired:
    fail("timed out (dialog not dismissed within 30s)")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 4. terminal-notifier (brew install terminal-notifier)
# ------------------------------------------------------------------
section("4. terminal-notifier")
try:
    result = subprocess.run(
        ["terminal-notifier", "-title", TITLE, "-message", MESSAGE, "-sound", "default"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        wait()
        ok("exit 0")
    else:
        fail(f"exit {result.returncode}: {result.stderr.strip()}")
except FileNotFoundError:
    fail("terminal-notifier not found — brew install terminal-notifier")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 5. pync (wraps terminal-notifier)
# ------------------------------------------------------------------
section("5. pync (wraps terminal-notifier)")
try:
    import pync
    pync.notify(MESSAGE, title=TITLE)
    wait()
    ok("pync.notify() returned without exception")
except ImportError:
    fail("pync not installed — pip install pync")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 6. PyObjC (native macOS, no external tools)
# ------------------------------------------------------------------
section("6. PyObjC — NSUserNotificationCenter (deprecated API, macOS < 12)")
try:
    from Foundation import NSUserNotification, NSUserNotificationCenter  # type: ignore
    note = NSUserNotification.alloc().init()
    note.setTitle_(TITLE)
    note.setInformativeText_(MESSAGE)
    center = NSUserNotificationCenter.defaultUserNotificationCenter()
    center.deliverNotification_(note)
    wait()
    ok("NSUserNotificationCenter.deliverNotification_() called")
except ImportError:
    fail("PyObjC not installed — pip install pyobjc-framework-Cocoa")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 7. PyObjC — UNUserNotificationCenter (modern API, macOS 10.14+)
# ------------------------------------------------------------------
section("7. PyObjC — UNUserNotificationCenter (modern API)")
try:
    import uuid
    from UserNotifications import (  # type: ignore
        UNMutableNotificationContent,
        UNNotificationRequest,
        UNUserNotificationCenter,
    )

    center = UNUserNotificationCenter.currentNotificationCenter()

    def _request_auth():
        import threading
        done = threading.Event()
        result = {}

        def handler(granted, error):
            result["granted"] = granted
            result["error"] = error
            done.set()

        center.requestAuthorizationWithOptions_completionHandler_(0b111, handler)
        done.wait(timeout=10)
        return result

    auth = _request_auth()
    if not auth.get("granted"):
        fail(f"Authorization not granted: {auth.get('error')}")
    else:
        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(TITLE)
        content.setBody_(MESSAGE)

        request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
            str(uuid.uuid4()), content, None
        )

        done_event = __import__("threading").Event()
        errors = {}

        def add_handler(error):
            errors["error"] = error
            done_event.set()

        center.addNotificationRequest_withCompletionHandler_(request, add_handler)
        done_event.wait(timeout=5)

        if errors.get("error"):
            fail(f"addNotificationRequest error: {errors['error']}")
        else:
            wait()
            ok("UNUserNotificationCenter request delivered")
except ImportError:
    fail("PyObjC UserNotifications not installed — pip install pyobjc-framework-UserNotifications")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
print(f"\n{'='*60}")
print("  Done. Check above for [OK] / [FAIL] per method.")
print(f"{'='*60}\n")
