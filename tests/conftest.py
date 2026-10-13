from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixtures  # noqa: E402


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> Path:
    """テストごとに作り直したダミーの Excel（tests/fixtures と同じ内容）。"""
    return make_fixtures.build_all(tmp_path_factory.mktemp("fixtures"))


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("APPROVALREPORT_HOME", str(h))
    return h


@pytest.fixture
def cfg(home):
    from core import config
    return config.load()


@pytest.fixture
def templates(home):
    from core import config
    return config.load_text_templates()


@pytest.fixture
def db(home):
    from core import workflow
    from core.db import Database
    d = Database()
    workflow.seed_glossary(d)
    yield d
    d.close()


@pytest.fixture
def loaded(db, cfg, fixtures_dir):
    """過去事例 3 件を取り込んだ状態。{事例名: id}"""
    from core import workflow
    return {f"case_{x}": workflow.import_case(db, fixtures_dir / f"case_{x}.xlsx", cfg) for x in "ABC"}


@pytest.fixture
def advisor(db, cfg, templates, loaded):
    from core.advisor import Advisor
    return Advisor(db, cfg, templates).build()
