"""Never write synthetic model diagnostics into the user's production runtime."""
import pytest

from services.understanding import model_runs


@pytest.fixture(autouse=True)
def isolate_model_run_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(model_runs, 'runs_root', lambda: tmp_path / 'model-runs')
