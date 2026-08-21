from web_watcher import version
from web_watcher.main import main


def test_version():
    assert version == "1.0.2"


def test_main():
    assert main() == 0
