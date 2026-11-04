"""Tests for require_deps.py -- the bake's Python dependency preflight.

No test touches the network or this interpreter's real packages: _run,
module_available, pip_available, ensure_pip and install_package are replaced
with fakes.
"""

import sys

import require_deps as rd


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Runner:
    """Records commands and returns scripted results."""

    def __init__(self, results=()):
        self.calls = []
        self.results = list(results)

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if not self.results:
            return Result()
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def pillow():
    return next(dep for dep in rd.REQUIRED if dep.import_name == "PIL")


def no_install(*args, **kwargs):
    raise AssertionError("this path must not install anything")


def test_pillow_is_the_required_package():
    deps = {dep.import_name: dep for dep in rd.REQUIRED}
    assert deps["PIL"].package == "Pillow"
    assert deps["PIL"].why


def test_install_commands_use_user_then_plain():
    commands = rd.install_commands("Pillow")
    assert commands[0][-4:] == [
        "install", "--user", "--disable-pip-version-check", "Pillow"]
    assert commands[1][-3:] == [
        "install", "--disable-pip-version-check", "Pillow"]
    assert "--user" not in commands[1]


def test_module_available_imports_with_this_interpreter(monkeypatch):
    runner = Runner([Result(0)])
    monkeypatch.setattr(rd, "_run", runner)

    assert rd.module_available("PIL") == (True, "")
    assert runner.calls[0][:3] == [sys.executable, "-c", "import PIL"]


def test_module_available_returns_the_last_error_line(monkeypatch):
    runner = Runner([Result(
        1, stderr="Traceback (most recent call last):\n"
                  "ModuleNotFoundError: No module named 'PIL'\n")])
    monkeypatch.setattr(rd, "_run", runner)

    assert rd.module_available("PIL") == (
        False, "ModuleNotFoundError: No module named 'PIL'")


def test_module_available_reports_a_missing_interpreter(monkeypatch):
    runner = Runner([OSError("no interpreter")])
    monkeypatch.setattr(rd, "_run", runner)

    assert rd.module_available("PIL") == (False, "no interpreter")


def test_install_tries_user_before_plain(monkeypatch):
    runner = Runner([Result(1), Result(0)])
    monkeypatch.setattr(rd, "_run", runner)

    assert rd.install_package(pillow()) is True

    installs = [call for call in runner.calls if "install" in call]
    assert len(installs) == 2
    assert "--user" in installs[0]
    assert "--user" not in installs[1]


def test_install_fails_when_both_forms_fail(monkeypatch):
    runner = Runner([Result(1), Result(1)])
    monkeypatch.setattr(rd, "_run", runner)

    assert rd.install_package(pillow()) is False
    assert len(runner.calls) == 2


def test_missing_pip_is_bootstrapped(monkeypatch):
    runner = Runner([Result(1), Result(0), Result(0)])
    monkeypatch.setattr(rd, "_run", runner)

    assert rd.ensure_pip() is True
    assert runner.calls[0][1:] == ["-m", "pip", "--version"]
    assert "ensurepip" in runner.calls[1]
    assert runner.calls[2][1:] == ["-m", "pip", "--version"]


def test_present_pip_is_not_bootstrapped(monkeypatch):
    runner = Runner([Result(0)])
    monkeypatch.setattr(rd, "_run", runner)

    assert rd.ensure_pip() is True
    assert len(runner.calls) == 1


def test_check_mode_reports_present_without_installing(monkeypatch, capsys):
    monkeypatch.setattr(rd, "module_available", lambda name: (True, ""))
    monkeypatch.setattr(rd, "ensure_pip", no_install)
    monkeypatch.setattr(rd, "install_package", no_install)

    assert rd.main(["--check"]) == 0

    out = capsys.readouterr().out
    assert "[deps] Pillow: ok" in out
    assert "MISSING" not in out


def test_check_mode_reports_missing_without_installing(monkeypatch, capsys):
    monkeypatch.setattr(
        rd, "module_available",
        lambda name: (False, "ModuleNotFoundError: No module named 'PIL'"))
    monkeypatch.setattr(rd, "ensure_pip", no_install)
    monkeypatch.setattr(rd, "install_package", no_install)

    assert rd.main(["--check"]) == 1

    out = capsys.readouterr().out
    assert "[deps] Pillow: MISSING" in out
    assert "install it automatically" in out


def test_missing_package_is_installed_and_verified(monkeypatch, capsys):
    states = iter([(False, "No module named 'PIL'"), (True, "")])
    monkeypatch.setattr(rd, "module_available", lambda name: next(states))
    monkeypatch.setattr(rd, "ensure_pip", lambda: True)
    installed = []
    monkeypatch.setattr(
        rd, "install_package",
        lambda dep: installed.append(dep.package) or True)

    assert rd.main([]) == 0

    assert installed == ["Pillow"]
    assert "[deps] Python packages are ready." in capsys.readouterr().out


def test_install_that_does_not_take_effect_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        rd, "module_available",
        lambda name: (False, "ModuleNotFoundError: No module named 'PIL'"))
    monkeypatch.setattr(rd, "ensure_pip", lambda: True)
    monkeypatch.setattr(rd, "install_package", lambda dep: True)

    assert rd.main([]) == 1

    out = capsys.readouterr().out
    assert "still not importable after installation" in out
    assert sys.executable in out
    assert "pip install --user Pillow" in out
    assert "internet connection or proxy" in out
