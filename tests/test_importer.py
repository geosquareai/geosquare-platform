import pytest
from fastapi.testclient import TestClient
import geosquare_engine as gs

from geosquare_platform.app import app
from geosquare_platform.importer import ImportRejected, analyse



@pytest.fixture(scope="module")
def gids():
    return [gs.encode(9, 4000, 5000), gs.encode(9, 4001, 5000)]


def csv_of(gids, col="gid"):
    return (f"name,{col},value\n" + "\n".join(f"a{i},{g},{i}" for i, g in enumerate(gids))).encode()


def test_finds_column_and_level(gids):
    r = analyse(csv_of(gids, "cell"), confirm_digit_gid=True)
    assert r.ok and r.key_column == "cell" and r.level == 9 and r.rows == 2


def test_no_key_column():
    r = analyse(b"name,value\nx,1\ny,2\n")
    assert not r.ok and r.status == "rejected"


def test_bad_row_rejects(gids):
    data = csv_of(gids) + b"\nzz,not-a-gid,9"
    assert not analyse(data, confirm_digit_gid=True).ok


def test_empty_file():
    with pytest.raises(ImportRejected):
        analyse(b"")


def test_api(gids):
    c = TestClient(app)
    r = c.post("/import/analyse", params={"confirm_digit_gid": True}, files={"file": ("a.csv", csv_of(gids))})
    assert r.status_code == 200 and r.json()["level"] == 9
    assert c.post("/import/analyse", files={"file": ("a.csv", b"")}).status_code == 422
