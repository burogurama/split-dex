"""Group classes by how they are linked, without splitting a Java package.

A class usually loads the classes it is linked to, so linked classes should land in the same DEX
file. A package is never split across two groups: when each DEX file is served by its own class
loader, two classes of one package in different loaders cannot use each other's package-private
members, and ART throws IllegalAccessError. Keeping each package whole keeps that access inside
one class loader.
"""

import collections.abc
import dataclasses
import heapq


class AffinityGraph:
    """An undirected, weighted graph of the links between classes.

    Each class is a dense id in `range(size)`. The weight of the edge between two classes counts
    the links between them. The graph is symmetric: an edge is stored on both of its ends.
    """

    def __init__(self, size: int) -> None:
        """Create a graph of `size` classes with no edges.

        Args:
            size: The number of classes.
        """
        self._neighbours: list[dict[int, int]] = [{} for _ in range(size)]

    @property
    def size(self) -> int:
        """The number of classes in the graph."""
        return len(self._neighbours)

    def neighbours(self, class_id: int) -> collections.abc.Mapping[int, int]:
        """The classes linked to a class.

        Args:
            class_id: The class.

        Returns:
            A mapping from each linked class to the weight of its edge.
        """
        return self._neighbours[class_id]

    def add_edge(self, first: int, second: int, weight: int = 1) -> None:
        """Add `weight` to the edge between two classes, on both of its ends.

        An edge from a class to itself is ignored.

        Args:
            first: One end of the edge.
            second: The other end of the edge.
            weight: How much to add to the weight of the edge.
        """
        if first == second:
            return
        self._neighbours[first][second] = self._neighbours[first].get(second, 0) + weight
        self._neighbours[second][first] = self._neighbours[second].get(first, 0) + weight


def package_of(descriptor: str) -> str:
    """The package of a class descriptor: the descriptor up to its last '/'.

    For example `Lcom/a/B;` gives `Lcom/a`. A class in the unnamed package, such as `LFoo;`,
    gives `L`.

    Args:
        descriptor: A class descriptor.

    Returns:
        The package, in descriptor form.
    """
    slash = descriptor.rfind("/")
    if slash == -1:
        return "L"
    return descriptor[:slash]


def partition_by_package_affinity(
    members: collections.abc.Sequence[int],
    graph: AffinityGraph,
    descriptors: collections.abc.Sequence[str],
    cap: int,
) -> list[list[int]]:
    """Partition classes into groups of about `cap` classes, keeping each package whole.

    The steps:

    1. Group the members by package. A package is the unit that moves; it is never split.
    2. Add up the edges between classes into edges between packages.
    3. Put the packages in order so that linked packages are next to each other. The order
       starts from the package linked to the most other packages. Each next package is the one
       with the heaviest edge to the packages already placed.
    4. Fill groups of about `cap` classes in that order, each package whole. A package of `cap`
       classes or more ends the group it is in, so a package bigger than `cap` gets a group of
       its own.

    Packages with no links come last and are packed together, so no group is left with a single
    stray class. The result is deterministic: ties are broken by package ids, which follow the
    order in which `members` first shows each package, never by hash order.

    Args:
        members: The ids of the classes to partition, a subset of the classes of `graph`.
        graph: The links between all classes. Only edges between two members are used.
        descriptors: The descriptor of every class, indexed by class id.
        cap: The target number of classes per group. A value below 1 counts as 1.

    Returns:
        The groups, each a list of class ids. No group is empty.
    """
    packages = _group_by_package(members, descriptors)
    edges = _package_edges(members, graph, packages)
    order = _order_packages(edges)
    return _fill_groups(order, packages.classes, max(cap, 1))


@dataclasses.dataclass(frozen=True)
class _Packages:
    """The packages of a set of classes. Package ids count from 0, in order of appearance.

    Attributes:
        classes: The classes of each package, indexed by package id.
        package_of_class: The package id of each class.
    """

    classes: list[list[int]]
    package_of_class: dict[int, int]


def _group_by_package(
    members: collections.abc.Sequence[int], descriptors: collections.abc.Sequence[str]
) -> _Packages:
    package_ids: dict[str, int] = {}
    classes: list[list[int]] = []
    package_of_class: dict[int, int] = {}
    for class_id in members:
        package_id = package_ids.setdefault(package_of(descriptors[class_id]), len(classes))
        if package_id == len(classes):
            classes.append([])
        classes[package_id].append(class_id)
        package_of_class[class_id] = package_id
    return _Packages(classes, package_of_class)


def _package_edges(
    members: collections.abc.Sequence[int], graph: AffinityGraph, packages: _Packages
) -> list[dict[int, int]]:
    """Add up the edges between classes of two different packages into package edges."""
    edges: list[dict[int, int]] = [{} for _ in packages.classes]
    for class_id in members:
        package = packages.package_of_class[class_id]
        for neighbour, weight in graph.neighbours(class_id).items():
            neighbour_package = packages.package_of_class.get(neighbour)
            if neighbour_package is None or neighbour_package == package:
                continue
            edges[package][neighbour_package] = edges[package].get(neighbour_package, 0) + weight
    return edges


def _order_packages(
    edges: collections.abc.Sequence[collections.abc.Mapping[int, int]],
) -> list[int]:
    """Order the packages so that linked packages are next to each other.

    Walks start from the packages with the most links (ties: the lower id first). Each walk is
    best-first, like Prim's algorithm: it takes the package with the heaviest edge to the
    packages already placed.
    """
    seeds = sorted(range(len(edges)), key=lambda package: (-len(edges[package]), package))
    placed = [False] * len(edges)
    order: list[int] = []
    for seed in seeds:
        if placed[seed] is False:
            _walk_from(seed, edges, placed, order)
    return order


def _walk_from(
    seed: int,
    edges: collections.abc.Sequence[collections.abc.Mapping[int, int]],
    placed: list[bool],
    order: list[int],
) -> None:
    """Place every package reachable from `seed`, heaviest edge first, and append it to `order`."""
    # A max-heap of (edge weight, package id), kept as a min-heap of the negated values: the
    # heaviest edge comes out first, and of equal weights the higher package id.
    heap = [(0, -seed)]
    while len(heap) > 0:
        _, negated_package = heapq.heappop(heap)
        package = -negated_package
        if placed[package] is True:
            continue
        placed[package] = True
        order.append(package)
        for neighbour, weight in edges[package].items():
            if placed[neighbour] is False:
                heapq.heappush(heap, (-weight, -neighbour))


def _fill_groups(
    order: collections.abc.Sequence[int],
    package_classes: collections.abc.Sequence[collections.abc.Sequence[int]],
    cap: int,
) -> list[list[int]]:
    """Fill groups of about `cap` classes with whole packages, in the given order."""
    groups: list[list[int]] = []
    current: list[int] = []
    for package in order:
        classes = package_classes[package]
        if len(current) > 0 and len(current) + len(classes) > cap:
            groups.append(current)
            current = []
        current.extend(classes)
        if len(current) >= cap:
            groups.append(current)
            current = []
    if len(current) > 0:
        groups.append(current)
    return groups
