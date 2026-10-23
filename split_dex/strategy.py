"""The interface between the splitter and the strategies that group classes into DEX files.

The splitter sorts the classes of an APK into sets (see `split_dex.class_sets`). For each set, a
strategy decides how its classes are grouped: it returns groups, and each group becomes one DEX
file, or several when it goes over a 64K limit of the DEX format.

A strategy is a class whose keyword-only constructor parameters are its settings and whose
`plan` method returns the groups. Built-in strategies are registered in `split_dex.registry`; a
user's strategy is given on the command line as `FILE.py:CLASS` or `MODULE:CLASS`, or installed
as a plugin (see `split_dex.registry`).
"""

import collections.abc
import dataclasses
import enum
import itertools
import typing
from typing import Self

import dexpp

import split_dex
from split_dex import partition


class StrategyError(split_dex.Error):
    """Raised when a strategy cannot be created, or returns groups that break the rules."""


@enum.unique
class LinkKind(enum.Enum):
    """The kinds of links between two classes."""

    SUPERCLASS = "superclass"
    """One class is the superclass of the other."""
    INTERFACE = "interface"
    """One class implements the other, an interface."""
    NESTING = "nesting"
    """One class is nested in the other: `LFoo$Bar;` is linked to `LFoo;`."""
    CALL = "call"
    """A method of one class calls a method of the other."""
    FIELD = "field"
    """A method of one class reads or writes a field of the other."""
    TYPE = "type"
    """A method of one class uses the other as a type: `new`, a cast, `instanceof`, ..."""


@dataclasses.dataclass(frozen=True)
class Block:
    """Classes that should stay in the same DEX file.

    When a group goes over a 64K limit, the splitter cuts it at the block boundary nearest its
    middle. Only a group of a single block is cut inside that block.

    Attributes:
        class_ids: The classes, in write order.
        name: How the warning names the block when it has to be cut, such as
            `package Lcom/a`. None for a block that can be cut without a warning.
    """

    class_ids: tuple[int, ...]
    name: str | None = None


@dataclasses.dataclass(frozen=True)
class Group:
    """The classes of one DEX file, in write order, split into blocks.

    Attributes:
        blocks: The blocks, in write order.
    """

    blocks: tuple[Block, ...]

    @classmethod
    def of_classes(cls, class_ids: collections.abc.Iterable[int]) -> Self:
        """A group with one block per class, so that a cut can fall anywhere.

        Args:
            class_ids: The classes, in write order.

        Returns:
            The group.
        """
        return cls(tuple(Block((class_id,)) for class_id in class_ids))

    @classmethod
    def of_packages(
        cls, class_ids: collections.abc.Iterable[int], descriptors: collections.abc.Sequence[str]
    ) -> Self:
        """A group with one block per package, named after it.

        A group that goes over a 64K limit is then cut at a package boundary, and a package that
        has to be cut is named in the warning.

        Args:
            class_ids: The classes, in write order. The classes of a package should be next to
                each other: each run of one package's classes becomes a block.
            descriptors: The descriptor of every class, by class id.

        Returns:
            The group.
        """
        runs = itertools.groupby(
            class_ids, key=lambda class_id: partition.package_of(descriptors[class_id])
        )
        return cls(tuple(Block(tuple(ids), f"package {package}") for package, ids in runs))

    @property
    def class_ids(self) -> list[int]:
        """Every class of the group, in write order."""
        return [class_id for block in self.blocks for class_id in block.class_ids]


class App(typing.Protocol):
    """What the splitter knows about the APK. Every set shares it."""

    @property
    def descriptors(self) -> collections.abc.Sequence[str]:
        """The descriptor of every class of the APK, by class id."""
        ...

    def source_dex(self, class_id: int) -> int:
        """The input DEX file a class comes from: 0 for `classes.dex`, 1 for `classes2.dex`, ..."""
        ...

    def links(
        self,
        class_ids: collections.abc.Sequence[int],
        weights: collections.abc.Mapping[LinkKind, int],
    ) -> partition.AffinityGraph:
        """The links among the given classes, each link counted with the weight of its kind."""
        ...

    @property
    def apk(self) -> dexpp.Apk:
        """The APK."""
        ...

    @property
    def analysis(self) -> dexpp.AnalysisContext:
        """The analysis of the APK's code."""
        ...


class Classes:
    """A set of classes to group, and what a strategy can know about them.

    Class ids count every class of the APK, in multidex order, so they index `descriptors`
    directly. A class defined in more than one DEX file is counted once, for its first copy.
    """

    def __init__(self, ids: collections.abc.Sequence[int], app: App) -> None:
        """Create the view of a set of classes.

        Args:
            ids: The classes of the set, in multidex order.
            app: What the splitter knows about the APK.
        """
        self._ids = ids
        self._app = app

    @property
    def ids(self) -> collections.abc.Sequence[int]:
        """The classes of the set, in multidex order."""
        return self._ids

    @property
    def descriptors(self) -> collections.abc.Sequence[str]:
        """The descriptor of every class of the APK, by class id."""
        return self._app.descriptors

    def source_dex(self, class_id: int) -> int:
        """The input DEX file a class comes from: 0 for `classes.dex`, 1 for `classes2.dex`, ...

        Args:
            class_id: The class.

        Returns:
            The position of its DEX file in multidex order.
        """
        return self._app.source_dex(class_id)

    def links(
        self, weights: collections.abc.Mapping[LinkKind, int] | None = None
    ) -> partition.AffinityGraph:
        """The links among the classes of the set, counted per pair of classes.

        The graph is built on each call, from the code of the classes of the set.

        Args:
            weights: The weight of each kind of link. A kind left out weighs 1, and a kind that
                weighs 0 is ignored. None weighs every link 1.

        Returns:
            The graph, over class ids. The weight of an edge is the sum of the weights of the
            links between its two classes, in both directions.
        """
        merged = dict.fromkeys(LinkKind, 1)
        if weights is not None:
            merged.update(weights)
        return self._app.links(self._ids, merged)

    @property
    def apk(self) -> dexpp.Apk:
        """The APK, for its manifest, resources or entries."""
        return self._app.apk

    @property
    def analysis(self) -> dexpp.AnalysisContext:
        """The analysis of the APK's code: classes, methods, call graph, class hierarchy, xrefs.

        It covers every class of the APK, not only the set. It is made on first use and shared by
        every set.
        """
        return self._app.analysis


@typing.runtime_checkable
class Strategy(typing.Protocol):
    """How the classes of a set are grouped into DEX files."""

    def plan(self, classes: Classes) -> collections.abc.Sequence[Group]:
        """Group the classes of a set.

        Args:
            classes: The classes to group.

        Returns:
            The groups, in the order their DEX files are written. Every class of the set must be
            in exactly one group, and no group or block may be empty.
        """
        ...
