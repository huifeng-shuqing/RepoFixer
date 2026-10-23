import pathlib
import shutil
import uuid

import pytest


@pytest.fixture()
def ws():
    """Workspace-local temp dir (system temp may be unwritable in sandboxed CI)."""
    d = pathlib.Path(__file__).resolve().parent / ".tmp" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)
