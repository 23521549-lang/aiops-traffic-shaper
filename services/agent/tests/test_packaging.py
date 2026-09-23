"""The package the landing page tells a stranger to install.

`pip install traffic-shaper-agent` and `traffic-shaper run` sat on the
quickstart for months with nothing behind either. There was no package and no
console script: the CLI could only be reached as
`python -m services.agent.cli` from a checkout of the backend repository, so
a customer following the page got "command not found" on the very first line.

The failure this file is really guarding against is subtler than that one and
survives a build. A wheel that declares `services.agent` but forgets
`services.agent.enforcer` installs perfectly, prints its help perfectly, and
raises ImportError the first time someone runs `run` - on their machine,
during the install they are timing, with no way to tell whether they did
something wrong.
"""
import importlib
import tomllib
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parents[1]
_PYPROJECT = _AGENT_DIR / "pyproject.toml"


@pytest.fixture(scope="module")
def meta():
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_the_command_the_page_tells_people_to_run_is_the_installed_one(meta):
    scripts = meta["project"]["scripts"]
    assert "traffic-shaper" in scripts

    module, _, attr = scripts["traffic-shaper"].partition(":")
    assert getattr(importlib.import_module(module), attr) is not None


def test_every_package_the_cli_needs_is_shipped(meta):
    """Declared by hand, because setuptools' own discovery under a
    package-dir remap finds `tests` and misses `enforcer`."""
    declared = set(meta["tool"]["setuptools"]["packages"])

    # Anything with an __init__.py under the agent, minus the tests, has to
    # be in the wheel.
    for init in _AGENT_DIR.rglob("__init__.py"):
        rel = init.parent.relative_to(_AGENT_DIR)
        if "tests" in rel.parts or "build" in rel.parts:
            continue
        name = ".".join(("services", "agent", *rel.parts)) if rel.parts else "services.agent"
        assert name in declared, f"{name} exists but is not in the wheel"

    assert "services.agent" in declared


def test_the_agent_stays_thin(meta):
    """ADR-002 puts every piece of ML in the backend, and this list is where
    that decision is actually enforced. boto3 or numpy appearing here is a
    design change wearing a packaging change's clothes, and it is what makes
    the agent too heavy to install on somebody's web server without an
    argument."""
    names = [d.split(">")[0].split("=")[0].split("<")[0].strip().lower()
             for d in meta["project"]["dependencies"]]

    assert names == ["click"]


def test_the_package_declares_the_pythons_it_is_tested_on(meta):
    assert meta["project"]["requires-python"]


def test_the_readme_the_package_ships_exists(meta):
    """PyPI renders it, so a missing file is a blank project page - and it is
    also what breaks the build, silently, only when someone tries to
    publish."""
    assert (_AGENT_DIR / meta["project"]["readme"]).is_file()
