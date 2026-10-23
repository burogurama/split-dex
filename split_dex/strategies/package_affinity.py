"""The `package-affinity` strategy: group classes by how they are linked, whole packages."""

from split_dex import partition
from split_dex import strategy


class PackageAffinity:
    """Group classes by how they are linked, each package whole, about `cap` classes per file."""

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
        """Group the classes, as `split_dex.partition.partition_by_package_affinity` does.

        Each package is a block named after it, so a group that goes over a 64K limit is cut at
        a package boundary.

        Args:
            classes: The classes to group.

        Returns:
            The groups.
        """
        if len(classes.ids) == 0:
            return []
        groups = partition.partition_by_package_affinity(
            classes.ids, classes.links(), classes.descriptors, self._cap
        )
        return [strategy.Group.of_packages(group, classes.descriptors) for group in groups]
