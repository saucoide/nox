# Copyright 2016 Alethea Katherine Flowers
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import argparse
import logging
import operator
import os
import re
import shutil
import subprocess
import sys
import tempfile
import typing
from pathlib import Path
from typing import Any, Literal, NoReturn
from unittest import mock

import pytest

import nox._decorators
import nox.command
import nox.manifest
import nox.popen
import nox.registry
import nox.sessions
import nox.virtualenv
from nox import _options
from nox.logger import logger

HAS_CONDA = shutil.which("conda") is not None
has_conda = pytest.mark.skipif(not HAS_CONDA, reason="Missing conda command.")

DIR = Path(__file__).parent.resolve()


def run_with_defaults(**kwargs: Any) -> dict[str, Any]:
    return {
        "env": None,
        "silent": False,
        "paths": None,
        "success_codes": None,
        "log": True,
        "external": False,
        "stdout": None,
        "stderr": subprocess.STDOUT,
        "interrupt_timeout": nox.popen.DEFAULT_INTERRUPT_TIMEOUT,
        "terminate_timeout": nox.popen.DEFAULT_TERMINATE_TIMEOUT,
        **kwargs,
    }


def _run_with_defaults(**kwargs: Any) -> dict[str, Any]:
    return {
        "env": None,
        "include_outer_env": True,
        "silent": False,
        "success_codes": None,
        "log": True,
        "external": False,
        "stdout": None,
        "stderr": subprocess.STDOUT,
        "interrupt_timeout": nox.popen.DEFAULT_INTERRUPT_TIMEOUT,
        "terminate_timeout": nox.popen.DEFAULT_TERMINATE_TIMEOUT,
        **kwargs,
    }


def test__normalize_path() -> None:
    envdir = "envdir"
    normalize = nox.sessions._normalize_path
    assert normalize(envdir, "hello") == os.path.join("envdir", "hello")
    assert normalize(envdir, b"hello") == os.path.join("envdir", "hello")
    assert normalize(envdir, "hello(world)") == os.path.join("envdir", "hello-world")
    assert normalize(envdir, "hello(world, meep)") == os.path.join(
        "envdir", "hello-world-meep"
    )
    assert normalize(envdir, 'tests(interpreter="python2.7", django="1.10")') == (
        os.path.join("envdir", "tests-interpreter-python2-7-django-1-10")
    )


def test__normalize_path_hash() -> None:
    envdir = "d" * (100 - len("bin/pythonX.Y") - 10)
    norm_path = nox.sessions._normalize_path(envdir, "a-really-long-virtualenv-path")
    assert "a-really-long-virtualenv-path" not in norm_path
    assert len(norm_path) < 100


def test__normalize_path_give_up() -> None:
    envdir = "d" * 100
    norm_path = nox.sessions._normalize_path(envdir, "any-path")
    assert "any-path" in norm_path


class FakeEnv(mock.MagicMock):
    _get_env = nox.virtualenv.VirtualEnv._get_env


def make_fake_env(venv_backend: str = "venv", **kwargs: Any) -> FakeEnv:
    return FakeEnv(
        spec=nox.virtualenv.VirtualEnv,
        env={},
        venv_backend=venv_backend,
        **kwargs,
    )


class TestSession:
    def make_session_and_runner(
        self,
    ) -> tuple[nox.sessions.Session, nox.sessions.SessionRunner]:
        func = mock.Mock(spec=["python"], python="3.7")
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=func,
            global_config=_options.options.namespace(
                posargs=[],
                error_on_external_run=False,
                install_only=False,
                invoked_from=os.getcwd(),
            ),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = make_fake_env(bin_paths=["/no/bin/for/you"])
        assert isinstance(runner.venv, nox.virtualenv.VirtualEnv)
        return nox.sessions.Session(runner=runner), runner

    def test_create_tmp(self) -> None:
        session, runner = self.make_session_and_runner()
        with tempfile.TemporaryDirectory() as root:
            runner.global_config.envdir = root
            tmpdir = session.create_tmp()
            assert session.env["TMPDIR"] == os.path.abspath(tmpdir)
            assert tmpdir.startswith(root)

    def test_create_tmp_twice(self) -> None:
        session, runner = self.make_session_and_runner()
        with tempfile.TemporaryDirectory() as root:
            runner.global_config.envdir = root
            assert runner.venv
            runner.venv.bin = bin  # type: ignore[misc, assignment]
            session.create_tmp()
            tmpdir = session.create_tmp()
            assert session.env["TMPDIR"] == os.path.abspath(tmpdir)
            assert tmpdir.startswith(root)

    def test_properties(self) -> None:
        session, runner = self.make_session_and_runner()
        with tempfile.TemporaryDirectory() as root:
            runner.global_config.envdir = root

            assert session.name is runner.friendly_name
            assert runner.venv
            assert session.env is runner.venv.env
            assert session.posargs == runner.global_config.posargs
            assert session.virtualenv is runner.venv
            assert runner.venv.bin_paths
            assert session.bin_paths is runner.venv.bin_paths
            assert session.bin is runner.venv.bin_paths[0]
            assert session.python is runner.func.python
            assert session.invoked_from is runner.global_config.invoked_from
            assert session.cache_dir == Path(runner.global_config.envdir).joinpath(
                ".cache"
            )

    def test_no_bin_paths(self) -> None:
        session, runner = self.make_session_and_runner()

        assert runner.venv
        runner.venv.bin_paths = None  # type: ignore[misc]
        with pytest.raises(
            ValueError, match=r"^The environment does not have a bin directory\.$"
        ):
            session.bin  # noqa: B018
        assert session.bin_paths is None

    def test_virtualenv_as_none(self) -> None:
        session, runner = self.make_session_and_runner()

        runner.venv = None

        with pytest.raises(ValueError, match="virtualenv"):
            _ = session.virtualenv

        assert session.venv_backend == "none"

    def test_interactive(self) -> None:
        session, _runner = self.make_session_and_runner()

        with mock.patch("nox.sessions.sys.stdin.isatty") as m_isatty:
            m_isatty.return_value = True

            assert session.interactive is True

            m_isatty.return_value = False

            assert session.interactive is False

    def test_explicit_non_interactive(self) -> None:
        session, runner = self.make_session_and_runner()

        with mock.patch("nox.sessions.sys.stdin.isatty") as m_isatty:
            m_isatty.return_value = True
            runner.global_config.non_interactive = True

            assert session.interactive is False

    def test_chdir(self, tmp_path: Path) -> None:
        cdbby = tmp_path / "cdbby"
        cdbby.mkdir()
        current_cwd = os.getcwd()

        session, _ = self.make_session_and_runner()

        session.chdir(str(cdbby))

        assert cdbby.samefile(".")
        os.chdir(current_cwd)

    def test_chdir_ctx(self, tmp_path: Path) -> None:
        cdbby = tmp_path / "cdbby"
        cdbby.mkdir()
        current_cwd = Path.cwd().resolve()

        session, _ = self.make_session_and_runner()

        with session.chdir(cdbby):
            assert cdbby.samefile(".")

        assert current_cwd.samefile(".")

        os.chdir(current_cwd)

    def test_invoked_from(self, tmp_path: Path) -> None:
        cdbby = tmp_path / "cdbby"
        cdbby.mkdir()
        current_cwd = Path.cwd().resolve()

        session, _ = self.make_session_and_runner()

        session.chdir(cdbby)

        assert current_cwd.samefile(session.invoked_from)
        os.chdir(current_cwd)

    def test_chdir_pathlib(self, tmp_path: Path) -> None:
        cdbby = tmp_path / "cdbby"
        cdbby.mkdir()
        current_cwd = Path.cwd().resolve()

        session, _ = self.make_session_and_runner()

        session.chdir(cdbby)

        assert cdbby.samefile(".")
        os.chdir(current_cwd)

    def test_run_bad_args(self) -> None:
        session, _ = self.make_session_and_runner()

        with pytest.raises(ValueError, match="arg"):
            session.run()

    def test_run_with_func(self) -> None:
        session, _ = self.make_session_and_runner()

        assert session.run(operator.add, 1, 2) == 3  # type: ignore[arg-type]

    def test_run_with_func_error(self) -> None:
        session, _ = self.make_session_and_runner()

        def raise_value_error() -> NoReturn:
            msg = "meep"
            raise ValueError(msg)

        with pytest.raises(nox.command.CommandFailed):
            assert session.run(raise_value_error)  # type: ignore[arg-type]

    def test_run_install_only(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        session, runner = self.make_session_and_runner()
        runner.global_config.install_only = True

        with mock.patch.object(nox.command, "run") as run:
            assert session.run("spam", "eggs") is None

        run.assert_not_called()

        assert "install-only" in caplog.text

    def test_run_install_only_should_install(self) -> None:
        session, runner = self.make_session_and_runner()
        runner.global_config.install_only = True

        with mock.patch.object(nox.command, "run") as run:
            session.install("spam")
            session.run("spam", "eggs")

        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(["/no/bin/for/you", env["PATH"]])

        run.assert_called_once_with(
            ("python", "-m", "pip", "install", "spam"),
            **run_with_defaults(paths=mock.ANY, silent=True, env=env, external="error"),
        )

    def test_run_success(self) -> None:
        session, _ = self.make_session_and_runner()
        result = session.run(sys.executable, "-c", "print(123)")
        assert result

    def test_run_error(self) -> None:
        session, _ = self.make_session_and_runner()

        with pytest.raises(nox.command.CommandFailed):
            session.run(sys.executable, "-c", "import sys; sys.exit(1)")

    def test_run_install_script(self) -> None:
        session, _ = self.make_session_and_runner()

        with mock.patch.object(nox.command, "run") as run:
            session.install_and_run_script(DIR / "resources/pep721example1.py")

        assert len(run.call_args_list) == 2
        assert "rich" in run.call_args_list[0][0][0]
        assert DIR / "resources/pep721example1.py" in run.call_args_list[1][0][0]

    def test_run_overly_env(self) -> None:
        session, runner = self.make_session_and_runner()
        assert runner.venv
        runner.venv.env["A"] = "1"
        runner.venv.env["B"] = "2"
        runner.venv.env["C"] = "4"
        result = session.run(
            sys.executable,
            "-c",
            'import os; print(os.environ["A"], os.environ["B"], os.environ.get("C", "5"))',
            env={"B": "3", "C": None},
            silent=True,
        )
        assert result
        assert result.strip() == "1 3 5"

    def test_by_default_all_invocation_env_vars_are_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("I_SHOULD_BE_INCLUDED", "happy")
        session, runner = self.make_session_and_runner()
        assert runner.venv
        runner.venv.env["I_SHOULD_BE_INCLUDED_TOO"] = "happier"
        runner.venv.env["EVERYONE_SHOULD_BE_INCLUDED_TOO"] = "happiest"
        result = session.run(
            sys.executable,
            "-c",
            "import os; print(os.environ)",
            silent=True,
        )
        assert result
        assert "happy" in result
        assert "happier" in result
        assert "happiest" in result

    def test_no_included_invocation_env_vars_are_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("I_SHOULD_NOT_BE_INCLUDED", "sad")
        monkeypatch.setenv("AND_NEITHER_SHOULD_I", "unhappy")
        session, runner = self.make_session_and_runner()
        assert runner.venv
        result = session.run(
            sys.executable,
            "-c",
            "import os; print(os.environ)",
            env={"I_SHOULD_BE_INCLUDED": "happy"},
            include_outer_env=False,
            silent=True,
        )
        assert result
        assert "sad" not in result
        assert "unhappy" not in result
        assert "happy" in result

    def test_no_included_invocation_env_vars_are_passed_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("I_SHOULD_NOT_BE_INCLUDED", "sad")
        monkeypatch.setenv("AND_NEITHER_SHOULD_I", "unhappy")
        session, runner = self.make_session_and_runner()
        result = session.run(
            sys.executable,
            "-c",
            "import os; print(os.environ)",
            include_outer_env=False,
            silent=True,
        )
        assert result
        assert "sad" not in result
        assert "unhappy" not in result

    def test_run_external_not_a_virtualenv(self) -> None:
        # Non-virtualenv sessions should always allow external programs.
        session, runner = self.make_session_and_runner()

        runner.venv = nox.virtualenv.PassthroughEnv()

        with mock.patch("nox.command.run", autospec=True) as run:
            session.run(sys.executable, "--version")

        run.assert_called_once_with(
            (sys.executable, "--version"),
            **run_with_defaults(external=True, env=mock.ANY),
        )

    def test_run_external_condaenv(self) -> None:
        # condaenv sessions should always allow conda.
        session, runner = self.make_session_and_runner()
        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.allowed_globals = ("conda",)  # type: ignore[misc]
        runner.venv.env = {}
        runner.venv.bin_paths = ["/path/to/env/bin"]  # type: ignore[misc]
        runner.venv.create.return_value = True  # type: ignore[attr-defined]

        with mock.patch("nox.command.run", autospec=True) as run:
            session.run("conda", "--version")

        run.assert_called_once_with(
            ("conda", "--version"),
            **run_with_defaults(
                external=True, env=mock.ANY, paths=["/path/to/env/bin"]
            ),
        )

    def test_run_external_with_error_on_external_run(self) -> None:
        session, runner = self.make_session_and_runner()

        runner.global_config.error_on_external_run = True

        with pytest.raises(nox.command.CommandFailed, match="External"):
            session.run(sys.executable, "--version")

    def test_run_external_with_error_on_external_run_condaenv(self) -> None:
        session, runner = self.make_session_and_runner()
        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.env = {}
        runner.venv.bin_paths = ["/path/to/env/bin"]  # type: ignore[misc]

        runner.global_config.error_on_external_run = True

        with pytest.raises(nox.command.CommandFailed, match="External"):
            session.run(sys.executable, "--version")

    def test_run_install_bad_args(self) -> None:
        session, _ = self.make_session_and_runner()

        with pytest.raises(
            ValueError, match="At least one argument required to run_install"
        ):
            session.run_install()

    def test_run_no_install_passthrough(self) -> None:
        session, runner = self.make_session_and_runner()
        runner.venv = nox.virtualenv.PassthroughEnv()
        runner.global_config.no_install = True

        session.install("numpy")
        session.conda_install("numpy")

    def test_run_no_conda_install(self) -> None:
        session, _runner = self.make_session_and_runner()

        with pytest.raises(TypeError, match="A session without a conda"):
            session.conda_install("numpy")

    def test_run_install_success(self) -> None:
        session, _ = self.make_session_and_runner()

        assert session.run_install(operator.add, 1300, 37) == 1337  # type: ignore[arg-type]

    def test_run_install_install_only(self) -> None:
        session, runner = self.make_session_and_runner()
        runner.global_config.install_only = True

        assert session.run_install(operator.add, 23, 19) == 42  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        (
            "interrupt_timeout_setting",
            "terminate_timeout_setting",
            "interrupt_timeout_expected",
            "terminate_timeout_expected",
        ),
        [
            ("default", "default", 0.3, 0.2),
            (None, None, None, None),
            (0, 0, 0, 0),
            (1, 2, 1, 2),
        ],
    )
    def test_run_shutdown_process_timeouts(
        self,
        interrupt_timeout_setting: Literal["default"] | int | None,
        terminate_timeout_setting: Literal["default"] | int | None,
        interrupt_timeout_expected: float | None,
        terminate_timeout_expected: float | None,
    ) -> None:
        session, runner = self.make_session_and_runner()

        runner.venv = nox.virtualenv.PassthroughEnv()

        subp_popen_instance = mock.Mock()
        subp_popen_instance.communicate.side_effect = KeyboardInterrupt()
        with mock.patch(
            "nox.popen.shutdown_process", autospec=True
        ) as shutdown_process, mock.patch(
            "nox.popen.subprocess.Popen",
            new=mock.Mock(return_value=subp_popen_instance),
        ):
            shutdown_process.return_value = ("", "")

            timeout_kwargs: dict[str, None | float] = {}
            if interrupt_timeout_setting != "default":
                timeout_kwargs["interrupt_timeout"] = interrupt_timeout_setting
            if terminate_timeout_setting != "default":
                timeout_kwargs["terminate_timeout"] = terminate_timeout_setting

            with pytest.raises(KeyboardInterrupt):
                session.run(sys.executable, "--version", **timeout_kwargs)  # type: ignore[arg-type]

        shutdown_process.assert_called_once_with(
            proc=mock.ANY,
            interrupt_timeout=interrupt_timeout_expected,
            terminate_timeout=terminate_timeout_expected,
        )

    @pytest.mark.parametrize(
        ("no_install", "reused", "run_called"),
        [
            (True, True, False),
            (True, False, True),
            (False, True, True),
            (False, False, True),
        ],
    )
    @pytest.mark.parametrize("run_install_func", ["run_always", "run_install"])
    def test_run_install_no_install(
        self, no_install: bool, reused: bool, run_called: bool, run_install_func: str
    ) -> None:
        session, runner = self.make_session_and_runner()
        runner.global_config.no_install = no_install
        assert runner.venv
        runner.venv._reused = reused

        with mock.patch.object(nox.command, "run") as run:
            run_install = getattr(session, run_install_func)
            run_install("python", "-m", "pip", "install", "requests")

        assert run.called is run_called

    def test_conda_install_bad_args(self) -> None:
        session, runner = self.make_session_and_runner()
        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.location = "dummy"

        with pytest.raises(ValueError, match="arg"):
            session.conda_install()

    def test_conda_install_bad_args_odd_nb_double_quotes(self) -> None:
        session, runner = self.make_session_and_runner()
        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.location = "./not/a/location"

        with pytest.raises(ValueError, match="odd number of quotes"):
            session.conda_install('a"a')

    def test_conda_install_bad_args_cannot_escape(self) -> None:
        session, runner = self.make_session_and_runner()
        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.location = "./not/a/location"

        with pytest.raises(ValueError, match="Cannot escape"):
            session.conda_install('a"o"<a')

    def test_conda_install_not_a_condaenv(self) -> None:
        session, runner = self.make_session_and_runner()

        runner.venv = None

        with pytest.raises(TypeError, match="conda environment"):
            session.conda_install()

    @pytest.mark.parametrize(
        "auto_offline", [False, True], ids="auto_offline={}".format
    )
    @pytest.mark.parametrize("offline", [False, True], ids="offline={}".format)
    @pytest.mark.parametrize("conda", ["conda", "mamba"], ids=str)
    @pytest.mark.parametrize(
        "channel",
        ["", "conda-forge", ["conda-forge", "bioconda"]],
        ids=["default", "conda-forge", "bioconda"],
    )
    def test_conda_install(
        self, auto_offline: bool, offline: bool, conda: str, channel: str | list[str]
    ) -> None:
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=mock.sentinel.func,
            global_config=_options.options.namespace(posargs=[]),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.location = "/path/to/conda/env"
        runner.venv.env = {}
        runner.venv.is_offline = lambda: offline  # type: ignore[attr-defined]
        runner.venv.conda_cmd = conda  # type: ignore[attr-defined]

        class SessionNoSlots(nox.sessions.Session):
            pass

        session = SessionNoSlots(runner=runner)

        with mock.patch.object(session, "_run", autospec=True) as run:
            args = ["--offline"] if auto_offline and offline else []
            if channel and isinstance(channel, str):
                args.append(f"--channel={channel}")
            else:
                args += [f"--channel={c}" for c in channel]
            session.conda_install(
                "requests", "urllib3", auto_offline=auto_offline, channel=channel
            )
            run.assert_called_once_with(
                conda,
                "install",
                "--yes",
                *args,
                "--prefix",
                "/path/to/conda/env",
                "requests",
                "urllib3",
                **_run_with_defaults(silent=True, external="error"),
            )

    @pytest.mark.parametrize(
        ("no_install", "reused", "run_called"),
        [
            (True, True, False),
            (True, False, True),
            (False, True, True),
            (False, False, True),
        ],
    )
    def test_conda_venv_reused_with_no_install(
        self, no_install: bool, reused: bool, run_called: bool
    ) -> None:
        session, runner = self.make_session_and_runner()

        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.location = "/path/to/conda/env"
        runner.venv.env = {}
        runner.venv.is_offline = lambda: True  # type: ignore[attr-defined]
        runner.venv.conda_cmd = "conda"  # type: ignore[attr-defined]

        runner.global_config.no_install = no_install
        runner.venv._reused = reused

        with mock.patch.object(nox.command, "run") as run:
            session.conda_install("baked beans", "eggs", "spam")

        assert run.called is run_called

    @pytest.mark.parametrize(
        "version_constraint",
        ["no", "yes", "already_dbl_quoted"],
        ids="version_constraint={}".format,
    )
    def test_conda_install_non_default_kwargs(self, version_constraint: str) -> None:
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=mock.sentinel.func,
            global_config=_options.options.namespace(posargs=[]),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = mock.create_autospec(nox.virtualenv.CondaEnv)
        assert runner.venv
        runner.venv.location = "/path/to/conda/env"
        runner.venv.env = {}
        runner.venv.is_offline = lambda: False  # type: ignore[attr-defined]
        runner.venv.conda_cmd = "conda"  # type: ignore[attr-defined]

        class SessionNoSlots(nox.sessions.Session):
            pass

        session = SessionNoSlots(runner=runner)

        if version_constraint == "no":
            pkg_requirement = passed_arg = "urllib3"
        elif version_constraint == "yes":
            pkg_requirement = "urllib3<1.25"
            passed_arg = f'"{pkg_requirement}"'
        elif version_constraint == "already_dbl_quoted":
            pkg_requirement = passed_arg = '"urllib3<1.25"'
        else:
            raise ValueError(version_constraint)

        with mock.patch.object(session, "_run", autospec=True) as run:
            session.conda_install("requests", pkg_requirement, silent=False)
            run.assert_called_once_with(
                "conda",
                "install",
                "--yes",
                "--prefix",
                "/path/to/conda/env",
                "requests",
                # this will be double quoted if unquoted constraint is present
                passed_arg,
                **_run_with_defaults(silent=False, external="error"),
            )

    def test_install_bad_args_no_arg(self) -> None:
        session, _ = self.make_session_and_runner()

        with pytest.raises(ValueError, match="arg"):
            session.install()

    def test_install_not_a_virtualenv(self) -> None:
        session, runner = self.make_session_and_runner()

        runner.venv = None

        with pytest.raises(TypeError, match="virtualenv"):
            session.install()

    def test_install(self) -> None:
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=mock.sentinel.func,
            global_config=_options.options.namespace(posargs=[]),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = make_fake_env()
        assert isinstance(runner.venv, nox.virtualenv.VirtualEnv)

        class SessionNoSlots(nox.sessions.Session):
            pass

        session = SessionNoSlots(runner=runner)

        assert session.venv_backend == "venv"

        with mock.patch.object(session, "_run", autospec=True) as run:
            session.install("requests", "urllib3")
            run.assert_called_once_with(
                "python",
                "-m",
                "pip",
                "install",
                "requests",
                "urllib3",
                **_run_with_defaults(silent=True, external="error"),
            )

    def test_install_non_default_kwargs(self) -> None:
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=mock.sentinel.func,
            global_config=_options.options.namespace(posargs=[]),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = make_fake_env()
        assert isinstance(runner.venv, nox.virtualenv.VirtualEnv)

        class SessionNoSlots(nox.sessions.Session):
            pass

        session = SessionNoSlots(runner=runner)

        with mock.patch.object(session, "_run", autospec=True) as run:
            session.install("requests", "urllib3", silent=False)
            run.assert_called_once_with(
                "python",
                "-m",
                "pip",
                "install",
                "requests",
                "urllib3",
                **_run_with_defaults(silent=False, external="error"),
            )

    def test_install_no_venv_failure(self) -> None:
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=mock.sentinel.func,
            global_config=_options.options.namespace(posargs=[]),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = mock.create_autospec(nox.virtualenv.PassthroughEnv)
        assert runner.venv
        runner.venv.env = {}

        class SessionNoSlots(nox.sessions.Session):
            pass

        session = SessionNoSlots(runner=runner)

        with pytest.raises(
            ValueError,
            match=(
                r"use of session\.install\(\) is no longer allowed since"
                r" it would modify the global Python environment"
            ),
        ):
            session.install("requests", "urllib3")

    def test_notify(self) -> None:
        session, runner = self.make_session_and_runner()

        session.notify("other")

        runner.manifest.notify.assert_called_once_with("other", None)  # type: ignore[attr-defined]

        session.notify("other", posargs=["--an-arg"])

        runner.manifest.notify.assert_called_with("other", ["--an-arg"])  # type: ignore[attr-defined]

    def test_posargs_are_not_shared_between_sessions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry: dict[str, nox._decorators.Func] = {}
        monkeypatch.setattr("nox.registry._REGISTRY", registry)

        @nox.session(venv_backend="none")
        def test(session: nox.Session) -> None:
            session.posargs.extend(["-x"])

        @nox.session(venv_backend="none")
        def lint(session: nox.Session) -> None:
            if "-x" in session.posargs:
                msg = "invalid option: -x"
                raise RuntimeError(msg)

        config = _options.options.namespace(posargs=[], envdir=".nox")
        manifest = nox.manifest.Manifest(registry, config)

        assert manifest["test"].execute()
        assert manifest["lint"].execute()

    def test_log(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        session, _ = self.make_session_and_runner()

        session.log("meep")

        assert "meep" in caplog.text

    def test_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        session, _ = self.make_session_and_runner()

        session.warn("meep")

        assert "meep" in caplog.text

    def test_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        session, _ = self.make_session_and_runner()

        session.debug("meep")

        assert "meep" in caplog.text

    def test_error(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        session, _ = self.make_session_and_runner()

        with pytest.raises(nox.sessions._SessionQuit, match="meep"):
            session.error("meep")

    def test_error_no_log(self) -> None:
        session, _ = self.make_session_and_runner()

        with pytest.raises(nox.sessions._SessionQuit):
            session.error()

    def test_skip_no_log(self) -> None:
        session, _ = self.make_session_and_runner()

        with pytest.raises(nox.sessions._SessionSkip):
            session.skip()

    @pytest.mark.parametrize(
        ("no_install", "reused", "run_called"),
        [
            (True, True, False),
            (True, False, True),
            (False, True, True),
            (False, False, True),
        ],
    )
    def test_session_venv_reused_with_no_install(
        self, no_install: bool, reused: bool, run_called: bool
    ) -> None:
        session, runner = self.make_session_and_runner()
        runner.global_config.no_install = no_install
        assert runner.venv
        runner.venv._reused = reused

        with mock.patch.object(nox.command, "run") as run:
            session.install("eggs", "spam")

        assert run.called is run_called

    def test_install_uv(self) -> None:
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=mock.sentinel.func,
            global_config=_options.options.namespace(posargs=[]),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = make_fake_env(venv_backend="uv")
        assert isinstance(runner.venv, nox.virtualenv.VirtualEnv)
        assert runner.venv.venv_backend == "uv"

        class SessionNoSlots(nox.sessions.Session):
            pass

        session = SessionNoSlots(runner=runner)

        with mock.patch.object(session, "_run", autospec=True) as run:
            session.install("requests", "urllib3", silent=False)
            run.assert_called_once_with(
                "uv",
                "pip",
                "install",
                "requests",
                "urllib3",
                **_run_with_defaults(silent=False, external="error"),
            )

    def test_install_uv_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = nox.sessions.SessionRunner(
            name="test",
            signatures=["test"],
            func=mock.sentinel.func,
            global_config=_options.options.namespace(posargs=[]),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )
        runner.venv = make_fake_env(venv_backend="uv")
        assert isinstance(runner.venv, nox.virtualenv.VirtualEnv)
        assert runner.venv.venv_backend == "uv"

        class SessionNoSlots(nox.sessions.Session):
            pass

        session = SessionNoSlots(runner=runner)

        monkeypatch.setattr(nox.virtualenv, "UV", "/some/uv")
        monkeypatch.setattr(shutil, "which", lambda x, path=None: None)  # noqa: ARG005

        with mock.patch.object(nox.command, "run", autospec=True) as run:
            session.install("requests", "urllib3", silent=False)
            run.assert_called_once()

            ((call_args,), _) = run.call_args
            assert call_args == (
                "/some/uv",
                "pip",
                "install",
                "requests",
                "urllib3",
            )

        # User runs uvx
        with mock.patch.object(nox.command, "run", autospec=True) as run:
            session.run("uvx", "cowsay")
            run.assert_called_once()

            ((call_args,), _) = run.call_args
            assert call_args == (
                "/some/uvx",
                "cowsay",
            )

        # user installs uv in the session venv
        monkeypatch.setattr(
            shutil, "which", lambda x, path="": path + "/uv" if x == "uv" else None
        )

        with mock.patch.object(nox.command, "run", autospec=True) as run:
            session.install("requests", "urllib3", silent=False)
            run.assert_called_once()

            ((call_args,), _) = run.call_args
            assert call_args == (
                "uv",
                "pip",
                "install",
                "requests",
                "urllib3",
            )

    def test___slots__(self) -> None:
        session, _ = self.make_session_and_runner()
        with pytest.raises(AttributeError):
            session.foo = "bar"  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            session.quux  # type: ignore[attr-defined] # noqa: B018

    def test___dict__(self) -> None:
        session, _ = self.make_session_and_runner()
        expected = {name: getattr(session, name) for name in session.__slots__}
        assert session.__dict__ == expected

    def test_first_arg_list(self) -> None:
        session, _ = self.make_session_and_runner()

        with pytest.raises(
            ValueError, match="First argument to `session.run` is a list. Did you mean"
        ):
            session.run(["ls", "-al"])  # type: ignore[arg-type]


class TestSessionRunner:
    def make_runner(self) -> nox.sessions.SessionRunner:
        func = mock.Mock()
        func.python = None
        func.venv_backend = None
        func.reuse_venv = False
        func.requires = []
        return nox.sessions.SessionRunner(
            name="test",
            signatures=["test(1, 2)"],
            func=func,
            global_config=_options.options.namespace(
                noxfile=os.path.join(os.getcwd(), "noxfile.py"),
                envdir="envdir",
                posargs=[],
                reuse_venv="no",
                error_on_missing_interpreters="CI" in os.environ,
            ),
            manifest=mock.create_autospec(nox.manifest.Manifest),
        )

    def test_properties(self) -> None:
        runner = self.make_runner()

        assert runner.name == "test"
        assert runner.signatures == ["test(1, 2)"]
        assert runner.func is not None
        assert callable(runner.func)
        assert isinstance(runner.description, str)
        assert runner.global_config.posargs == []
        assert isinstance(runner.manifest, nox.manifest.Manifest)

    def test_str_and_friendly_name(self) -> None:
        runner = self.make_runner()
        runner.signatures = ["test(1, 2)", "test(3, 4)"]

        assert str(runner) == "Session(name=test, signatures=test(1, 2), test(3, 4))"
        assert runner.friendly_name == "test(1, 2)"

    def test_description_property_one_line(self) -> None:
        def foo() -> None:
            """Just one line"""

        runner = self.make_runner()
        runner.func = foo  # type: ignore[assignment]
        assert runner.description == "Just one line"

    def test_description_property_multi_line(self) -> None:
        def foo() -> None:
            """
            Multiline

            Extra description
            """

        runner = self.make_runner()
        runner.func = foo  # type: ignore[assignment]
        assert runner.description == "Multiline"

    def test_description_property_no_doc(self) -> None:
        def foo() -> None:
            pass

        runner = self.make_runner()
        runner.func = foo  # type: ignore[assignment]
        assert runner.description is None

    def test__create_venv_process_env(self) -> None:
        runner = self.make_runner()
        runner.func.python = False

        runner._create_venv()

        assert isinstance(runner.venv, nox.virtualenv.ProcessEnv)

    @mock.patch("nox.virtualenv.VirtualEnv.create", autospec=True)
    def test__create_venv(self, create: mock.Mock) -> None:
        runner = self.make_runner()

        runner._create_venv()

        create.assert_called_once_with(runner.venv)
        assert isinstance(runner.venv, nox.virtualenv.VirtualEnv)
        assert runner.venv.location.endswith(os.path.join("envdir", "test-1-2"))
        assert runner.venv.interpreter is None
        assert runner.venv.reuse_existing is False

    @pytest.mark.parametrize(
        ("create_method", "venv_backend", "expected_backend"),
        [
            ("nox.virtualenv.VirtualEnv.create", None, nox.virtualenv.VirtualEnv),
            (
                "nox.virtualenv.VirtualEnv.create",
                "virtualenv",
                nox.virtualenv.VirtualEnv,
            ),
            ("nox.virtualenv.VirtualEnv.create", "venv", nox.virtualenv.VirtualEnv),
            pytest.param(
                "nox.virtualenv.CondaEnv.create",
                "conda",
                nox.virtualenv.CondaEnv,
                marks=has_conda,
            ),
        ],
    )
    def test__create_venv_options(
        self,
        create_method: str,
        venv_backend: None | str,
        expected_backend: type[nox.virtualenv.VirtualEnv | nox.virtualenv.CondaEnv],
    ) -> None:
        runner = self.make_runner()
        runner.func.python = "coolpython"
        runner.func.reuse_venv = True
        runner.func.venv_backend = venv_backend

        with mock.patch(create_method, autospec=True) as create:
            runner._create_venv()

        create.assert_called_once_with(runner.venv)
        assert isinstance(runner.venv, expected_backend)
        assert runner.venv.interpreter == "coolpython"  # type: ignore[union-attr]
        assert runner.venv.reuse_existing is True  # type: ignore[union-attr]

    def test__create_venv_unexpected_venv_backend(self) -> None:
        runner = self.make_runner()
        runner.func.venv_backend = "somenewenvtool"
        with pytest.raises(ValueError, match="venv_backend"):
            runner._create_venv()

    @pytest.mark.parametrize(
        "venv_backend",
        ["uv|virtualenv", "conda|virtualenv", "mamba|conda|venv"],
    )
    def test_fallback_venv(
        self, venv_backend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = self.make_runner()
        runner.func.venv_backend = venv_backend
        monkeypatch.setattr(
            nox.virtualenv,
            "OPTIONAL_VENVS",
            {"uv": False, "conda": False, "mamba": False},
        )
        with mock.patch("nox.virtualenv.VirtualEnv.create", autospec=True):
            runner._create_venv()
        assert runner.venv
        assert runner.venv.venv_backend == venv_backend.split("|")[-1]

    @pytest.mark.parametrize(
        "venv_backend",
        [
            "uv|virtualenv|unknown",
            "conda|unknown|virtualenv",
            "virtualenv|venv",
            "conda|mamba",
        ],
    )
    def test_invalid_fallback_venv(
        self, venv_backend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = self.make_runner()
        runner.func.venv_backend = venv_backend
        monkeypatch.setattr(
            nox.virtualenv,
            "OPTIONAL_VENVS",
            {"uv": False, "conda": False, "mamba": False},
        )
        with mock.patch(
            "nox.virtualenv.VirtualEnv.create", autospec=True
        ), pytest.raises(
            ValueError,
            match="No backends present|Only optional backends|Expected venv_backend",
        ):
            runner._create_venv()

    @pytest.mark.parametrize(
        ("reuse_venv", "reuse_venv_func", "should_reuse"),
        [
            ("yes", None, True),
            ("yes", False, False),
            ("yes", True, True),
            ("no", None, False),
            ("no", False, False),
            ("no", True, True),
            ("always", None, True),
            ("always", False, True),
            ("always", True, True),
            ("never", None, False),
            ("never", False, False),
            ("never", True, False),
        ],
    )
    def test__reuse_venv_outcome(
        self, reuse_venv: str, reuse_venv_func: bool | None, should_reuse: bool
    ) -> None:
        runner = self.make_runner()
        runner.func.reuse_venv = reuse_venv_func
        runner.global_config.reuse_venv = reuse_venv
        assert runner.reuse_existing_venv() == should_reuse

    def test__reuse_venv_invalid(self) -> None:
        runner = self.make_runner()
        runner.global_config.reuse_venv = True
        msg = "nox.options.reuse_venv must be set to 'always', 'never', 'no', or 'yes', got True!"
        with pytest.raises(AttributeError, match=re.escape(msg)):
            runner.reuse_existing_venv()

    def make_runner_with_mock_venv(self) -> nox.sessions.SessionRunner:
        runner = self.make_runner()
        runner._create_venv = mock.Mock()  # type: ignore[method-assign]
        runner.venv = make_fake_env()
        assert isinstance(runner.venv, nox.virtualenv.VirtualEnv)
        return runner

    def test_execute_noop_success(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)

        runner = self.make_runner_with_mock_venv()

        result = runner.execute()

        assert result
        runner.func.assert_called_once_with(mock.ANY)  # type: ignore[attr-defined]
        assert "Running session test(1, 2)" in caplog.text

    def test_execute_quit(self) -> None:
        runner = self.make_runner_with_mock_venv()

        def func(session: nox.Session) -> None:
            session.error("meep")

        func.requires = []  # type: ignore[attr-defined]
        runner.func = func  # type: ignore[assignment]

        result = runner.execute()

        assert result.status == nox.sessions.Status.ABORTED

    def test_execute_skip(self) -> None:
        runner = self.make_runner_with_mock_venv()

        def func(session: nox.Session) -> None:
            session.skip("meep")

        func.requires = []  # type: ignore[attr-defined]
        runner.func = func  # type: ignore[assignment]

        result = runner.execute()

        assert result.status == nox.sessions.Status.SKIPPED

    def test_execute_with_manifest_null_session_func(self) -> None:
        runner = self.make_runner()
        runner.func = nox.manifest._null_session_func

        result = runner.execute()

        assert result.status == nox.sessions.Status.SKIPPED
        assert result.reason
        assert "no parameters" in result.reason

    def test_execute_skip_missing_interpreter(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Important to have this first here as the runner will pick it up
        # to set default for --error-on-missing-interpreters
        monkeypatch.delenv("CI", raising=False)

        runner = self.make_runner_with_mock_venv()
        runner._create_venv.side_effect = nox.virtualenv.InterpreterNotFound("meep")  # type: ignore[attr-defined]

        result = runner.execute()

        assert result.status == nox.sessions.Status.SKIPPED
        assert result.reason
        assert "meep" in result.reason
        assert (
            "Missing interpreters will error by default on CI systems." in caplog.text
        )

    def test_execute_missing_interpreter_on_CI(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CI", "True")
        runner = self.make_runner_with_mock_venv()
        runner._create_venv.side_effect = nox.virtualenv.InterpreterNotFound("meep")  # type: ignore[attr-defined]

        result = runner.execute()

        assert result.status == nox.sessions.Status.FAILED
        assert result.reason
        assert "meep" in result.reason

    def test_execute_error_missing_interpreter(self) -> None:
        runner = self.make_runner_with_mock_venv()
        runner.global_config.error_on_missing_interpreters = True
        runner._create_venv.side_effect = nox.virtualenv.InterpreterNotFound("meep")  # type: ignore[attr-defined]

        result = runner.execute()

        assert result.status == nox.sessions.Status.FAILED
        assert result.reason
        assert "meep" in result.reason

    def test_execute_failed(self) -> None:
        runner = self.make_runner_with_mock_venv()

        def func(session: nox.Session) -> None:  # noqa: ARG001
            raise nox.command.CommandFailed()

        func.requires = []  # type: ignore[attr-defined]
        runner.func = func  # type: ignore[assignment]

        result = runner.execute()

        assert result.status == nox.sessions.Status.FAILED

    def test_execute_interrupted(self) -> None:
        runner = self.make_runner_with_mock_venv()

        def func(session: nox.Session) -> None:  # noqa: ARG001
            raise KeyboardInterrupt()

        func.requires = []  # type: ignore[attr-defined]
        runner.func = func  # type: ignore[assignment]

        with pytest.raises(KeyboardInterrupt):
            runner.execute()

    def test_execute_exception(self) -> None:
        runner = self.make_runner_with_mock_venv()

        def func(session: nox.Session) -> None:  # noqa: ARG001
            msg = "meep"
            raise ValueError(msg)

        func.requires = []  # type: ignore[attr-defined]
        runner.func = func  # type: ignore[assignment]

        result = runner.execute()

        assert result.status == nox.sessions.Status.FAILED

    def test_execute_check_env(self) -> None:
        runner = self.make_runner_with_mock_venv()

        def func(session: nox.Session) -> None:
            session.run(
                sys.executable,
                "-c",
                "import os; raise SystemExit(0 if"
                f' os.environ["NOX_CURRENT_SESSION"] == {session.name!r} else 0)',
            )

        func.requires = []  # type: ignore[attr-defined]
        runner.func = func  # type: ignore[assignment]

        result = runner.execute()

        assert result


class TestResult:
    def test_init(self) -> None:
        result = nox.sessions.Result(
            session=mock.sentinel.SESSION, status=mock.sentinel.STATUS
        )
        assert result.session == mock.sentinel.SESSION
        assert result.status == mock.sentinel.STATUS

    def test__bool_true(self) -> None:
        for status in (nox.sessions.Status.SUCCESS, nox.sessions.Status.SKIPPED):
            result = nox.sessions.Result(
                session=typing.cast(nox.sessions.SessionRunner, object()), status=status
            )
            assert bool(result)

    def test__bool_false(self) -> None:
        for status in (nox.sessions.Status.FAILED, nox.sessions.Status.ABORTED):
            result = nox.sessions.Result(
                session=typing.cast(nox.sessions.SessionRunner, object()), status=status
            )
            assert not bool(result)

    def test__imperfect(self) -> None:
        result = nox.sessions.Result(
            typing.cast(nox.sessions.SessionRunner, object()),
            nox.sessions.Status.SUCCESS,
        )
        assert result.imperfect == "was successful"
        result = nox.sessions.Result(
            typing.cast(nox.sessions.SessionRunner, object()),
            nox.sessions.Status.FAILED,
        )
        assert result.imperfect == "failed"
        result = nox.sessions.Result(
            typing.cast(nox.sessions.SessionRunner, object()),
            nox.sessions.Status.FAILED,
            reason="meep",
        )
        assert result.imperfect == "failed: meep"

    def test__log_success(self) -> None:
        result = nox.sessions.Result(
            typing.cast(nox.sessions.SessionRunner, object()),
            nox.sessions.Status.SUCCESS,
        )
        with mock.patch.object(logger, "success") as success:
            result.log("foo")
            success.assert_called_once_with("foo")

    def test__log_warning(self) -> None:
        result = nox.sessions.Result(
            typing.cast(nox.sessions.SessionRunner, object()),
            nox.sessions.Status.SKIPPED,
        )
        with mock.patch.object(logger, "warning") as warning:
            result.log("foo")
            warning.assert_called_once_with("foo")

    def test__log_error(self) -> None:
        result = nox.sessions.Result(
            typing.cast(nox.sessions.SessionRunner, object()),
            nox.sessions.Status.FAILED,
        )
        with mock.patch.object(logger, "error") as error:
            result.log("foo")
            error.assert_called_once_with("foo")

    def test__serialize(self) -> None:
        result = nox.sessions.Result(
            session=typing.cast(
                nox.sessions.SessionRunner,
                argparse.Namespace(
                    signatures=["siggy"], name="namey", func=mock.Mock()
                ),
            ),
            status=nox.sessions.Status.SUCCESS,
        )
        answer = result.serialize()
        assert answer["name"] == "namey"
        assert answer["result"] == "success"
        assert answer["result_code"] == 1
        assert answer["signatures"] == ["siggy"]
