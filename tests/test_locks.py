import subprocess
import sys
import time

import pytest
from conftest import make_jpeg

from strip_metadata.core import StripError, strip_file
from strip_metadata.locks import who_locks

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Restart Manager")


@pytest.fixture
def held(tmp_path):
    """A dirty JPEG kept open (without delete sharing) by another process."""
    p = make_jpeg(tmp_path / "held.jpg")
    child = subprocess.Popen([sys.executable, "-c",
                              f"f = open({str(p)!r}, 'rb'); print('ok', flush=True); import time; time.sleep(60)"],
                             stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "ok"
    yield p
    child.kill()
    child.wait()


def test_who_locks_names_the_other_program(held):
    names = who_locks(held)
    assert names and all(n for n in names)


def test_who_locks_unlocked_file_is_empty(tmp_path):
    assert who_locks(make_jpeg(tmp_path / "free.jpg")) == []


def test_locked_error_names_the_program(held):
    with pytest.raises(StripError) as e:
        strip_file(held)
    assert "「" in str(e.value) and who_locks(held)[0] in str(e.value)
