import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd, env=None):
    e = {**os.environ, "PYTHONPATH": str(ROOT)}
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "pcflip.cli", *args], cwd=cwd, env=e, capture_output=True, text=True)


def test_cli_evaluate_and_report_and_deals(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('data_dir = "data"\n[alerts]\nchannels = ["console"]\n')
    base = ["--config", str(cfg)]
    r = run([*base, "evaluate", "-p", "900", "Gaming PC RTX 4070 Super 7800X3D 32GB DDR5 2TB NVMe"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "STRONG_BUY" in r.stdout and "max buy" in r.stdout

    r = run([*base, "deals", "add", "--cost", "900", "--title", "4070S build"], tmp_path)
    assert r.returncode == 0 and "deal #1" in r.stdout
    r = run([*base, "deals", "sold", "1", "--price", "1300", "--platform", "local"], tmp_path)
    assert "profit $400" in r.stdout
    r = run([*base, "deals", "list"], tmp_path)
    assert "realized profit: $400" in r.stdout

    r = run([*base, "prices", "--skip-fetch"], tmp_path)
    assert r.returncode == 0, r.stderr
    snap = json.loads((tmp_path / "data" / "prices.json").read_text())
    assert snap["skus"]["rtx-3090"]["est_sold"] == 1200 and snap["skus"]["rtx-3090"]["source"] == "seed"
    md = (tmp_path / "data" / "PRICES.md").read_text()
    assert "| `rtx-3090` |" in md
    html = (tmp_path / "data" / "report.html").read_text()
    assert "Gaming PC RTX 4070 Super" in html and "4070S build" in html

    r = run([*base, "prices", "--strict"], tmp_path)
    assert r.returncode == 2 and "EBAY_CLIENT_ID" in r.stdout

    r = run([*base, "skus"], tmp_path)
    assert r.returncode == 0 and "rtx-4090" in r.stdout

    r = run([*base, "test-alerts"], tmp_path)
    assert r.returncode == 0 and "console: ok" in r.stdout


def test_cli_evaluate_stdin(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('data_dir = "data"\n')
    e = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-m", "pcflip.cli", "--config", str(cfg), "evaluate", "--no-store", "-"],
                       input="Corsair Vengeance 32GB DDR5 6000\n$300\n2x16 cl36", cwd=tmp_path, env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "[WATCH]" in r.stdout and "32GB DDR5" in r.stdout
