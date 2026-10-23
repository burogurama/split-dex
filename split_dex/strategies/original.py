"""The `original` strategy: keep the classes in the DEX files they came from."""

import collections.abc
import heapq

from split_dex import strategy


class Original:
    """Keep the classes in the DEX files they came from: one file per input DEX file."""

    def __init__(self, *, max_files: int | None = None) -> None:
        """Create the strategy.

        Args:
            max_files: The most DEX files to write. When the classes come from more input DEX
                files, the smallest groups are merged. None keeps one file per input DEX file.

        Raises:
            ValueError: If `max_files` is below 1.
        """
        if max_files is not None and max_files < 1:
            raise ValueError("max_files must be >= 1")
        self._max_files = max_files

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        """Group the classes by the input DEX file they come from, in multidex order.

        Each class is a block of its own, so a group that goes over a 64K limit is cut in the
        middle.

        Args:
            classes: The classes to group.

        Returns:
            The groups.
        """
        by_source: dict[int, list[int]] = {}
        for class_id in classes.ids:
            by_source.setdefault(classes.source_dex(class_id), []).append(class_id)
        groups = [by_source[source] for source in sorted(by_source)]
        if self._max_files is not None and len(groups) > self._max_files:
            groups = _merge(groups, self._max_files)
        return [strategy.Group.of_classes(group) for group in groups]


def _merge(groups: collections.abc.Sequence[list[int]], count: int) -> list[list[int]]:
    """Merge groups into `count` bins: the biggest group first, each into the smallest bin.

    Ties keep the order of the groups and take the lowest bin.
    """
    bins: list[list[int]] = [[] for _ in range(count)]
    # A heap of (class count, bin index). In ascending order, the list is already a heap.
    sizes = [(0, index) for index in range(count)]
    for group in sorted(groups, key=len, reverse=True):
        size, index = heapq.heappop(sizes)
        bins[index].extend(group)
        heapq.heappush(sizes, (size + len(group), index))
    return bins
