"""Split the classes of an APK into grouped DEX files.

Business classes, the app's own code under a given package, are grouped by how they are linked,
in DEX files of about a chosen number of classes, and a Java package is never split across files
(see `split_dex.partition`). Framework classes, everything else, keep the app's own layout: one
DEX file for each original `classes*.dex`. Every class lands in exactly one DEX file.
"""

import collections.abc
import dataclasses
import enum
import heapq
import pathlib
import re
from typing import TextIO

import dexpp

import split_dex
from split_dex import partition

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


class SplitError(split_dex.Error):
    """Raised when an option is out of range, the APK cannot be read, or a file cannot be written."""


@dataclasses.dataclass(frozen=True)
class Options:
    """What to split and how.

    Attributes:
        apk_path: The input APK. Every top-level `classes*.dex` entry is read.
        package: The app's package, for example `com.myapp.`. A class is business when its
            descriptor is under this package (`Lcom/myapp/...;`) and framework otherwise
            (AndroidX, Kotlin, other libraries). It is read straight from the class names, so the
            app must not be obfuscated.
        out_dir: The output directory. It is created if it does not exist.
        business_cap: The target number of classes per business DEX file. A larger cap gives
            fewer, fuller files and fewer strings and type names repeated across files.
        framework_cap: The most framework DEX files. Framework classes get one DEX file per
            original `classes*.dex`; only when the APK has more than this are the smallest
            merged.
        hotlist: Binary class names, such as `com.myapp.Startup`. The whole package of each
            listed business class is moved to the framework tier, for example to keep hot
            startup code in the base DEX file. The package moves as a unit, so no package is
            split across the two tiers.
    """

    apk_path: pathlib.Path
    package: str
    out_dir: pathlib.Path
    business_cap: int = 1000
    framework_cap: int = 16
    hotlist: collections.abc.Sequence[str] = ()


@dataclasses.dataclass(frozen=True)
class Result:
    """What a split produced.

    Attributes:
        total_classes: The number of classes read, each class counted once.
        business_classes: The number of business classes.
        framework_classes: The number of framework classes.
        business_dex: The number of business DEX files written.
        framework_dex: The number of framework DEX files written.
        overflow_splits: How many groups were cut in two because they went over a 64K limit of
            the DEX format. A business group is cut at a package boundary.
        split_packages: The packages, such as `Lcom/myapp/big`, that were too big for one DEX
            file and had to be cut across files. Classes of such a package can no longer use
            package-private access between those files. Normally empty.
        duplicate_classes: How many later copies of classes defined in more than one DEX file
            were skipped. The first copy in multidex order is kept: the one Android loads.
        total_bytes: The total size of the written DEX files.
    """

    total_classes: int
    business_classes: int
    framework_classes: int
    business_dex: int
    framework_dex: int
    overflow_splits: int
    split_packages: list[str]
    duplicate_classes: int
    total_bytes: int


def split(options: Options) -> Result:
    """Split the classes of an APK into grouped DEX files under `options.out_dir`.

    The output directory receives:

    - `<k>.dex`: the grouped DEX files, `0.dex`, `1.dex`, ..., business files first. Each one is
      a valid, standalone DEX file.
    - `index.csv` (`class,file`): every class descriptor and the DEX file it is in.
    - `tiers.csv` (`file,tier`): whether each DEX file is `business` or `framework`.

    These files replace those of an earlier run. `<k>.dex` files left by an earlier, bigger run
    are removed, so every DEX file in the directory is listed in `index.csv`. Other files in the
    directory are not touched.

    Args:
        options: What to split and how.

    Returns:
        The counts of what was read and written.

    Raises:
        SplitError: If a cap is below 1, the APK cannot be read, or a file cannot be written.
    """
    _check_caps(options)
    apk = _open_apk(options.apk_path)
    classes = _read_classes(apk, options.apk_path)
    plan = _plan_groups(apk, classes, options)
    _create_directory(options.out_dir)
    writer = _write_groups(options.out_dir, classes, plan)
    _remove_stale_dex_files(options.out_dir, writer.file_count)
    return Result(
        total_classes=len(classes.descriptors),
        business_classes=plan.business_classes,
        framework_classes=len(classes.descriptors) - plan.business_classes,
        business_dex=writer.tier_file_count(_Tier.BUSINESS),
        framework_dex=writer.tier_file_count(_Tier.FRAMEWORK),
        overflow_splits=writer.overflow_splits,
        split_packages=writer.split_packages,
        duplicate_classes=classes.duplicates,
        total_bytes=writer.total_bytes,
    )


class _Tier(enum.StrEnum):
    BUSINESS = "business"
    FRAMEWORK = "framework"


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


@dataclasses.dataclass(frozen=True)
class _Plan:
    """The groups to write, each a list of class ids, in the order they are written.

    Attributes:
        business_groups: The business groups, from the package-safe partition.
        framework_groups: The framework groups, one per source DEX file unless merged.
        business_classes: The number of business classes.
    """

    business_groups: list[list[int]]
    framework_groups: list[list[int]]
    business_classes: int


def _check_caps(options: Options) -> None:
    if options.business_cap < 1:
        raise SplitError("business-cap must be >= 1")
    if options.framework_cap < 1:
        raise SplitError("framework-cap must be >= 1")


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


def _plan_groups(apk: dexpp.Apk, classes: _ApkClasses, options: Options) -> _Plan:
    """Sort the classes into the two tiers and group each tier."""
    is_business = _business_flags(classes, options)
    graph = _affinity_graph(apk, options.apk_path, classes, is_business)
    business = [class_id for class_id, flag in enumerate(is_business) if flag is True]
    return _Plan(
        business_groups=partition.partition_by_package_affinity(
            business, graph, classes.descriptors, options.business_cap
        ),
        framework_groups=_framework_groups(classes.sources, is_business, options.framework_cap),
        business_classes=len(business),
    )


def _business_flags(classes: _ApkClasses, options: Options) -> list[bool]:
    """Whether each class is business: under the app's package and not in a hot package."""
    prefix = "L" + options.package.replace(".", "/")
    hot_packages = _hot_packages(options.hotlist, classes.index_of, prefix)
    return [
        descriptor.startswith(prefix) and partition.package_of(descriptor) not in hot_packages
        for descriptor in classes.descriptors
    ]


def _hot_packages(
    hotlist: collections.abc.Iterable[str],
    index_of: collections.abc.Mapping[str, int],
    prefix: str,
) -> set[str]:
    """The packages that the hotlist moves to the framework tier.

    That is the package of every listed class that is itself business. The whole package moves,
    not just the listed class, so no package is split across the two tiers.
    """
    packages: set[str] = set()
    for binary_name in hotlist:
        descriptor = "L" + binary_name.replace(".", "/") + ";"
        if descriptor in index_of and descriptor.startswith(prefix):
            packages.add(partition.package_of(descriptor))
    return packages


def _affinity_graph(
    apk: dexpp.Apk,
    apk_path: pathlib.Path,
    classes: _ApkClasses,
    is_business: collections.abc.Sequence[bool],
) -> partition.AffinityGraph:
    """The links between business classes, counted per pair of classes.

    Links never cross the tier boundary, so framework classes do not pull business classes
    together. The analysis reuses the archive that `apk` has already loaded.
    """
    try:
        context = apk.analysis()
    except ValueError as error:
        raise SplitError(f"analysis failed on apk: {apk_path}") from error
    graph = partition.AffinityGraph(len(classes.descriptors))
    for cls in context.classes:
        class_id = classes.index_of.get(cls.name)
        if class_id is None or is_business[class_id] is False:
            continue
        for descriptor in _linked_descriptors(cls):
            other = classes.index_of.get(descriptor)
            if other is not None and is_business[other] is True:
                graph.add_edge(class_id, other)
    return graph


def _linked_descriptors(cls: dexpp.Class) -> collections.abc.Iterator[str]:
    """The class descriptors a class is linked to, once per link.

    Those are its superclass, the interfaces it implements, the top-level class it is nested in
    (`LFoo$Bar;` is linked to `LFoo;`), and every class that one of its methods refers to: a
    called method, an accessed field, or a used type (`new`, a cast, `instanceof`, ...).
    """
    superclass = cls.superclass
    if superclass is not None:
        yield superclass.descriptor
    for interface in cls.interfaces:
        yield interface.descriptor
    name = cls.name
    dollar = name.find("$")
    if dollar != -1:
        yield name[:dollar] + ";"
    for method in cls.methods:
        for call in method.calls:
            yield call.target.class_descriptor
        for access in method.field_accesses:
            yield access.field.class_descriptor
        for use in method.type_uses:
            yield use.descriptor


def _framework_groups(
    sources: collections.abc.Sequence[int], is_business: collections.abc.Sequence[bool], cap: int
) -> list[list[int]]:
    """The framework classes: one group per source DEX file, merged down to at most `cap` groups.

    Framework classes keep the app's layout, so the Android class loader loads them as before.
    Only an APK with more DEX files than `cap` has groups merged (rare: most apps have a handful
    of DEX files).
    """
    by_source: dict[int, list[int]] = {}
    for class_id, source in enumerate(sources):
        if is_business[class_id] is False:
            by_source.setdefault(source, []).append(class_id)
    groups = [by_source[source] for source in sorted(by_source)]
    if len(groups) <= cap:
        return groups
    return _merge_groups(groups, cap)


def _merge_groups(groups: collections.abc.Sequence[list[int]], cap: int) -> list[list[int]]:
    """Merge groups into `cap` bins: the biggest group first, each into the smallest bin.

    Ties keep the original order of the groups and take the lowest bin.
    """
    bins: list[list[int]] = [[] for _ in range(cap)]
    # A heap of (class count, bin index). In ascending order, the list is already a heap.
    sizes = [(0, index) for index in range(cap)]
    for group in sorted(groups, key=len, reverse=True):
        size, index = heapq.heappop(sizes)
        bins[index].extend(group)
        heapq.heappush(sizes, (size + len(group), index))
    return bins


def _create_directory(out_dir: pathlib.Path) -> None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SplitError(f"cannot create output directory: {out_dir}") from error


def _open_map(path: pathlib.Path, header: str) -> TextIO:
    """Open a CSV map for writing, and write its header line."""
    try:
        file = path.open("w", encoding="utf-8", newline="\n")
    except OSError as error:
        raise SplitError(f"cannot write maps in: {path.parent}") from error
    file.write(f"{header}\n")
    return file


class _DexWriter:
    """Writes groups of classes as numbered DEX files and lists them in the maps."""

    def __init__(
        self,
        out_dir: pathlib.Path,
        classes: _ApkClasses,
        index_file: TextIO,
        tiers_file: TextIO,
    ) -> None:
        self._out_dir = out_dir
        self._classes = classes
        self._index_file = index_file
        self._tiers_file = tiers_file
        self._tier_file_counts = {_Tier.BUSINESS: 0, _Tier.FRAMEWORK: 0}
        self._total_bytes = 0
        self._overflow_splits = 0
        self._split_packages: set[str] = set()

    @property
    def file_count(self) -> int:
        """The number of DEX files written."""
        return sum(self._tier_file_counts.values())

    @property
    def total_bytes(self) -> int:
        """The total size of the DEX files written."""
        return self._total_bytes

    @property
    def overflow_splits(self) -> int:
        """How many groups were cut in two because they went over a 64K limit."""
        return self._overflow_splits

    @property
    def split_packages(self) -> list[str]:
        """The packages that had to be cut across files, sorted."""
        return sorted(self._split_packages)

    def tier_file_count(self, tier: _Tier) -> int:
        """The number of DEX files written for one tier."""
        return self._tier_file_counts[tier]

    def write_group(self, members: list[int], tier: _Tier) -> None:
        """Write a group as one DEX file, or as several if it goes over a 64K limit.

        A group that goes over a limit is cut in two (see `_cut_point`), and each part is written
        again, the first part first.
        """
        pending = [members]
        while len(pending) > 0:
            group = pending.pop()
            data = self._encode(group)
            if data is None:
                self._overflow_splits += 1
                cut = self._cut_point(group, tier)
                pending.append(group[cut:])
                pending.append(group[:cut])
            else:
                self._write_file(group, tier, data)

    def _encode(self, group: list[int]) -> bytes | None:
        """The group as DEX bytes, or None if it goes over a 64K limit and can be cut."""
        model = dexpp.Model(self._classes.version)
        for class_id in group:
            source = self._classes.models[self._classes.sources[class_id]]
            model.copy_class(source, self._classes.descriptors[class_id])
        try:
            return model.write()
        except ValueError as error:
            if len(group) > 1 and _overflows(model.pool_stats) is True:
                return None
            raise SplitError(f"write failed: {error}") from error

    def _cut_point(self, group: list[int], tier: _Tier) -> int:
        """Where to cut a group that goes over a 64K limit.

        A framework group is cut in the middle. A business group holds whole packages one after
        another, so it is cut at the package boundary nearest its middle and no package is split.
        Only a group that is a single package is cut inside it, and that package is recorded.
        """
        half = len(group) // 2
        if tier is _Tier.FRAMEWORK:
            return half
        packages = [partition.package_of(self._classes.descriptors[class_id]) for class_id in group]
        boundaries = [i for i in range(1, len(group)) if packages[i] != packages[i - 1]]
        if len(boundaries) == 0:
            self._split_packages.add(packages[0])
            return half
        return min(boundaries, key=lambda boundary: abs(boundary - half))

    def _write_file(self, group: list[int], tier: _Tier, data: bytes) -> None:
        name = f"{self.file_count}.dex"
        path = self._out_dir / name
        try:
            path.write_bytes(data)
        except OSError as error:
            raise SplitError(f"cannot write DEX: {path}") from error
        for class_id in group:
            self._index_file.write(f"{self._classes.descriptors[class_id]},{name}\n")
        self._tiers_file.write(f"{name},{tier}\n")
        self._total_bytes += len(data)
        self._tier_file_counts[tier] += 1


def _overflows(stats: dexpp.PoolStats) -> bool:
    """Whether a pool has more entries than a DEX file can index."""
    return (
        stats.types > _MAX_TYPES
        or stats.protos > _MAX_PROTOS
        or max(stats.fields, stats.methods, stats.method_handles, stats.call_sites) > _MAX_MEMBERS
    )


def _write_groups(out_dir: pathlib.Path, classes: _ApkClasses, plan: _Plan) -> _DexWriter:
    """Write every group as DEX files, business groups first, and list them in the maps."""
    with (
        _open_map(out_dir / "index.csv", "class,file") as index_file,
        _open_map(out_dir / "tiers.csv", "file,tier") as tiers_file,
    ):
        writer = _DexWriter(out_dir, classes, index_file, tiers_file)
        for group in plan.business_groups:
            writer.write_group(group, _Tier.BUSINESS)
        for group in plan.framework_groups:
            writer.write_group(group, _Tier.FRAMEWORK)
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
