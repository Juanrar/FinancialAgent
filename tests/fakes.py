import asyncio
import time

from financial_agent.fiwind.browser import BrowserCommandError
from financial_agent.fiwind.client import LoggedIn, Sent
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
                 balance_error=None, login_outcome=LoggedIn(), login_error=None,
                 code_outcome=LoggedIn(), code_error=None, delay=0.0):
        self.available = available
        self.recipient = recipient
        self.lookup_error = lookup_error
        self.outcome = outcome
        self.balance = balance
        self.balance_error = balance_error
        self.login_outcome = login_outcome
        self.login_error = login_error
        self.code_outcome = code_outcome
        self.code_error = code_error
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

    async def start_login(self):
        self.calls.append(("start_login",))
        if self.login_error:
            raise self.login_error
        return self.login_outcome

    async def submit_code(self, code):
        self.calls.append(("submit_code", code))
        if self.code_error:
            raise self.code_error
        return self.code_outcome
