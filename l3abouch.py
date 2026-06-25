#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║           L3ABOUCH SEARCHER  v3.0                   ║
║        Educational & Research Edition               ║
║       [ OSINT Phone Number Lookup Tool ]            ║
╚══════════════════════════════════════════════════════╝

Author  : L3ABOUCH
Version : 3.0
Refactor: Professional upgrade — single-file drop-in replacement
Platform: Kali Linux / Debian / Ubuntu / Termux / Python 3.8+

Improvements over v2.0
──────────────────────
  1. Eliminated 3× duplicated quit-block via _quit() helper
  2. Responsive separator — reads live terminal width (shutil)
  3. Narrow-terminal fallback banner (Termux / small windows)
  4. Animated spinner when opening browser tabs
  5. config.json — platforms fully editable without touching source
  6. Per-day rotating log file  →  logs/YYYY-MM-DD.log
  7. [H] Session history — view all searches done this run
  8. [E] Export — save session to timestamped .txt report
  9. Termux-aware browser launch (termux-open-url fallback)
 10. KeyboardInterrupt + EOFError caught at every input point
 11. Magic strings replaced with named constants
 12. pathlib.Path used throughout; no raw string path joins
"""

# ── Standard library ────────────────────────────────────────────────────────
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS — ANSI colors & styles
# ══════════════════════════════════════════════════════════════════════════════
RED      = "\033[91m"
GREEN    = "\033[92m"
YELLOW   = "\033[93m"
BLUE     = "\033[94m"
CYAN     = "\033[96m"
WHITE    = "\033[97m"
MAGENTA  = "\033[95m"
BOLD     = "\033[1m"
DIM      = "\033[2m"
RESET    = "\033[0m"

# ── Application metadata ─────────────────────────────────────────────────────
APP_NAME    = "L3ABOUCH SEARCHER"
APP_VERSION = "3.0"
APP_AUTHOR  = "L3ABOUCH"
APP_GITHUB  = "github.com/L3ABOUCH"

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOGS_DIR    = BASE_DIR / "logs"
EXPORTS_DIR = BASE_DIR / "exports"

# ── Minimum/maximum terminal widths for layout ───────────────────────────────
MIN_WIDTH       = 40
NARROW_THRESHOLD = 72   # below this we use the compact banner

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Default platform list — written to config.json on first run.
# Users can add/remove/reorder entries there without touching source.
DEFAULT_PLATFORMS: list[dict] = [
    {"name": "Facebook",   "query": '"{phone}" site:facebook.com'},
    {"name": "Instagram",  "query": '"{phone}" site:instagram.com'},
    {"name": "LinkedIn",   "query": '"{phone}" site:linkedin.com'},
    {"name": "Twitter/X",  "query": '"{phone}" site:twitter.com OR site:x.com'},
    {"name": "GitHub",     "query": '"{phone}" site:github.com'},
    {"name": "YouTube",    "query": '"{phone}" site:youtube.com'},
    {"name": "TikTok",     "query": '"{phone}" site:tiktok.com'},
    {"name": "Truecaller", "query": '"{phone}" site:truecaller.com'},
    {"name": "WhatsApp",   "query": '"{phone}" site:wa.me OR site:api.whatsapp.com'},
    {"name": "Pastebin",   "query": '"{phone}" site:pastebin.com'},
    {"name": "PDF Files",  "query": '"{phone}" filetype:pdf'},
    {"name": "All Web",    "query": '"{phone}"'},
]

DEFAULT_CONFIG: dict = {
    "platforms": DEFAULT_PLATFORMS,
    "search_engine": "https://www.google.com/search?q=",
    "tab_delay":     0.40,   # seconds between opening browser tabs
    "log_enabled":   True,
    "export_dir":    str(EXPORTS_DIR),
    "logs_dir":      str(LOGS_DIR),
}


def load_config() -> dict:
    """
    Load config.json if it exists; otherwise create it from defaults.
    Merges missing keys so partial configs stay valid after upgrades.
    """
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
        # Forward-compat: fill in any keys added in newer versions
        merged = {**DEFAULT_CONFIG, **user_cfg}
        return merged
    except (json.JSONDecodeError, OSError) as exc:
        print(f"{YELLOW}[!] config.json unreadable ({exc}). Using defaults.{RESET}")
        return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    """Persist config dict to config.json."""
    try:
        CONFIG_FILE.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"{RED}[!] Could not write config.json: {exc}{RESET}")


def build_platform_items(phone: str, cfg: dict) -> list[tuple[str, str]]:
    """
    Expand platform query templates with the actual phone number.
    Returns a list of (name, expanded_query) tuples.
    """
    engine_base = cfg.get("search_engine", DEFAULT_CONFIG["search_engine"])
    items = []
    for entry in cfg.get("platforms", DEFAULT_PLATFORMS):
        name  = entry.get("name", "Unknown")
        query = entry.get("query", '"{phone}"').replace("{phone}", phone)
        items.append((name, query, engine_base))
    return items   # list[tuple[name, query, engine_base]]


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — LOGGING
# ══════════════════════════════════════════════════════════════════════════════

_log_initialized = False


def init_logging(cfg: dict) -> None:
    """
    Set up a rotating daily log file in logs/ directory.
    Called once at startup if logging is enabled in config.
    """
    global _log_initialized
    if not cfg.get("log_enabled", True) or _log_initialized:
        return

    log_dir = Path(cfg.get("logs_dir", str(LOGS_DIR)))
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        filename  = log_file,
        level     = logging.INFO,
        format    = "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt   = "%H:%M:%S",
        encoding  = "utf-8",
    )
    logging.info("=" * 56)
    logging.info(f"{APP_NAME} v{APP_VERSION} — session started")
    logging.info("=" * 56)
    _log_initialized = True


def log(message: str) -> None:
    """Write to log file only if logging was initialised (no-op otherwise)."""
    if _log_initialized:
        logging.info(message)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — SESSION HISTORY
# ══════════════════════════════════════════════════════════════════════════════

# Each entry: {"ts": ISO-string, "phone": str, "platform": str, "url": str}
_session_history: list[dict] = []


def record_search(phone: str, platform: str, url: str) -> None:
    """Append a search event to the in-memory session history and log file."""
    entry = {
        "ts":       datetime.now().strftime("%H:%M:%S"),
        "phone":    phone,
        "platform": platform,
        "url":      url,
    }
    _session_history.append(entry)
    log(f"SEARCH | phone={phone} | platform={platform} | url={url}")


def export_session(cfg: dict) -> Path | None:
    """
    Write session history to a timestamped .txt file in exports/.
    Returns the Path on success, None on failure.
    """
    if not _session_history:
        return None

    export_dir = Path(cfg.get("export_dir", str(EXPORTS_DIR)))
    export_dir.mkdir(parents=True, exist_ok=True)

    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path  = export_dir / f"session_{ts}.txt"

    lines = [
        f"{APP_NAME} v{APP_VERSION} — Session Export",
        f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 56,
        "",
    ]
    for idx, entry in enumerate(_session_history, start=1):
        lines += [
            f"[{idx:03d}] {entry['ts']} | {entry['phone']} | {entry['platform']}",
            f"      {entry['url']}",
            "",
        ]

    try:
        out_path.write_text("\n".join(lines), encoding="utf-8")
        log(f"EXPORT | {out_path}")
        return out_path
    except OSError as exc:
        log(f"EXPORT FAILED | {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — TERMINAL UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def term_width() -> int:
    """Return usable terminal column count, clamped to MIN_WIDTH."""
    w = shutil.get_terminal_size(fallback=(80, 24)).columns
    return max(w, MIN_WIDTH)


def clear_screen() -> None:
    """Cross-platform terminal clear."""
    os.system("cls" if os.name == "nt" else "clear")


def separator(color: str = CYAN, char: str = "═") -> None:
    """
    Print a full-width separator that adapts to the current terminal width.
    Width is re-queried each call so resizing mid-session is handled gracefully.
    """
    print(f"{color}{char * term_width()}{RESET}")


def _wide_banner() -> str:
    """Full-width ASCII art banner (≥ 80 columns)."""
    return f"""\
{CYAN}
██╗     ██████╗      █████╗ ██████╗  ██████╗ ██╗   ██╗ ██████╗██╗  ██╗
██║     ╚════╝      ██╔══██╗██╔══██╗██╔═══██╗██║   ██║██╔════╝██║  ██║
██║      ╚███╗      ███████║██████╔╝██║   ██║██║   ██║██║     ███████║
██║      ╔══╝██╗    ██╔══██║██╔══██╗██║   ██║██║   ██║██║     ██╔══██║
███████╗██████╔╝    ██║  ██║██████╔╝╚██████╔╝╚██████╔╝╚██████╗██║  ██║
╚══════╝╚═════╝     ╚═╝  ╚═╝╚═════╝  ╚═════╝  ╚═════╝  ╚═════╝╚═╝  ╚═╝
{RESET}"""


def _narrow_banner() -> str:
    """Compact banner for Termux and small terminal windows (< 80 cols)."""
    w = term_width()
    title = f" {APP_NAME} v{APP_VERSION} "
    pad   = max((w - len(title)) // 2, 0)
    return (
        f"\n{CYAN}{'═' * w}{RESET}\n"
        f"{CYAN}{' ' * pad}{BOLD}{title}{RESET}\n"
        f"{CYAN}{'═' * w}{RESET}\n"
    )


def show_banner() -> None:
    """
    Display the appropriate banner based on terminal width, followed by
    the metadata box. Narrow terminals get the compact single-line banner.
    """
    clear_screen()
    if term_width() >= NARROW_THRESHOLD:
        print(_wide_banner())
    else:
        print(_narrow_banner())

    w      = term_width()
    box_w  = min(w - 2, 56)
    inner  = box_w - 2

    def box_line(text: str = "") -> str:
        return f"{MAGENTA}║{RESET}{BOLD}{text.center(inner)}{RESET}{MAGENTA}║{RESET}"

    top    = f"{MAGENTA}╔{'═' * inner}╗{RESET}"
    bottom = f"{MAGENTA}╚{'═' * inner}╝{RESET}"

    print(top)
    print(box_line(f"{APP_NAME}  v{APP_VERSION}"))
    print(box_line("Educational & Research Edition"))
    print(box_line("[ OSINT Phone Number Lookup Tool ]"))
    print(bottom)

    print(
        f"\n{DIM}{YELLOW}"
        f"  Author   : {APP_AUTHOR}\n"
        f"  Version  : {APP_VERSION}\n"
        f"  Platform : Kali Linux / Debian / Ubuntu / Termux\n"
        f"  GitHub   : {APP_GITHUB}{RESET}\n"
    )


# ── Spinner ──────────────────────────────────────────────────────────────────

class Spinner:
    """
    A lightweight terminal spinner that runs in a daemon thread.

    Usage:
        with Spinner("Opening browser"):
            time.sleep(1)
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Working", color: str = CYAN) -> None:
        self._message  = message
        self._color    = color
        self._stop_evt = threading.Event()
        self._thread   = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._stop_evt.is_set():
                break
            sys.stdout.write(
                f"\r  {self._color}{frame}{RESET}  {self._message}…  "
            )
            sys.stdout.flush()
            time.sleep(0.08)
        # Clear spinner line
        sys.stdout.write(f"\r{' ' * (len(self._message) + 12)}\r")
        sys.stdout.flush()

    def __enter__(self) -> "Spinner":
        self._thread.start()
        return self

    def __exit__(self, *_) -> None:
        self._stop_evt.set()
        self._thread.join()


# ── Prompt helpers ───────────────────────────────────────────────────────────

def prompt(text: str, default: str = "") -> str:
    """
    Wrapper around input() that catches EOFError (common in piped/Termux
    contexts) and returns the default gracefully.
    """
    try:
        return input(text).strip()
    except EOFError:
        return default


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — PHONE VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


def validate_phone(phone: str) -> bool:
    """Return True if phone matches E.164-ish pattern (7-15 digits, opt. + prefix)."""
    return bool(PHONE_RE.match(phone))


def get_phone() -> str:
    """
    Interactively prompt for a phone number until a valid one is entered.
    Handles KeyboardInterrupt (Ctrl-C) so the user can exit cleanly.
    """
    separator()
    print(f"\n{CYAN}{BOLD}  [ PHONE NUMBER INPUT ]{RESET}\n")

    while True:
        try:
            phone = prompt(
                f"  {GREEN}[+] Enter Phone Number (e.g. +212600000000): {RESET}"
            )
        except KeyboardInterrupt:
            _quit()

        if not phone:
            print(f"  {RED}[!] No input received. Please try again.{RESET}\n")
            continue

        if validate_phone(phone):
            print(f"\n  {GREEN}[✓] Valid number detected: {WHITE}{BOLD}{phone}{RESET}\n")
            log(f"INPUT  | phone={phone}")
            time.sleep(0.4)
            return phone

        print(
            f"  {RED}[!] Invalid format. Use 7-15 digits, "
            f"optional '+' prefix. (e.g. +212600000000){RESET}\n"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — BROWSER LAUNCH (with Termux fallback)
# ══════════════════════════════════════════════════════════════════════════════

def _is_termux() -> bool:
    """Heuristic: check for Termux-specific environment variable or prefix."""
    return (
        "com.termux" in os.environ.get("PREFIX", "")
        or Path("/data/data/com.termux").exists()
    )


def open_url(url: str) -> bool:
    """
    Open a URL in the default browser.

    Priority:
      1. termux-open-url  — if running inside Termux
      2. xdg-open         — standard Linux desktop
      3. webbrowser       — Python stdlib fallback

    Returns True if a method succeeded without raising, False otherwise.
    """
    if _is_termux():
        try:
            subprocess.run(
                ["termux-open-url", url],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass  # fall through to xdg-open

    try:
        subprocess.run(
            ["xdg-open", url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Last resort — Python's webbrowser module
    try:
        webbrowser.open_new_tab(url)
        return True
    except webbrowser.Error:
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — SEARCH ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _build_url(query: str, engine_base: str) -> str:
    return f"{engine_base}{quote(query)}"


def open_single_search(phone: str, name: str, query: str, engine_base: str) -> None:
    """Open one platform search, record it, and show feedback."""
    url = _build_url(query, engine_base)
    print(f"\n  {CYAN}[>] Platform : {WHITE}{name}{RESET}")
    print(f"  {CYAN}[>] URL      : {DIM}{url}{RESET}")

    with Spinner(f"Opening {name}"):
        success = open_url(url)
        time.sleep(0.3)

    if success:
        print(f"  {GREEN}[✓] Opened successfully!{RESET}")
        record_search(phone, name, url)
    else:
        print(f"  {RED}[✗] Could not open browser. URL copied above.{RESET}")


def open_all_searches(phone: str, items: list, cfg: dict) -> None:
    """Open every platform in sequence with a configurable delay between tabs."""
    delay = cfg.get("tab_delay", 0.40)
    print(f"\n  {YELLOW}[*] Opening all {len(items)} searches…{RESET}\n")

    for idx, (name, query, engine_base) in enumerate(items, start=1):
        url = _build_url(query, engine_base)
        print(f"  {GREEN}[{idx:02d}]{RESET} {WHITE}{name:<14}{RESET} {DIM}{url}{RESET}")
        open_url(url)
        record_search(phone, name, url)
        time.sleep(delay)

    print(f"\n  {GREEN}[✓] All {len(items)} searches opened.{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — MENUS
# ══════════════════════════════════════════════════════════════════════════════

def _quit(cfg: dict | None = None) -> None:
    """
    Unified quit routine — previously duplicated 3× in v2.0.
    Shows goodbye message and exits the process.
    """
    clear_screen()
    show_banner()
    separator()
    print(f"\n  {MAGENTA}{BOLD}Thank you for using {APP_NAME}.{RESET}")
    print(f"  {DIM}Stay ethical. Stay educational.{RESET}\n")

    if _session_history and cfg:
        path = export_session(cfg)
        if path:
            print(f"  {GREEN}[✓] Session auto-exported → {WHITE}{path}{RESET}\n")

    separator()
    sys.exit(0)


def show_main_menu(phone: str, items: list) -> None:
    """Render the platform selection menu."""
    clear_screen()
    show_banner()
    separator()
    print(f"\n{CYAN}{BOLD}  [ TARGET : {WHITE}{phone}{CYAN} ]{RESET}\n")
    separator(char="─")
    print(f"\n{CYAN}{BOLD}  AVAILABLE PLATFORMS :{RESET}\n")

    for i, (name, _query, _base) in enumerate(items, start=1):
        num = f"{YELLOW}[{i:02d}]{RESET}"
        print(f"    {num}  {WHITE}{name}{RESET}")

    print()
    separator(char="─")
    print(f"\n    {GREEN}[A]{RESET}  Open All Searches")
    print(f"    {CYAN}[H]{RESET}  View Session History")
    print(f"    {MAGENTA}[E]{RESET}  Export Session to File")
    print(f"    {BLUE}[N]{RESET}  Search New Number")
    print(f"    {RED}[Q]{RESET}  Quit\n")
    separator()


def show_history() -> None:
    """Display all searches performed in the current session."""
    separator(char="─")
    print(f"\n{CYAN}{BOLD}  [ SESSION HISTORY — {len(_session_history)} search(es) ]{RESET}\n")

    if not _session_history:
        print(f"  {DIM}No searches yet.{RESET}\n")
    else:
        for idx, entry in enumerate(_session_history, start=1):
            print(
                f"  {YELLOW}[{idx:03d}]{RESET}  "
                f"{DIM}{entry['ts']}{RESET}  "
                f"{WHITE}{entry['phone']}{RESET}  "
                f"{CYAN}{entry['platform']}{RESET}"
            )
        print()

    separator(char="─")


def result_actions_menu(phone: str) -> str:
    """
    Post-search sub-menu.
    Returns one of: "B" (back), "N" (new number), "Q" (quit).
    """
    separator(char="─")
    print(f"\n  {BOLD}What would you like to do next?{RESET}\n")
    print(f"    {CYAN}[B]{RESET}  Back to Search Menu  ({WHITE}{phone}{RESET})")
    print(f"    {BLUE}[N]{RESET}  Search a New Number")
    print(f"    {RED}[Q]{RESET}  Quit\n")
    separator(char="─")
    return prompt(f"\n  {BLUE}[?] Select: {RESET}", default="B").upper()


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 9 — MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    # ── Load config & init logging ──────────────────────────────────────────
    cfg = load_config()
    init_logging(cfg)
    log(f"Config loaded from {CONFIG_FILE}")

    show_banner()

    while True:
        # ── Get a phone number ──────────────────────────────────────────────
        phone = get_phone()
        items = build_platform_items(phone, cfg)  # list[(name, query, engine)]

        # ── Platform selection loop ─────────────────────────────────────────
        while True:
            show_main_menu(phone, items)

            try:
                choice = prompt(f"  {BLUE}[?] Select Option: {RESET}").upper()
            except KeyboardInterrupt:
                _quit(cfg)

            # ── Quit ────────────────────────────────────────────────────────
            if choice == "Q":
                _quit(cfg)

            # ── New number ──────────────────────────────────────────────────
            elif choice == "N":
                break   # exit inner loop → re-enter outer loop for new phone

            # ── Session history ─────────────────────────────────────────────
            elif choice == "H":
                show_history()
                prompt(f"\n  {DIM}Press Enter to return to menu…{RESET}")

            # ── Export session ──────────────────────────────────────────────
            elif choice == "E":
                path = export_session(cfg)
                if path:
                    print(f"\n  {GREEN}[✓] Exported → {WHITE}{path}{RESET}\n")
                else:
                    print(f"\n  {YELLOW}[!] Nothing to export yet.{RESET}\n")
                prompt(f"  {DIM}Press Enter to continue…{RESET}")

            # ── Open all searches ───────────────────────────────────────────
            elif choice == "A":
                open_all_searches(phone, items, cfg)
                action = result_actions_menu(phone)
                if action == "Q":
                    _quit(cfg)
                elif action == "N":
                    break   # new phone

            # ── Numbered platform choice ────────────────────────────────────
            else:
                try:
                    index = int(choice) - 1
                    if 0 <= index < len(items):
                        name, query, engine_base = items[index]
                        open_single_search(phone, name, query, engine_base)
                        action = result_actions_menu(phone)
                        if action == "Q":
                            _quit(cfg)
                        elif action == "N":
                            break   # new phone
                        # "B" → stay in inner loop (back to platform menu)
                    else:
                        print(
                            f"\n  {RED}[!] Option out of range. "
                            f"Choose 1–{len(items)}.{RESET}"
                        )
                        time.sleep(1)
                except ValueError:
                    print(
                        f"\n  {RED}[!] Invalid input. "
                        f"Enter a number, A, H, E, N, or Q.{RESET}"
                    )
                    time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {RED}[!] Interrupted by user. Exiting…{RESET}\n")
        sys.exit(130)   # conventional exit code for SIGINT