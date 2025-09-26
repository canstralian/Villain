"""
concurrency_and_recon.py

Purpose:
- Replace busy-wait patterns with proper threading primitives (Event, Lock).
- Provide a thread-safe JSONL logger for session/implant logging.
- Implement hardened recon helpers adapted from GhostTrack pseudocode:
    - geolocate_ip(ip_address)
    - enumerate_username(username)
    - analyze_phone_number(phone_number_string)

Notes:
- This module is self-contained and uses safe defaults (timeouts, retries, rate-limiting).
- Integrate into Villain by replacing flag-based busy-waits with Event signaling and
  using the JsonlLogger.store_session_details(...) method from worker threads.
"""

from __future__ import annotations

import json
import logging
import queue
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import phonenumbers  # pip install phonenumbers
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------------
# Configuration dataclass
# -----------------------------
@dataclass
class Config:
    ip_geolocation_api: str = "https://ipwhois.app/json/"  # example, replace in production
    default_timeout: float = 6.0
    max_workers: int = 8
    user_agents: Iterable[str] = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/14.1.2 Safari/605.1.15",
    )
    username_sites: Iterable[Dict[str, str]] = (
        # Example site list. In production, load from JSON config file.
        {"name": "GitHub", "url": "https://github.com/{}"},
        {"name": "Twitter", "url": "https://twitter.com/{}"},
        {"name": "Reddit", "url": "https://www.reddit.com/user/{}"},
    )
    token_bucket_rate_min: float = 0.5  # min delay
    token_bucket_rate_max: float = 1.5  # max delay

CONFIG = Config()

# -----------------------------
# Logging setup (module-level)
# -----------------------------
logger = logging.getLogger("villain.recon")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
logger.addHandler(handler)


# -----------------------------
# Thread-safe JSONL logger
# -----------------------------
class JsonlLogger:
    """
    Thread-safe JSONL logger. Writes one JSON object per line.
    Use .store_session_details(...) from any thread.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        # optional internal queue + writer thread for higher throughput
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._stop_event = threading.Event()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        """Background writer that flushes queued log entries to disk."""
        logger.debug("JsonlLogger writer thread started.")
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                entry = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                logger.debug("Wrote log entry to JSONL.")
            except Exception as e:
                logger.exception("Failed to write log entry: %s", e)
            finally:
                self._queue.task_done()
        logger.debug("JsonlLogger writer thread exiting.")

    def store_session_details(self, id: str, session_meta: Dict[str, Any]) -> None:
        """
        Non-blocking enqueue of a session metadata dict for persistence.
        """
        try:
            entry = {"id": id, "meta": session_meta, "ts": time.time()}
            self._queue.put_nowait(entry)
        except queue.Full:
            # In worst case, fall back to synchronous write with lock
            logger.warning("Queue full; writing log entry synchronously.")
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": id, "meta": session_meta, "ts": time.time()}) + "\n")

    def close(self, timeout: float = 5.0) -> None:
        """Stop writer thread and flush pending entries."""
        self._stop_event.set()
        self._writer_thread.join(timeout=timeout)
        # final flush (best-effort)
        while not self._queue.empty():
            try:
                entry = self._queue.get_nowait()
                with self._lock:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._queue.task_done()
            except queue.Empty:
                break


# -----------------------------
# Event-based synchronization helpers
# -----------------------------
class ImplantLoggerController:
    """
    Replaces boolean flags / busy-waits with an Event and explicit API.
    - set_open() -> mark file as open (writer inside process)
    - set_closed() -> mark file as closed
    - wait_closed(timeout=None) -> wait until closed
    """

    def __init__(self):
        self._open_event = threading.Event()
        # Initially file is closed (no writer).
        self._open_event.clear()

    def set_open(self) -> None:
        self._open_event.set()

    def set_closed(self) -> None:
        self._open_event.clear()

    def is_open(self) -> bool:
        return self._open_event.is_set()

    def wait_until_closed(self, timeout: Optional[float] = None) -> bool:
        """
        Wait until the open event is cleared (i.e., file closed).
        If timeout provided, returns False on timeout.
        """
        # If currently closed, return immediately.
        if not self._open_event.is_set():
            return True
        # Busy-wait avoidance: poll with small sleep and respect timeout.
        start = time.time()
        while self._open_event.is_set():
            time.sleep(0.05)
            if timeout is not None and (time.time() - start) > timeout:
                return False
        return True


# -----------------------------
# HTTP Session factory with retries
# -----------------------------
def make_requests_session(timeout: float = CONFIG.default_timeout) -> requests.Session:
    s = requests.Session()
    retries = Retry(total=2, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.request_timeout = timeout  # informal attribute; use per-call timeout
    return s


# -----------------------------
# Utility: IP validation
# -----------------------------
_IPV4_RE = re.compile(
    r"^(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)){3}$"
)
# simple IPv6 heuristic (not exhaustive)
_IPV6_RE = re.compile(r"^[0-9a-fA-F:]{2,39}$")


def is_valid_ip(ip: str) -> bool:
    return bool(_IPV4_RE.match(ip)) or bool(_IPV6_RE.match(ip))


# -----------------------------
# Geolocation function
# -----------------------------
def geolocate_ip(ip_address: str, config: Config = CONFIG) -> Optional[Dict[str, Any]]:
    """
    Hardened geolocation using external API with validation, timeout, and retries.
    Returns parsed JSON or None on failure.
    """
    if not is_valid_ip(ip_address):
        logger.error("Invalid IP address format: %s", ip_address)
        return None

    session = make_requests_session(config.default_timeout)
    url = config.ip_geolocation_api.rstrip("/") + "/" + ip_address if not config.ip_geolocation_api.endswith("/") else config.ip_geolocation_api + ip_address

    headers = {"User-Agent": "Villain-Recon-Module/1.0"}

    try:
        resp = session.get(url, timeout=config.default_timeout, headers=headers)
        if resp.status_code != 200:
            logger.warning("Geolocation API returned status %s for %s", resp.status_code, ip_address)
            return None
        data = resp.json()
        # Basic sanity checks
        if isinstance(data, dict) and ("ip" in data or "success" in data):
            # Map fields to normalized keys for Villain's use
            normalized = {
                "ip": data.get("ip", ip_address),
                "country": data.get("country"),
                "region": data.get("region") or data.get("region_name"),
                "city": data.get("city"),
                "latitude": data.get("latitude") or data.get("lat"),
                "longitude": data.get("longitude") or data.get("lon"),
                "isp": (data.get("connection", {}) or {}).get("isp") or data.get("isp"),
                "org": (data.get("connection", {}) or {}).get("org") or data.get("org"),
                "raw": data,
            }
            logger.debug("Geolocation result: %s", normalized)
            return normalized
        else:
            logger.warning("Unexpected geolocation response for %s: %s", ip_address, type(data))
            return None
    except requests.RequestException as e:
        logger.warning("Network error while geolocating %s: %s", ip_address, e)
    except ValueError as e:
        logger.warning("Failed to parse geolocation response for %s: %s", ip_address, e)
    return None


# -----------------------------
# Username enumeration (concurrent)
# -----------------------------
def _safe_site_check(session: requests.Session, site: Dict[str, str], username: str, timeout: float) -> Dict[str, str]:
    """
    Single site check helper. Returns status string or URL if found.
    """
    url = site["url"].format(username)
    headers = {"User-Agent": random.choice(list(CONFIG.user_agents))}
    try:
        r = session.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        if r.status_code == 200:
            return {"site": site["name"], "status": "found", "url": url, "http_code": r.status_code}
        elif r.status_code in (401, 403):
            # Might exist but protected; mark as found/locked
            return {"site": site["name"], "status": f"protected ({r.status_code})", "url": url, "http_code": r.status_code}
        else:
            return {"site": site["name"], "status": f"not_found ({r.status_code})", "http_code": r.status_code}
    except requests.RequestException as e:
        return {"site": site["name"], "status": f"error ({e})"}


def enumerate_username(username: str, config: Config = CONFIG) -> Dict[str, Any]:
    """
    Hardened username enumeration using ThreadPoolExecutor with rate-limiting.
    Returns a dict of results keyed by site name.
    """
    # Input validation
    if not username or len(username) > 30 or re.search(r"[^\w\-.]", username):
        logger.error("Invalid username format: %s", username)
        return {}

    sites = list(config.username_sites)
    session = make_requests_session(config.default_timeout)

    results: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=min(config.max_workers, len(sites) or 1)) as ex:
        futures = []
        for site in sites:
            # Rate-limiting pause per iteration (token-bucket-like, randomized)
            time.sleep(random.uniform(config.token_bucket_rate_min, config.token_bucket_rate_max))
            futures.append(ex.submit(_safe_site_check, session, site, username, config.default_timeout))

        for fut in as_completed(futures):
            try:
                res = fut.result()
                results[res["site"]] = res
            except Exception as e:
                logger.exception("Unhandled error during username check: %s", e)
    logger.debug("Username enumeration results: %s", results)
    return results


# -----------------------------
# Phone number analysis
# -----------------------------
def analyze_phone_number(phone_number_string: str) -> Optional[Dict[str, Any]]:
    """
    Uses phonenumbers library to parse and extract metadata safely.
    Returns dictionary with location, carrier, timezones, validity etc.
    """
    if not phone_number_string or len(phone_number_string) > 32:
        logger.error("Invalid phone number input.")
        return None

    # Basic sanity pattern: digits, +, spaces, parentheses, hyphen
    if not re.match(r"^[0-9+\-\s()extx.]{3,32}$", phone_number_string):
        logger.error("Phone number failed basic sanitation.")
        return None

    try:
        parsed = phonenumbers.parse(phone_number_string, None)  # None => try to infer country from number
    except phonenumbers.NumberParseException as e:
        logger.warning("phonenumbers.parse error: %s", e)
        return None

    is_valid = phonenumbers.is_valid_number(parsed)
    from phonenumbers import carrier, geocoder, timezone

    try:
        loc = geocoder.description_for_number(parsed, "en")
    except Exception:
        loc = None

    try:
        carrier_name = carrier.name_for_number(parsed, "en")
    except Exception:
        carrier_name = None

    try:
        timezones = timezone.time_zones_for_number(parsed)
    except Exception:
        timezones = []

    result = {
        "raw": phone_number_string,
        "international_format": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
        "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        "valid": is_valid,
        "location": loc,
        "carrier": carrier_name,
        "timezones": list(timezones),
    }
    logger.debug("Phone analysis: %s", result)
    return result


# -----------------------------
# Example: Replacing busy-wait usage in Villain
# -----------------------------
# Suppose original code used:
#   while HoaxShell_Implants_Logger.generated_implants_file_open:
#       pass
#
# Replacement pattern:
#
#   implant_logger_ctrl = ImplantLoggerController()
#   # When opening the file for writing:
#   implant_logger_ctrl.set_open()
#   try:
#       jsonl_logger.store_session_details(...)
#   finally:
#       implant_logger_ctrl.set_closed()
#
#   # Any other thread that needs to wait until the file is closed:
#   implant_logger_ctrl.wait_until_closed(timeout=5.0)
#
# This avoids busy-waits and uses small sleeps and Event semantics instead.
#
# The following small helper demonstrates usage.

def example_writer_task(jsonl_logger: JsonlLogger, implant_ctrl: ImplantLoggerController, ident: str, payload: dict) -> None:
    """
    Simulate a thread that writes session details and uses Event signalling.
    """
    logger.info("Writer thread %s requesting to write.", ident)
    implant_ctrl.set_open()
    try:
        # Simulate some write work
        jsonl_logger.store_session_details(ident, payload)
        time.sleep(0.1)  # emulate work
    finally:
        implant_ctrl.set_closed()
        logger.info("Writer thread %s finished writing.", ident)


def example_reader_task(implant_ctrl: ImplantLoggerController, timeout: float = 10.0) -> None:
    """
    Simulate a task that must wait until log file is closed to continue.
    """
    logger.info("Reader waiting for logger to close.")
    ok = implant_ctrl.wait_until_closed(timeout=timeout)
    if not ok:
        logger.warning("Timed out waiting for logger to close.")
    else:
        logger.info("Logger closed; reader can proceed.")


# -----------------------------
# Module-level quick demo (only runs when invoked directly)
# -----------------------------
if __name__ == "__main__":
    jl = JsonlLogger("implants.jsonl")
    ctrl = ImplantLoggerController()

    # Spawn a writer and reader to illustrate non-busy synchronization
    w = threading.Thread(target=example_writer_task, args=(jl, ctrl, "session-1", {"user": "alice"}))
    r = threading.Thread(target=example_reader_task, args=(ctrl, 3.0))

    w.start()
    r.start()

    w.join()
    r.join()

    # demo geolocation (replace with real IP)
    print("Geolocation demo:", geolocate_ip("8.8.8.8"))

    # demo username enumeration
    print("Username demo:", enumerate_username("example_username"))

    # demo phone analysis
    print("Phone demo:", analyze_phone_number("+14155552671"))

    jl.close()