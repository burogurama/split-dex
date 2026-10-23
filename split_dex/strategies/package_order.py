"""The `package-order` strategy: whole packages in name order, without code analysis."""

import itertools

from split_dex import partition
from split_dex import strategy


class PackageOrder:
    """Put whole packages, sorted by name, into files of about `cap` classes; no code analysis."""

    def __init__(self, *, cap: int = 1000) -> None:
        """Create the strategy.

        Args:
            cap: The target number of classes per DEX file.

        Raises:
            ValueError: If `cap` is below 1.
        """
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self._cap = cap

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        """Sort the classes by package, then by name, and fill files with whole packages.

        The classes are sorted by package first: sorted by descriptor alone, a package would be
        interleaved with its subpackages (`Lcom/a/z;` sorts after `Lcom/a/m/X;`). Packages next
        to each other in name order are usually parts of one library or feature, so this keeps
        related code together without reading any code: it is fast, and a new version of the app
        moves few classes between files. Each package is a block named after it, so a group that
        goes over a 64K limit is cut at a package boundary.

        Args:
            classes: The classes to group.

        Returns:
            The groups.
        """
        descriptors = classes.descriptors
        ordered = sorted(
            classes.ids,
            key=lambda class_id: (
                partition.package_of(descriptors[class_id]),
                descriptors[class_id],
            ),
        )
        runs = itertools.groupby(
            ordered, key=lambda class_id: partition.package_of(descriptors[class_id])
        )
        packages = [list(ids) for _, ids in runs]
        return [
            strategy.Group.of_packages(group, descriptors)
            for group in partition.fill_groups(packages, self._cap)
        ]
