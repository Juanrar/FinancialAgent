import asyncio
import time

from financial_agent.fiwind.browser import BrowserCommandError
from financial_agent.fiwind.client import Sent
from financial_agent.fiwind.pages import Recipient


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


class FakeWallet:
    """Doble de FiwindClient. Registra cada llamada en calls como (método, *argumentos)."""

    def __init__(self, available=True,
                 recipient=Recipient("Federico Pérez", "Brubank", "0000168300000000123459"),
                 lookup_error=None, outcome=Sent("En proceso"), balance=300,
                 balance_error=None, session_ok=True, session_error=None, login_ok=True,
                 delay=0.0):
        self.available = available
        self.recipient = recipient
        self.lookup_error = lookup_error
        self.outcome = outcome
        self.balance = balance
        self.balance_error = balance_error
        self.session_ok = session_ok
        self.session_error = session_error
        self.login_ok = login_ok
        self.delay = delay
        self.calls: list[tuple] = []

    async def lookup(self, destination):
        self.calls.append(("lookup", destination))
        if self.lookup_error:
            raise self.lookup_error
        return self.recipient

    async def withdraw(self, order):
        self.calls.append(("withdraw", order))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.outcome

    async def read_balance(self):
        self.calls.append(("read_balance",))
        if self.balance_error:
            raise self.balance_error
        return self.balance

    async def check_session(self):
        self.calls.append(("check_session",))
        if self.session_error:
            raise self.session_error
        return self.session_ok

    async def open_login_window(self):
        self.calls.append(("open_login_window",))

    async def finish_login(self):
        self.calls.append(("finish_login",))
        return self.login_ok
