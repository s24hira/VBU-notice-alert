"""
Resilient HTTP client with multi-strategy anti-bot bypass.

The Samarth eGov portal blocks requests from datacenter IPs and
simple HTTP clients.  This module layers several bypass strategies
that mimic real browser traffic:

    Strategy 1 – cloudscraper   (defeats Cloudflare / JS challenges)
    Strategy 2 – curl_cffi      (impersonates Chrome's TLS fingerprint)
    Strategy 3 – requests.Session with full browser headers + cookie warmup

Each strategy is tried in order; on a 403 the next one is attempted.
All strategies share the same randomized User-Agent pool and send a
complete set of headers that match a real Chrome browser.
"""

import logging
import random
import time
import threading

import certifi
import requests

logger = logging.getLogger(__name__)

_cloudscraper_lock = threading.Lock()
_cloudscraper_instance = None

_warmup_session_lock = threading.Lock()
_warmup_session = None

def reset_sessions():
    """Reset the underlying HTTP sessions to clear connection pools and memory."""
    global _cloudscraper_instance, _warmup_session
    with _cloudscraper_lock:
        if _cloudscraper_instance is not None:
            try:
                _cloudscraper_instance.close()
            except Exception:
                pass
            _cloudscraper_instance = None

    with _warmup_session_lock:
        if _warmup_session is not None:
            try:
                _warmup_session.close()
            except Exception:
                pass
            _warmup_session = None

# ---------------------------------------------------------------------------
# User-Agent pool – rotated on every request
# ---------------------------------------------------------------------------
_USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]


def _pick_ua():
    return random.choice(_USER_AGENTS)


def _chrome_headers(url: str, ua: str | None = None) -> dict:
    """Return a full set of Chrome-grade request headers."""
    ua = ua or _pick_ua()
    from urllib.parse import urlparse
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8,"
                  "application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Cache-Control": "max-age=0",
        "Referer": origin + "/",
    }


# ---------------------------------------------------------------------------
# Strategy 1 – cloudscraper
# ---------------------------------------------------------------------------
def _try_cloudscraper(url: str, timeout: int = 30) -> requests.Response | None:
    """Use cloudscraper to bypass Cloudflare / JS-challenge protections."""
    global _cloudscraper_instance
    try:
        import cloudscraper
    except ImportError:
        logger.debug("cloudscraper not installed – skipping strategy")
        return None

    try:
        with _cloudscraper_lock:
            if _cloudscraper_instance is None:
                _cloudscraper_instance = cloudscraper.create_scraper(
                    browser={
                        "browser": "chrome",
                        "platform": "windows",
                        "desktop": True,
                    },
                )
            scraper = _cloudscraper_instance
            
        headers = _chrome_headers(url)

        resp = scraper.get(url, headers=headers, timeout=timeout, verify=certifi.where())
        if resp.status_code == 403:
            logger.warning("cloudscraper got 403 – will try next strategy")
            resp.close()
            return None
        resp.raise_for_status()
        return resp
    except Exception:
        logger.exception("cloudscraper strategy failed")
        return None


# ---------------------------------------------------------------------------
# Strategy 2 – curl_cffi  (impersonates Chrome's TLS JA3 fingerprint)
# ---------------------------------------------------------------------------
def _try_curl_cffi(url: str, timeout: int = 30) -> requests.Response | None:
    """Use curl_cffi to send requests with Chrome's real TLS fingerprint."""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.debug("curl_cffi not installed – skipping strategy")
        return None

    try:
        headers = _chrome_headers(url)
        resp = cffi_requests.get(
            url,
            headers=headers,
            timeout=timeout,
            verify=certifi.where(),
            impersonate="chrome131",       # JA3 fingerprint of Chrome 131
        )
        if resp.status_code == 403:
            logger.warning("curl_cffi got 403 – will try next strategy")
            try:
                resp.close()
            except AttributeError:
                pass
            return None
        resp.raise_for_status()
        return resp
    except Exception:
        logger.exception("curl_cffi strategy failed")
        return None


# ---------------------------------------------------------------------------
# Strategy 3 – plain requests.Session with cookie warm-up
# ---------------------------------------------------------------------------
def _try_session_warmup(url: str, timeout: int = 30) -> requests.Response | None:
    """
    Use a plain requests.Session but warm it up first:
      1. Hit the root domain to collect cookies / pass server-side checks.
      2. Then request the target URL with the populated cookie jar.
    """
    global _warmup_session
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        root_url = f"{parsed.scheme}://{parsed.netloc}/"

        with _warmup_session_lock:
            if _warmup_session is None:
                _warmup_session = requests.Session()
                needs_warmup = True
            else:
                needs_warmup = False
            session = _warmup_session

        ua = _pick_ua()
        headers = _chrome_headers(url, ua=ua)
        session.headers.update(headers)

        if needs_warmup:
            # Step 1 – warm-up: hit root to acquire session cookies / CSRF
            try:
                warmup = session.get(root_url, timeout=15, verify=certifi.where(),
                                     allow_redirects=True)
                logger.debug(f"Warmup status: {warmup.status_code}, "
                             f"cookies: {len(session.cookies)}")
                warmup.close()
            except Exception:
                logger.debug("Warmup request failed – continuing anyway")

            # Small human-like pause
            time.sleep(random.uniform(0.5, 1.5))

        # Step 2 – now request the actual page with cookies
        # Update referer to look like an internal navigation
        session.headers["Referer"] = root_url
        session.headers["Sec-Fetch-Site"] = "same-origin"

        resp = session.get(url, timeout=timeout, verify=certifi.where(),
                           allow_redirects=True)
        if resp.status_code == 403:
            logger.warning("session-warmup strategy got 403")
            resp.close()
            return None
        resp.raise_for_status()
        return resp
    except Exception:
        logger.exception("session-warmup strategy failed")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def resilient_get(url: str, *, timeout: int = 30,
                  max_retries: int = 3) -> requests.Response:
    """
    Fetch *url* using layered bypass strategies with retries.

    Returns a `requests.Response` on success.
    Raises `requests.HTTPError` if all strategies and retries are exhausted.
    """
    strategies = [
        ("cloudscraper", _try_cloudscraper),
        ("curl_cffi", _try_curl_cffi),
        ("session_warmup", _try_session_warmup),
    ]

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        for name, fn in strategies:
            try:
                resp = fn(url, timeout=timeout)
                if resp is not None:
                    logger.info(f"Successfully fetched {url} via {name} "
                                f"(attempt {attempt})")
                    return resp
            except Exception as exc:
                last_error = exc
                logger.warning(f"Strategy {name} failed on attempt {attempt}: "
                               f"{type(exc).__name__}")

        # Back off before next retry round (jittered exponential)
        if attempt < max_retries:
            backoff = min(2 ** attempt + random.uniform(0, 2), 15)
            logger.info(f"All strategies failed on attempt {attempt}; "
                        f"retrying in {backoff:.1f}s")
            time.sleep(backoff)

    # All retries exhausted
    msg = f"All bypass strategies exhausted for {url} after {max_retries} attempts"
    logger.error(msg)
    if last_error:
        raise requests.HTTPError(msg) from last_error
    raise requests.HTTPError(msg)


def resilient_download_file(url: str, *, timeout: int = 30,
                      max_retries: int = 3, max_size: int = 10 * 1024 * 1024) -> requests.Response:
    """
    Fetch a file (PDF or Image) URL with bypass strategies and memory limit enforcement.
    """
    headers = _chrome_headers(url)
    headers["Accept"] = "application/pdf,image/webp,image/apng,image/*,*/*;q=0.8"

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            # 1. Primary Strategy for files: requests streaming (to strictly enforce memory limits)
            try:
                resp = requests.get(url, headers=headers, timeout=timeout,
                                    verify=certifi.where(), stream=True)
                if resp.status_code != 403:
                    resp.raise_for_status()
                    
                    content = bytearray()
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            content.extend(chunk)
                            if len(content) > max_size:
                                raise ValueError(f"File exceeds maximum allowed size of {max_size} bytes")
                    resp._content = bytes(content)
                    
                    logger.info(f"File fetched securely via requests (attempt {attempt})")
                    return resp
            except ValueError as ve:
                # If it exceeds max_size, don't retry, abort immediately
                logger.error(str(ve))
                raise requests.HTTPError(str(ve))
            except Exception as e:
                logger.debug(f"Requests stream failed: {e}")

            # 2. Fallback Strategy: curl_cffi (if requests gets 403)
            try:
                from curl_cffi import requests as cffi_requests
                resp = cffi_requests.get(
                    url, headers=headers, timeout=timeout,
                    verify=certifi.where(), impersonate="chrome131",
                )
                resp.raise_for_status()
                
                # Check size post-download for curl_cffi since older versions don't stream well
                if len(resp.content) > max_size:
                    raise ValueError(f"File exceeds maximum allowed size of {max_size} bytes")
                    
                logger.info(f"File fetched via curl_cffi fallback (attempt {attempt})")
                return resp
            except ImportError:
                pass
            except ValueError as ve:
                logger.error(str(ve))
                raise requests.HTTPError(str(ve))
            except Exception as e:
                logger.debug(f"curl_cffi file fetch failed: {e}")
                raise e # re-raise to trigger retry logic

        except Exception as exc:
            last_error = exc
            logger.warning(f"File download attempt {attempt} failed: "
                           f"{type(exc).__name__}")
            if attempt < max_retries:
                time.sleep(min(2 ** attempt + random.uniform(0, 1), 10))

    msg = f"File download failed for {url} after {max_retries} attempts"
    logger.error(msg)
    if last_error:
        raise requests.HTTPError(msg) from last_error
    raise requests.HTTPError(msg)
