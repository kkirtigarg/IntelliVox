"""
src/memory/browser_memory.py
Browser-specific memory: URL back-history (for "go back") and last search.
"""


class BrowserMemory:
    def __init__(self):
        self.history: list[str] = []
        self.last_search_query: str | None = None
        self.last_search_results: list[str] = []

    def push_url(self, url: str) -> None:
        if not url:
            return
        if not self.history or self.history[-1] != url:
            self.history.append(url)

    def pop_previous_url(self) -> str | None:
        """Drop the current URL off the stack and return the one before it, if any."""
        if len(self.history) < 2:
            return None
        self.history.pop()
        return self.history[-1]
