"""No test may reach the machine's own config folder (conftest's _no_real_config)."""

from heliofits.core import config
from heliofits.core.library import Library


def test_a_library_opened_in_a_test_writes_its_config_under_the_test(tmp_path):
    assert tmp_path in config.app_support_dir().parents
    with Library.open(tmp_path / "library"):
        pass
    assert config.config_path() == tmp_path / "appsupport" / "config.json"
    assert config.config_path().exists()
