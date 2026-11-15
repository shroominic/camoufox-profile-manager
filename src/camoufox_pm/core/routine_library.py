"""Read-only, allowlisted summaries of an external recorded routine catalog.

Only taxonomy and numeric aggregates leave this boundary. Recorded labels,
source paths, profile identifiers, clip contents and input values never do.
"""

import json
import math
from pathlib import Path

MAX_CATALOG_BYTES = 16 * 1024 * 1024
TAXONOMY = {
    "account-menu": ("Open account menu", "click"),
    "address-focus": ("Focus address bar", "click"),
    "appearance-light": ("Choose light appearance", "click"),
    "back": ("Go back on the page", "click"),
    "change-view": ("Change view", "click"),
    "click-unknown": ("Unidentified click", "click"),
    "close-panel": ("Close a panel", "click"),
    "compose-focus": ("Focus post composer", "click"),
    "delete": ("Delete action", "click"),
    "dismiss-notice": ("Dismiss notice", "click"),
    "drag": ("Recorded drag", "drag"),
    "follow-account": ("Follow an account", "click"),
    "keyboard-backspace": ("Keyboard backspace", "keyboard"),
    "keyboard-enter": ("Keyboard enter", "keyboard"),
    "keyboard-left": ("Keyboard left", "keyboard"),
    "keyboard-right": ("Keyboard right", "keyboard"),
    "login": ("Sign in to an existing X account", "login"),
    "login-continue": ("Continue login", "click"),
    "login-email-focus": ("Focus login identifier", "click"),
    "manage-timelines": ("Manage timelines", "click"),
    "navigate-home": ("Open Home", "click"),
    "not-interested": ("Mark not interested", "click"),
    "open-affiliates": ("Open Affiliates list", "click"),
    "open-content-or-account": ("Open content or account", "click"),
    "open-posts": ("Open Posts tab", "click"),
    "open-profile": ("Open own profile", "click"),
    "open-replies": ("Open Replies tab", "click"),
    "open-reply": ("Open reply composer", "click"),
    "pin-topic": ("Pin a topic", "click"),
    "pointer-move": ("Move pointer", "pointer-move"),
    "reading-pause": ("Reading pause", "wait"),
    "refresh-new-posts": ("Show new posts", "click"),
    "scroll": ("Scroll with recorded pauses", "scroll"),
    "search-focus": ("Focus timeline search", "click"),
    "setup-profile": ("Open profile setup", "click"),
    "show-more": ("Show more content", "click"),
    "tab-new": ("Open browser tab", "click"),
    "tab-select": ("Select browser tab", "click"),
    "topic-tab": ("Select topic tab", "click"),
    "upgrade": ("Open upgrade page", "click"),
}
LENGTHS = ("any", "short", "medium", "long")
DIRECTIONS = ("none", "down", "up", "mixed", "horizontal")


def _empty(status: str) -> dict:
    return {
        "status": status,
        "readOnly": True,
        "routineCount": 0,
        "variantCount": 0,
        "readyCount": 0,
        "reviewCount": 0,
        "candidateCount": 0,
        "routines": [],
    }


def _count(value) -> int:
    if type(value) is not int or not 0 <= value <= 100_000_000:
        raise ValueError("Invalid count")
    return value


def summarize_catalog(document: dict) -> dict:
    """Validate metadata and return only fixed strings and numeric aggregates."""
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported catalog")
    routines = document.get("routines")
    if not isinstance(routines, list) or len(routines) > len(TAXONOMY):
        raise ValueError("Invalid routines")
    result = _empty("ready")
    seen = set()
    for routine in routines:
        if not isinstance(routine, dict):
            raise ValueError("Invalid routine")
        key = routine.get("id")
        if not isinstance(key, str) or key not in TAXONOMY or key in seen:
            raise ValueError("Unknown or duplicate routine")
        seen.add(key)
        variants = routine.get("variants")
        if not isinstance(variants, list) or len(variants) > 10_000:
            raise ValueError("Invalid variants")
        pools = {}
        ready = 0
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError("Invalid variant")
            length = variant.get("length")
            direction = variant.get("direction", "none")
            duration = variant.get("durationSeconds")
            if (
                length not in LENGTHS
                or direction not in DIRECTIONS
                or type(duration) not in (int, float)
                or not math.isfinite(duration)
                or not 0 <= duration <= 86_400
            ):
                raise ValueError("Invalid variant metadata")
            status = (
                "ready"
                if variant.get("replayReady") is True and variant.get("status") == "ready"
                else "review"
            )
            ready += status == "ready"
            pool_key = (length, direction, status)
            pool = pools.setdefault(
                pool_key,
                {
                    "length": length,
                    "direction": direction,
                    "status": status,
                    "count": 0,
                    "minSeconds": duration,
                    "maxSeconds": duration,
                },
            )
            pool["count"] += 1
            pool["minSeconds"] = min(pool["minSeconds"], duration)
            pool["maxSeconds"] = max(pool["maxSeconds"], duration)
        title, kind = TAXONOMY[key]
        candidates = _count(routine.get("candidateCount", 0))
        item = {
            "key": key,
            "title": title,
            "kind": kind,
            "parameterized": key == "login" and routine.get("status") == "parameterized",
            "variantCount": len(variants),
            "readyCount": ready,
            "reviewCount": len(variants) - ready,
            "candidateCount": candidates,
            "pools": [pools[key] for key in sorted(pools)],
        }
        result["routines"].append(item)
        for name in ("variantCount", "readyCount", "reviewCount", "candidateCount"):
            result[name] += item[name]
    result["routines"].sort(key=lambda item: item["title"].casefold())
    result["routineCount"] = len(result["routines"])
    return result


def read_catalog(configured_path: str | None) -> dict:
    """Read one bounded JSON snapshot; failure responses contain no local details."""
    if not configured_path:
        return _empty("not-configured")
    try:
        path = Path(configured_path).expanduser()
        if not path.is_file():
            return _empty("missing")
        with path.open("rb") as handle:
            payload = handle.read(MAX_CATALOG_BYTES + 1)
        if len(payload) > MAX_CATALOG_BYTES:
            return _empty("invalid")
        return summarize_catalog(json.loads(payload))
    except FileNotFoundError:
        return _empty("missing")
    except (OSError, ValueError, TypeError, RecursionError):
        return _empty("invalid")
