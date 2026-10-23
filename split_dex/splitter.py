"""Split the classes of an APK into grouped DEX files.

The classes are sorted into named sets (see `split_dex.class_sets`), and the strategy of each set
groups its classes (see `split_dex.strategy`). Each group is written as one DEX file, or as
several when it goes over a 64K limit of the DEX format. Every class lands in exactly one DEX
file, and two sets never share a file.
"""

import collections.abc
import dataclasses
import pathlib
import re
from typing import TextIO

import dexpp

import split_dex
from split_dex import class_sets
from split_dex import partition
from split_dex import strategy
from split_dex.strategies import package_affinity

# Older DEX files are written as 035, the lowest DEX version.
_MIN_DEX_VERSION = 35

# A top-level DEX entry of an APK: classes.dex, then classes2.dex, classes3.dex, ...  The number
# is read the way dexpp's analysis reads it: decimal digits that fit in 32 bits.
_DEX_ENTRY = re.compile(r"classes([0-9]*)\.dex")
_MAX_DEX_ENTRY_NUMBER = 0xFFFFFFFF

# A DEX file name that this tool writes: 0.dex, 1.dex, ... but never 07.dex.
_WRITTEN_DEX_FILE = re.compile(r"(0|[1-9][0-9]*)\.dex")

# The most entries a DEX file can index in each pool (see dexpp.PoolStats).
_MAX_TYPES = 65535
_MAX_PROTOS = 65535
_MAX_MEMBERS = 65536  # fields, methods, method handles and call sites

# How many classes an error message about a strategy's groups names.
_EXAMPLES = 3


class SplitError(split_dex.Error):
    """Raised when the APK cannot be read or a file cannot be written."""


def default_sets() -> tuple[class_sets.ClassSet, ...]:
    """The sets used when none are given: one set, `all`, grouped by `package-affinity`.

    Returns:
        The sets.
    """
    return (class_sets.ClassSet("all", package_affinity.PackageAffinity()),)


@dataclasses.dataclass(frozen=True)
class Options:
    """What to split and how.

    Attributes:
        apk_path: The input APK. Every top-level `classes*.dex` entry is read.
        out_dir: The output directory. It is created if it does not exist.
        sets: The sets to sort the classes into. Their DEX files are written in this order.
        assignments: Which classes go to which set, tried in order (see
            `split_dex.class_sets.assign`). It may be empty when there is a single set.
    """

    apk_path: pathlib.Path
    out_dir: pathlib.Path
    sets: collections.abc.Sequence[class_sets.ClassSet] = dataclasses.field(
        default_factory=default_sets
    )
    assignments: collections.abc.Sequence[class_sets.Assignment] = ()


@dataclasses.dataclass(frozen=True)
class SetResult:
    """What a split produced for one set.

    Attributes:
        name: The set's name.
        classes: The number of classes in the set.
        dex_files: The number of DEX files written for the set.
    """

    name: str
    classes: int
    dex_files: int


@dataclasses.dataclass(frozen=True)
class Result:
    """What a split produced.

    Attributes:
        total_classes: The number of classes read, each class counted once.
        duplicate_classes: How many later copies of classes defined in more than one DEX file
            were skipped. The first copy in multidex order is kept: the one Android loads.
        dex_files: The number of DEX files written.
        overflow_splits: How many groups were cut in two because they went over a 64K limit of
            the DEX format.
        split_blocks: The names of the blocks, such as `package Lcom/myapp/big`, that were too
            big for one DEX file and had to be cut across files. Normally empty.
        total_bytes: The total size of the written DEX files.
        sets: What was produced for each set, in the order of the sets.
    """

    total_classes: int
    duplicate_classes: int
    dex_files: int
    overflow_splits: int
    split_blocks: list[str]
    total_bytes: int
    sets: list[SetResult]


def split(options: Options) -> Result:
    """Split the classes of an APK into grouped DEX files under `options.out_dir`.

    The output directory receives:

    - `<k>.dex`: the grouped DEX files, `0.dex`, `1.dex`, ..., set by set. Each one is a valid,
      standalone DEX file.
    - `index.csv` (`class,file`): every class descriptor and the DEX file it is in.
    - `sets.csv` (`file,set`): the set of each DEX file.

    These files replace those of an earlier run. `<k>.dex` files left by an earlier, bigger run
    are removed, so every DEX file in the directory is listed in `index.csv`. Other files in the
    directory are not touched.

    Args:
        options: What to split and how.

    Returns:
        The counts of what was read and written.

    Raises:
        SplitError: If the APK cannot be read or a file cannot be written.
        split_dex.class_sets.AssignmentError: If the classes cannot be sorted into the sets.
        split_dex.strategy.StrategyError: If a strategy returns groups that break the rules.
    """
    apk = _open_apk(options.apk_path)
    classes = _read_classes(apk, options.apk_path)
    members = class_sets.assign(classes.descriptors, options.sets, options.assignments)
    app = _App(apk, options.apk_path, classes)
    plans = [_plan(class_set, members[class_set.name], app) for class_set in options.sets]
    _create_directory(options.out_dir)
    writer = _write_plans(options.out_dir, classes, options.sets, plans)
    _remove_stale_dex_files(options.out_dir, writer.file_count)
    return Result(
        total_classes=len(classes.descriptors),
        duplicate_classes=classes.duplicates,
        dex_files=writer.file_count,
        overflow_splits=writer.overflow_splits,
        split_blocks=writer.split_blocks,
        total_bytes=writer.total_bytes,
        sets=[
            SetResult(class_set.name, len(members[class_set.name]), writer.files_of(class_set.name))
            for class_set in options.sets
        ],
    )


@dataclasses.dataclass(frozen=True)
class _ApkClasses:
    """The classes of an APK's DEX files. Class ids count from 0, in multidex order.

    Attributes:
        models: One model per `classes*.dex` entry, in multidex order.
        descriptors: The descriptor of each class, indexed by class id.
        sources: The index in `models` of the DEX file each class comes from.
        index_of: The class id of each descriptor.
        duplicates: How many later copies of classes were skipped.
        version: The DEX version to write: the highest version read, and at least 035.
    """

    models: list[dexpp.Model]
    descriptors: list[str]
    sources: list[int]
    index_of: dict[str, int]
    duplicates: int
    version: int


def _open_apk(apk_path: pathlib.Path) -> dexpp.Apk:
    if apk_path.exists() is False:
        raise SplitError(f"apk not found: {apk_path}")
    try:
        return dexpp.Apk.open(str(apk_path))
    except (FileNotFoundError, ValueError) as error:
        raise SplitError(f"cannot open apk: {apk_path}") from error


def _read_classes(apk: dexpp.Apk, apk_path: pathlib.Path) -> _ApkClasses:
    """Read the classes of every `classes*.dex` entry, in multidex order.

    A class defined in more than one DEX file is kept once: the first copy in multidex order,
    which is the one Android loads. Keeping both would put one class in two output files.
    """
    entries = _dex_entries(apk.entries)
    if len(entries) == 0:
        raise SplitError(f"apk has no classes*.dex: {apk_path}")
    models: list[dexpp.Model] = []
    descriptors: list[str] = []
    sources: list[int] = []
    index_of: dict[str, int] = {}
    duplicates = 0
    for source, entry in enumerate(entries):
        model = _read_model(apk, entry)
        models.append(model)
        for descriptor in model.class_descriptors:
            if descriptor in index_of:
                duplicates += 1
                continue
            index_of[descriptor] = len(descriptors)
            descriptors.append(descriptor)
            sources.append(source)
    version = max(_MIN_DEX_VERSION, *(model.version for model in models))
    return _ApkClasses(models, descriptors, sources, index_of, duplicates, version)


def _dex_entries(names: collections.abc.Iterable[str]) -> list[str]:
    """The top-level `classes*.dex` entry names, in multidex order.

    A name listed twice in the archive is read once, as dexpp's analysis does. Ties on the number
    (`classes2.dex` and `classes02.dex`) are broken by name.
    """
    numbers: dict[str, int] = {}
    for name in names:
        number = _multidex_number(name)
        if number is not None:
            numbers.setdefault(name, number)
    return sorted(numbers, key=lambda name: (numbers[name], name))


def _multidex_number(name: str) -> int | None:
    """The multidex order of a DEX entry: 1 for classes.dex, N for classesN.dex (N >= 2)."""
    match = _DEX_ENTRY.fullmatch(name)
    if match is None:
        return None
    digits = match.group(1)
    if digits == "":
        return 1
    number = int(digits)
    if number < 2 or number > _MAX_DEX_ENTRY_NUMBER:
        return None
    return number


def _read_model(apk: dexpp.Apk, entry: str) -> dexpp.Model:
    data = apk.read(entry)
    if data is None:
        raise SplitError(f"cannot read apk entry: {entry}")
    try:
        return dexpp.Model.read(data)
    except ValueError as error:
        raise SplitError(f"{entry}: {error}") from error


class _App:
    """What the splitter knows about the APK, shared by every set (see `strategy.App`).

    The analysis of the APK's code is made on first use, by the first set that needs it.
    """

    def __init__(self, apk: dexpp.Apk, apk_path: pathlib.Path, classes: _ApkClasses) -> None:
        self._apk = apk
        self._apk_path = apk_path
        self._classes = classes
        self._context: dexpp.AnalysisContext | None = None

    @property
    def descriptors(self) -> collections.abc.Sequence[str]:
        """The descriptor of every class of the APK, by class id."""
        return self._classes.descriptors

    def source_dex(self, class_id: int) -> int:
        """The input DEX file a class comes from: 0 for `classes.dex`, 1 for `classes2.dex`, ..."""
        return self._classes.sources[class_id]

    def links(
        self,
        class_ids: collections.abc.Sequence[int],
        weights: collections.abc.Mapping[strategy.LinkKind, int],
    ) -> partition.AffinityGraph:
        """The links among the given classes, each link counted with the weight of its kind.

        Only the code of the given classes is read, so the cost follows the size of the set.
        """
        members = set(class_ids)
        index_of = self._classes.index_of
        graph = partition.AffinityGraph(len(self._classes.descriptors))
        for cls in self.analysis.classes:
            class_id = index_of.get(cls.name)
            if class_id is None or class_id not in members:
                continue
            for descriptor, weight in _weighted_links(cls, weights):
                other = index_of.get(descriptor)
                if other is not None and other in members:
                    graph.add_edge(class_id, other, weight)
        return graph

    @property
    def apk(self) -> dexpp.Apk:
        """The APK."""
        return self._apk

    @property
    def analysis(self) -> dexpp.AnalysisContext:
        """The analysis of the APK's code."""
        if self._context is None:
            try:
                self._context = self._apk.analysis()
            except ValueError as error:
                raise SplitError(f"analysis failed on apk: {self._apk_path}") from error
        return self._context


def _weighted_links(
    cls: dexpp.Class, weights: collections.abc.Mapping[strategy.LinkKind, int]
) -> collections.abc.Iterator[tuple[str, int]]:
    """The class descriptors a class is linked to, once per link, with the weight of the link.

    Those are its superclass, the interfaces it implements, the top-level class it is nested in
    (`LFoo$Bar;` is linked to `LFoo;`), and every class that one of its methods refers to: a
    called method, an accessed field, or a used type (`new`, a cast, `instanceof`, ...). Links of
    a kind that weighs 0 are skipped without reading them.
    """
    superclass = cls.superclass
    if superclass is not None and weights[strategy.LinkKind.SUPERCLASS] != 0:
        yield superclass.descriptor, weights[strategy.LinkKind.SUPERCLASS]
    if weights[strategy.LinkKind.INTERFACE] != 0:
        for interface in cls.interfaces:
            yield interface.descriptor, weights[strategy.LinkKind.INTERFACE]
    name = cls.name
    dollar = name.find("$")
    if dollar != -1 and weights[strategy.LinkKind.NESTING] != 0:
        yield name[:dollar] + ";", weights[strategy.LinkKind.NESTING]
    yield from _code_links(cls, weights)


def _code_links(
    cls: dexpp.Class, weights: collections.abc.Mapping[strategy.LinkKind, int]
) -> collections.abc.Iterator[tuple[str, int]]:
    """The classes the methods of a class refer to, with the weight of each reference's kind."""
    call_weight = weights[strategy.LinkKind.CALL]
    field_weight = weights[strategy.LinkKind.FIELD]
    type_weight = weights[strategy.LinkKind.TYPE]
    if call_weight == 0 and field_weight == 0 and type_weight == 0:
        return
    for method in cls.methods:
        if call_weight != 0:
            for call in method.calls:
                yield call.target.class_descriptor, call_weight
        if field_weight != 0:
            for access in method.field_accesses:
                yield access.field.class_descriptor, field_weight
        if type_weight != 0:
            for use in method.type_uses:
                yield use.descriptor, type_weight


def _plan(
    class_set: class_sets.ClassSet, class_ids: collections.abc.Sequence[int], app: _App
) -> list[strategy.Group]:
    """Ask the set's strategy for its groups, and check them."""
    groups = list(class_set.strategy.plan(strategy.Classes(class_ids, app)))
    _check_groups(class_set.name, groups, class_ids, app.descriptors)
    return groups


def _check_groups(
    set_name: str,
    groups: collections.abc.Sequence[strategy.Group],
    class_ids: collections.abc.Sequence[int],
    descriptors: collections.abc.Sequence[str],
) -> None:
    """Check that a strategy put every class of its set in exactly one non-empty block."""
    expected = set(class_ids)
    seen: set[int] = set()
    for group in groups:
        if len(group.blocks) == 0:
            raise strategy.StrategyError(f"set {set_name}: the strategy returned an empty group")
        for block in group.blocks:
            if len(block.class_ids) == 0:
                raise strategy.StrategyError(
                    f"set {set_name}: the strategy returned an empty block"
                )
            for class_id in block.class_ids:
                if class_id not in expected:
                    raise strategy.StrategyError(
                        f"set {set_name}: the strategy returned class id {class_id}, which is "
                        "not in the set"
                    )
                if class_id in seen:
                    raise strategy.StrategyError(
                        f"set {set_name}: the strategy returned {descriptors[class_id]} twice"
                    )
                seen.add(class_id)
    if len(seen) < len(expected):
        missing = [descriptors[class_id] for class_id in class_ids if class_id not in seen]
        raise strategy.StrategyError(
            f"set {set_name}: the strategy left out {len(missing)} classes, such as "
            f"{', '.join(missing[:_EXAMPLES])}"
        )


def _create_directory(out_dir: pathlib.Path) -> None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SplitError(f"cannot create output directory: {out_dir}") from error


def _open_csv(path: pathlib.Path, header: str) -> TextIO:
    """Open a CSV map for writing, and write its header line."""
    try:
        file = path.open("w", encoding="utf-8", newline="\n")
    except OSError as error:
        raise SplitError(f"cannot write {path}") from error
    file.write(f"{header}\n")
    return file


class _DexWriter:
    """Writes groups of classes as numbered DEX files and lists them in the maps."""

    def __init__(
        self,
        out_dir: pathlib.Path,
        classes: _ApkClasses,
        index_file: TextIO,
        sets_file: TextIO,
    ) -> None:
        self._out_dir = out_dir
        self._classes = classes
        self._index_file = index_file
        self._sets_file = sets_file
        self._set_file_counts: dict[str, int] = {}
        self._total_bytes = 0
        self._overflow_splits = 0
        self._split_blocks: set[str] = set()

    @property
    def file_count(self) -> int:
        """The number of DEX files written."""
        return sum(self._set_file_counts.values())

    @property
    def total_bytes(self) -> int:
        """The total size of the DEX files written."""
        return self._total_bytes

    @property
    def overflow_splits(self) -> int:
        """How many groups were cut in two because they went over a 64K limit."""
        return self._overflow_splits

    @property
    def split_blocks(self) -> list[str]:
        """The names of the blocks that had to be cut across files, sorted."""
        return sorted(self._split_blocks)

    def files_of(self, set_name: str) -> int:
        """The number of DEX files written for a set."""
        return self._set_file_counts.get(set_name, 0)

    def write_group(self, group: strategy.Group, set_name: str) -> None:
        """Write a group as one DEX file, or as several if it goes over a 64K limit.

        A group that goes over a limit is cut in two (see `_cut`), and each part is written
        again, the first part first.
        """
        pending = [list(group.blocks)]
        while len(pending) > 0:
            blocks = pending.pop()
            class_ids = [class_id for block in blocks for class_id in block.class_ids]
            data = self._encode(class_ids)
            if data is None:
                self._overflow_splits += 1
                first, second = self._cut(blocks)
                pending.append(second)
                pending.append(first)
            else:
                self._write_file(class_ids, set_name, data)

    def _encode(self, class_ids: list[int]) -> bytes | None:
        """The classes as DEX bytes, or None if they go over a 64K limit and can be cut."""
        model = dexpp.Model(self._classes.version)
        for class_id in class_ids:
            source = self._classes.models[self._classes.sources[class_id]]
            model.copy_class(source, self._classes.descriptors[class_id])
        try:
            return model.write()
        except ValueError as error:
            if len(class_ids) > 1 and _overflows(model.pool_stats) is True:
                return None
            raise SplitError(f"write failed: {error}") from error

    def _cut(
        self, blocks: list[strategy.Block]
    ) -> tuple[list[strategy.Block], list[strategy.Block]]:
        """Cut blocks that go over a 64K limit in two.

        The cut falls at the block boundary nearest the middle (the first one on a tie). A
        single block is cut in its middle, and its name is recorded.
        """
        half = sum(len(block.class_ids) for block in blocks) // 2
        if len(blocks) == 1:
            block = blocks[0]
            if block.name is not None:
                self._split_blocks.add(block.name)
            return (
                [strategy.Block(block.class_ids[:half], block.name)],
                [strategy.Block(block.class_ids[half:], block.name)],
            )
        boundaries: list[int] = []
        position = 0
        for block in blocks[:-1]:
            position += len(block.class_ids)
            boundaries.append(position)
        cut = min(range(len(boundaries)), key=lambda index: abs(boundaries[index] - half))
        return blocks[: cut + 1], blocks[cut + 1 :]

    def _write_file(self, class_ids: list[int], set_name: str, data: bytes) -> None:
        name = f"{self.file_count}.dex"
        path = self._out_dir / name
        try:
            path.write_bytes(data)
        except OSError as error:
            raise SplitError(f"cannot write DEX: {path}") from error
        for class_id in class_ids:
            self._index_file.write(f"{self._classes.descriptors[class_id]},{name}\n")
        self._sets_file.write(f"{name},{set_name}\n")
        self._total_bytes += len(data)
        self._set_file_counts[set_name] = self.files_of(set_name) + 1


def _overflows(stats: dexpp.PoolStats) -> bool:
    """Whether a pool has more entries than a DEX file can index."""
    return (
        stats.types > _MAX_TYPES
        or stats.protos > _MAX_PROTOS
        or max(stats.fields, stats.methods, stats.method_handles, stats.call_sites) > _MAX_MEMBERS
    )


def _write_plans(
    out_dir: pathlib.Path,
    classes: _ApkClasses,
    sets: collections.abc.Sequence[class_sets.ClassSet],
    plans: collections.abc.Sequence[collections.abc.Sequence[strategy.Group]],
) -> _DexWriter:
    """Write the groups of every set as DEX files, set by set, and list them in the maps."""
    with (
        _open_csv(out_dir / "index.csv", "class,file") as index_file,
        _open_csv(out_dir / "sets.csv", "file,set") as sets_file,
    ):
        writer = _DexWriter(out_dir, classes, index_file, sets_file)
        for class_set, groups in zip(sets, plans, strict=True):
            for group in groups:
                writer.write_group(group, class_set.name)
    return writer


def _remove_stale_dex_files(out_dir: pathlib.Path, file_count: int) -> None:
    """Remove the `<k>.dex` files past the last one written, left by an earlier, bigger run.

    Afterwards every DEX file in the directory is listed in `index.csv`. Only names this tool
    writes are touched.
    """
    stale = [path for path in out_dir.iterdir() if _is_stale_dex_file(path, file_count) is True]
    for path in stale:
        try:
            path.unlink()
        except OSError as error:
            raise SplitError(f"cannot remove stale DEX: {path}") from error


def _is_stale_dex_file(path: pathlib.Path, file_count: int) -> bool:
    match = _WRITTEN_DEX_FILE.fullmatch(path.name)
    if match is None or path.is_dir() is True:
        return False
    return int(match.group(1)) >= file_count
