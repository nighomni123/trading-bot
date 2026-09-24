"""The ordinary ablation entry point must not open frozen OOS by accident."""
import pytest

from scripts.run_ablation import main


def test_oos_requires_explicit_locked_config():
    with pytest.raises(SystemExit):
        main(["--start", "2025-01-01", "--end", "2025-01-02"])
