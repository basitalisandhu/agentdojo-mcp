"""Suite loading and the AgentDojo guard: the error paths of agentdojo_mcp.dojo."""

from __future__ import annotations

import builtins

import pytest

from agentdojo_mcp import dojo

from .conftest import DOJO_SUITE, FIXTURES


def test_require_names_the_extra_when_agentdojo_is_missing(monkeypatch):
    real_import = builtins.__import__

    def no_agentdojo(name, *args, **kwargs):
        if name == "agentdojo" or name.startswith("agentdojo."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_agentdojo)
    with pytest.raises(dojo.DojoMissing, match=r'pip install "agentdojo-mcp\[dojo\]"'):
        dojo.require()


def test_agentdojo_version_falls_back_to_unknown(monkeypatch):
    import importlib.metadata as md

    def missing(_name):
        raise md.PackageNotFoundError("agentdojo")

    monkeypatch.setattr(md, "version", missing)
    assert dojo.agentdojo_version() == "unknown"


@pytest.mark.dojo
def test_file_suite_loads():
    suite = dojo.load_suite(DOJO_SUITE)
    assert "user_task_0" in suite.user_tasks


@pytest.mark.dojo
def test_missing_suite_file(tmp_path):
    with pytest.raises(dojo.SuiteError, match="does not exist"):
        dojo.load_suite(f"{tmp_path / 'nope.py'}:suite")


@pytest.mark.dojo
def test_attribute_that_is_not_a_task_suite():
    path = FIXTURES / "fixture-dojo-suite" / "fixture-suite.py"
    with pytest.raises(dojo.SuiteError, match="is not an AgentDojo TaskSuite"):
        dojo.load_suite(f"{path}:no_such_attribute")


@pytest.mark.dojo
def test_module_that_cannot_be_imported():
    with pytest.raises(dojo.SuiteError, match="cannot import agentdojo_mcp_no_such_module"):
        dojo.load_suite("agentdojo_mcp_no_such_module:suite")


@pytest.mark.dojo
def test_module_attribute_suite():
    suite = dojo.load_suite("agentdojo.default_suites.v1.banking.task_suite:task_suite")
    assert suite.name == "banking"


@pytest.mark.dojo
def test_unknown_builtin_suite_lists_the_known_ones():
    with pytest.raises(dojo.SuiteError, match=r"not a v1\.2\.2 suite \(have: banking, slack"):
        dojo.load_suite("no_such_suite")


@pytest.mark.dojo
def test_unknown_benchmark_version():
    with pytest.raises(dojo.SuiteError, match="no benchmark version 'v0.0.0'"):
        dojo.load_suite("banking", "v0.0.0")
