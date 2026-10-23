"""Tests for split_dex.registry."""

import importlib.metadata
import pathlib

import pytest

from split_dex import registry
from split_dex import strategy
from split_dex.strategies import single
from tests import class_view

# A user's strategy file: one strategy class, and one class that is not a strategy.
_USER_STRATEGIES = '''
"""Strategies of a user, for the tests."""

from split_dex import strategy


class FirstAndRest:
    """Put the first `count` classes in one group and the others in another."""

    def __init__(self, *, count: int = 1) -> None:
        self._count = count

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        ids = list(classes.ids)
        return [
            strategy.Group.of_classes(ids[: self._count]),
            strategy.Group.of_classes(ids[self._count :]),
        ]


class NoPlan:
    """Not a strategy: it has no plan method."""
'''


def _group_sizes(created: strategy.Strategy, class_count: int) -> list[int]:
    classes = class_view.make([f"Lp/C{index};" for index in range(class_count)])
    return [len(group.class_ids) for group in created.plan(classes)]


def _write_module(directory: pathlib.Path, module_name: str) -> pathlib.Path:
    path = directory / f"{module_name}.py"
    path.write_text(_USER_STRATEGIES, encoding="utf-8")
    return path


def _install_plugins(
    monkeypatch: pytest.MonkeyPatch, *plugins: importlib.metadata.EntryPoint
) -> None:
    """Make importlib.metadata list the given entry points as installed plugins."""

    def entry_points(*, group: str) -> importlib.metadata.EntryPoints:
        return importlib.metadata.EntryPoints(plugin for plugin in plugins if plugin.group == group)

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)


def testCreate_whenTheNameIsBuiltIn_returnsThatStrategy() -> None:
    created = registry.create("single", {})

    assert isinstance(created, single.Single)


def testCreate_whenASettingIsGiven_convertsItToTheParameterType() -> None:
    # max-files sets the int parameter max_files: four input DEX files merge into two.
    created = registry.create("original", {"max-files": "2"})

    groups = created.plan(class_view.make(["La;", "Lb;", "Lc;", "Ld;"], sources=[0, 1, 2, 3]))

    assert len(groups) == 2


def testCreate_whenTheSpecIsAFileAndAClass_loadsTheClassWithItsSettings(
    tmp_path: pathlib.Path,
) -> None:
    path = _write_module(tmp_path, "mine")

    created = registry.create(f"{path}:FirstAndRest", {"count": "2"})

    assert _group_sizes(created, 3) == [2, 1]


def testCreate_whenTheSpecIsAModuleAndAClass_importsTheModule(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(tmp_path, "split_dex_test_module_strategies")
    monkeypatch.syspath_prepend(str(tmp_path))

    created = registry.create("split_dex_test_module_strategies:FirstAndRest", {})

    assert _group_sizes(created, 3) == [1, 2]


def testCreate_whenAPluginDeclaresTheStrategy_findsItByName(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(tmp_path, "split_dex_test_plugin_strategies")
    monkeypatch.syspath_prepend(str(tmp_path))
    _install_plugins(
        monkeypatch,
        importlib.metadata.EntryPoint(
            name="first-and-rest",
            value="split_dex_test_plugin_strategies:FirstAndRest",
            group=registry.PLUGIN_GROUP,
        ),
    )

    created = registry.create("first-and-rest", {"count": "2"})

    assert _group_sizes(created, 3) == [2, 1]


def testCreate_whenAPluginHasTheNameOfABuiltIn_usesTheBuiltIn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_plugins(
        monkeypatch,
        importlib.metadata.EntryPoint(
            name="single", value="no_such_module:Single", group=registry.PLUGIN_GROUP
        ),
    )

    created = registry.create("single", {})

    assert isinstance(created, single.Single)


def testCreate_whenTheNameIsUnknown_raisesStrategyError() -> None:
    with pytest.raises(strategy.StrategyError, match="unknown strategy 'nope'"):
        registry.create("nope", {})


def testCreate_whenTheFileDoesNotExist_raisesStrategyError(tmp_path: pathlib.Path) -> None:
    with pytest.raises(strategy.StrategyError, match="strategy file not found"):
        registry.create(f"{tmp_path / 'missing.py'}:FirstAndRest", {})


def testCreate_whenTheClassIsNotInTheFile_raisesStrategyError(tmp_path: pathlib.Path) -> None:
    path = _write_module(tmp_path, "mine")

    with pytest.raises(strategy.StrategyError, match="cannot load strategy"):
        registry.create(f"{path}:Missing", {})


def testCreate_whenTheClassHasNoPlanMethod_raisesStrategyError(tmp_path: pathlib.Path) -> None:
    path = _write_module(tmp_path, "mine")

    with pytest.raises(strategy.StrategyError, match="has no plan method"):
        registry.create(f"{path}:NoPlan", {})


def testCreate_whenTheModuleCannotBeImported_raisesStrategyError() -> None:
    with pytest.raises(strategy.StrategyError, match="cannot load strategy"):
        registry.create("no_such_module_for_split_dex:Mine", {})


def testCreate_whenTheSpecHasNoClass_raisesStrategyError() -> None:
    with pytest.raises(strategy.StrategyError, match="expected MODULE:CLASS or FILE.py:CLASS"):
        registry.create("some_module:", {})


def testCreate_whenTheSettingIsUnknown_raisesStrategyError() -> None:
    with pytest.raises(strategy.StrategyError, match="has no setting 'size'; its settings: cap"):
        registry.create("package-affinity", {"size": "3"})


def testCreate_whenTheValueIsNotANumber_raisesStrategyError() -> None:
    with pytest.raises(strategy.StrategyError, match="cap must be int, not 'many'"):
        registry.create("package-affinity", {"cap": "many"})


def testCreate_whenTheStrategyRejectsTheValue_raisesStrategyError() -> None:
    with pytest.raises(strategy.StrategyError, match="cap must be >= 1"):
        registry.create("package-affinity", {"cap": "0"})


def testDescribe_whenNoPluginIsInstalled_listsTheBuiltInStrategiesWithTheirSettings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_plugins(monkeypatch)

    lines = registry.describe()

    assert lines == [
        "package-affinity: Group classes by how they are linked, each package whole, about `cap` "
        "classes per file.",
        "    cap: int = 1000",
        "package-order: Put whole packages, sorted by name, into files of about `cap` classes; no "
        "code analysis.",
        "    cap: int = 1000",
        "original: Keep the classes in the DEX files they came from: one file per input DEX file.",
        "    max_files: int | None = None",
        "single: Put all the classes in one DEX file, cut only where it goes over a 64K limit.",
    ]


def testDescribe_whenAPluginIsInstalled_listsItAfterTheBuiltInStrategies(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(tmp_path, "split_dex_test_described_strategies")
    monkeypatch.syspath_prepend(str(tmp_path))
    _install_plugins(
        monkeypatch,
        importlib.metadata.EntryPoint(
            name="first-and-rest",
            value="split_dex_test_described_strategies:FirstAndRest",
            group=registry.PLUGIN_GROUP,
        ),
    )

    lines = registry.describe()

    assert lines[-2:] == [
        "first-and-rest: Put the first `count` classes in one group and the others in another.",
        "    count: int = 1",
    ]
