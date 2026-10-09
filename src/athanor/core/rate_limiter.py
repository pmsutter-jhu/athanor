"""
ATHANOR Rate Limiter.

Manages API request pacing with a minimum interval between calls
and exponential backoff for rate-limited (429) responses.
"""
import time
import threading


class RateLimiter:
    """Simple rate limiter with minimum call interval and 429 backoff."""

    def __init__(self, min_interval: float = 0.5):
        self._lock = threading.Lock()
        self.last_call_time = 0.0
        self.min_interval = min_interval

    def wait_for_slot(self):
        """Ensure minimum interval between calls."""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_call_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_call_time = time.time()

    def handle_rate_limit_error(self, e: Exception, attempt: int = 1) -> bool:
        """Sleep with exponential backoff if error is a rate limit. Returns True if retryable."""
        msg = str(e).lower()
        is_rate_limit = (
            "429" in msg
            or "resourceexhausted" in msg
            or "quota" in msg
            or "too many requests" in msg
        )
        if is_rate_limit:
            wait_time = 15 * (2 ** (attempt - 1))
            print(f"   [RateLimiter] Quota exceeded. Sleeping for {wait_time}s (Attempt {attempt})...")
            time.sleep(wait_time)
            return True
        return False


# Default instance used by the gateway.
limiter = RateLimiter()
