"""The `single` strategy: all the classes in one DEX file."""

from split_dex import strategy


class Single:
    """Put all the classes in one DEX file, cut only where it goes over a 64K limit."""

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        """Put every class in one group, in multidex order.

        Each class is a block of its own, so a group that goes over a 64K limit is cut in the
        middle, and the halves are cut again until each one fits.

        Args:
            classes: The classes to group.

        Returns:
            One group, or none when the set is empty.
        """
        if len(classes.ids) == 0:
            return []
        return [strategy.Group.of_classes(classes.ids)]
