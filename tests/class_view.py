"""Build `strategy.Classes` views for strategy tests, without an APK."""

import collections.abc

import dexpp

from split_dex import partition
from split_dex import strategy


class FakeApp:
    """An `strategy.App` with no APK behind it: the test gives the classes and their links.

    Each given edge is one link, counted with weight 1 whatever its kind. `asked_weights` records
    the weights of every `links` call.
    """

    def __init__(
        self,
        descriptors: collections.abc.Sequence[str],
        sources: collections.abc.Sequence[int] | None = None,
        edges: collections.abc.Iterable[tuple[int, int]] = (),
    ) -> None:
        self._descriptors = descriptors
        self._sources = [0] * len(descriptors) if sources is None else sources
        self._edges = list(edges)
        self.asked_weights: list[dict[strategy.LinkKind, int]] = []

    @property
    def descriptors(self) -> collections.abc.Sequence[str]:
        return self._descriptors

    def source_dex(self, class_id: int) -> int:
        return self._sources[class_id]

    def links(
        self,
        class_ids: collections.abc.Sequence[int],
        weights: collections.abc.Mapping[strategy.LinkKind, int],
    ) -> partition.AffinityGraph:
        self.asked_weights.append(dict(weights))
        members = set(class_ids)
        graph = partition.AffinityGraph(len(self._descriptors))
        for first, second in self._edges:
            if first in members and second in members:
                graph.add_edge(first, second)
        return graph

    @property
    def apk(self) -> dexpp.Apk:
        raise AssertionError("this test has no APK")

    @property
    def analysis(self) -> dexpp.AnalysisContext:
        raise AssertionError("this test has no APK")


def make(
    descriptors: collections.abc.Sequence[str],
    sources: collections.abc.Sequence[int] | None = None,
    edges: collections.abc.Iterable[tuple[int, int]] = (),
) -> strategy.Classes:
    """A view of every class in `descriptors`.

    Args:
        descriptors: The descriptor of each class, by class id.
        sources: The input DEX file of each class. None puts every class in the first one.
        edges: The links between classes, each counted once.

    Returns:
        The view, over a `FakeApp`.
    """
    return strategy.Classes(list(range(len(descriptors))), FakeApp(descriptors, sources, edges))
