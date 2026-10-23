"""Find and create strategies: built-in, from installed plugins, or from a user's file or module.

A strategy is named by one of:

- the name of a built-in strategy, listed in `STRATEGIES`;
- the name of a plugin: an installed package that declares the strategy as an entry point in
  the group `split_dex.strategies`;
- `MODULE:CLASS`, for a class in a module that Python can import;
- `FILE.py:CLASS`, for a class in a Python file.

To add a built-in strategy, write its module in `split_dex/strategies/` and add its class to
`STRATEGIES`. The keyword-only parameters of a strategy's constructor are its settings: each
`KEY=VALUE` of the command line is converted to the parameter's type (int, float, bool, str or
Path, optionally `| None`).

A strategy from a plugin, module or file is ordinary Python code that runs inside split-dex, so it
must be trusted.
"""

import collections.abc
import hashlib
import importlib.metadata
import importlib.util
import inspect
import pathlib
import sys
import types
import typing
from typing import Any

from split_dex import strategy
from split_dex.strategies import original
from split_dex.strategies import package_affinity
from split_dex.strategies import package_order
from split_dex.strategies import single

STRATEGIES: collections.abc.Mapping[str, collections.abc.Callable[..., strategy.Strategy]] = {
    "package-affinity": package_affinity.PackageAffinity,
    "package-order": package_order.PackageOrder,
    "original": original.Original,
    "single": single.Single,
}

PLUGIN_GROUP = "split_dex.strategies"

_SCALAR_TYPES = (int, float, str, pathlib.Path)
_TRUE = ("true", "yes", "1")
_FALSE = ("false", "no", "0")


def create(spec: str, settings: collections.abc.Mapping[str, str]) -> strategy.Strategy:
    """Create a strategy from its name and settings given as text.

    Args:
        spec: The strategy: a built-in or plugin name such as `package-affinity`,
            `MODULE:CLASS`, or `FILE.py:CLASS`.
        settings: The value of each setting, as text, by parameter name. A dash in a name counts
            as an underscore, so `max-files` sets `max_files`.

    Returns:
        The strategy.

    Raises:
        StrategyError: If the strategy cannot be found or loaded, a setting does not exist, a
            value is invalid, or the created object has no `plan` method.
    """
    factory = _find(spec)
    parameters = inspect.signature(factory).parameters
    arguments: dict[str, Any] = {}
    for key, text in settings.items():
        parameter = parameters.get(key.replace("-", "_"))
        if parameter is None:
            known = ", ".join(parameters) if len(parameters) > 0 else "none"
            raise strategy.StrategyError(
                f"strategy {spec} has no setting {key!r}; its settings: {known}"
            )
        arguments[parameter.name] = _convert(spec, parameter, text)
    try:
        created = factory(**arguments)
    except ValueError as error:
        raise strategy.StrategyError(f"strategy {spec}: {error}") from error
    if isinstance(created, strategy.Strategy) is False:
        raise strategy.StrategyError(f"strategy {spec} has no plan method")
    return created


def describe() -> list[str]:
    """Describe every built-in and plugin strategy: its name, what it does, and its settings.

    Returns:
        The lines to print, one per strategy and one per setting.

    Raises:
        StrategyError: If a plugin cannot be loaded.
    """
    lines: list[str] = []
    for name, factory in STRATEGIES.items():
        lines.extend(_describe(name, factory))
    for name, entry_point in sorted(_plugins().items()):
        source = "" if entry_point.dist is None else f" (plugin from {entry_point.dist.name})"
        lines.extend(_describe(f"{name}{source}", _load(entry_point, name)))
    return lines


def _find(spec: str) -> collections.abc.Callable[..., strategy.Strategy]:
    built_in = STRATEGIES.get(spec)
    if built_in is not None:
        return built_in
    location, separator, attribute = spec.rpartition(":")
    if separator == "":
        plugin = _plugins().get(spec)
        if plugin is None:
            known = ", ".join([*STRATEGIES, *sorted(_plugins())])
            raise strategy.StrategyError(
                f"unknown strategy {spec!r}; known: {known}; or give MODULE:CLASS or FILE.py:CLASS"
            )
        return _load(plugin, spec)
    if location == "" or attribute == "":
        raise strategy.StrategyError(f"strategy {spec}: expected MODULE:CLASS or FILE.py:CLASS")
    if location.endswith(".py"):
        location = _import_file(pathlib.Path(location))
    entry_point = importlib.metadata.EntryPoint(
        name=spec, value=f"{location}:{attribute}", group=PLUGIN_GROUP
    )
    return _load(entry_point, spec)


def _plugins() -> dict[str, importlib.metadata.EntryPoint]:
    """The strategies of installed plugins, by name. A built-in strategy hides a plugin's."""
    return {
        entry_point.name: entry_point
        for entry_point in importlib.metadata.entry_points(group=PLUGIN_GROUP)
        if entry_point.name not in STRATEGIES
    }


def _load(
    entry_point: importlib.metadata.EntryPoint, spec: str
) -> collections.abc.Callable[..., strategy.Strategy]:
    try:
        loaded = entry_point.load()
    except (ImportError, AttributeError) as error:
        raise strategy.StrategyError(f"cannot load strategy {spec}: {error}") from error
    if callable(loaded) is False:
        raise strategy.StrategyError(f"strategy {spec} is not a class")
    factory: collections.abc.Callable[..., strategy.Strategy] = loaded
    return factory


def _import_file(path: pathlib.Path) -> str:
    """Import a Python file as a module, once, and return the module's name."""
    if path.is_file() is False:
        raise strategy.StrategyError(f"strategy file not found: {path}")
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    module_name = f"split_dex_user_strategy_{digest}"
    if module_name in sys.modules:
        return module_name
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise strategy.StrategyError(f"cannot load strategy file {path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as error:
        del sys.modules[module_name]
        raise strategy.StrategyError(f"cannot load strategy file {path}: {error}") from error
    return module_name


def _describe(title: str, factory: collections.abc.Callable[..., strategy.Strategy]) -> list[str]:
    summary = (inspect.getdoc(factory) or "").split("\n", 1)[0]
    lines = [f"{title}: {summary}"]
    for parameter in inspect.signature(factory).parameters.values():
        lines.append(
            f"    {parameter.name}: {_type_name(parameter.annotation)} = {parameter.default!r}"
        )
    return lines


def _convert(spec: str, parameter: inspect.Parameter, text: str) -> object:
    target = _value_type(parameter.annotation)
    if target is bool:
        if text.lower() in _TRUE:
            return True
        if text.lower() in _FALSE:
            return False
    elif target in _SCALAR_TYPES:
        try:
            return target(text)
        except ValueError:
            pass
    else:
        raise strategy.StrategyError(
            f"strategy {spec}: setting {parameter.name} cannot be given on the command line"
        )
    raise strategy.StrategyError(
        f"strategy {spec}: {parameter.name} must be {_type_name(target)}, not {text!r}"
    )


def _value_type(annotation: Any) -> Any:
    """The type a value converts to: `X` for both `X` and `X | None`."""
    if isinstance(annotation, types.UnionType):
        members = [member for member in typing.get_args(annotation) if member is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _type_name(annotation: Any) -> str:
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)
