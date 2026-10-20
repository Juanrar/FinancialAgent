import pytest

from financial_agent.store.db import connect, migrate


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()
