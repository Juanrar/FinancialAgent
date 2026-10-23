import time

from financial_agent.fiwind.browser import BrowserCommandError


class FakeBrowser:
    """Doble de AgentBrowser. Las claves de responses son prefijos de comando."""

    def __init__(self, responses: dict | None = None, delay: float = 0):
        self.responses = {k: (list(v) if isinstance(v, list) else v)
                          for k, v in (responses or {}).items()}
        self.calls: list[tuple] = []
        self.delay = delay

    def open(self, url: str, headed: bool | None = None) -> None:
        self.calls.append(("open", url, headed))
        if self.delay:
            time.sleep(self.delay)

    def run(self, *args: str) -> str:
        self.calls.append(tuple(args))
        if self.delay:
            time.sleep(self.delay)
        keys = [k for k in self.responses if args[:len(k)] == k]
        if not keys:
            return ""
        value = self.responses[max(keys, key=len)]
        if isinstance(value, Exception):
            raise BrowserCommandError(tuple(args), str(value))
        if isinstance(value, list):
            return value.pop(0) if value else ""
        return value

    def close(self) -> None:
        self.calls.append(("close",))
