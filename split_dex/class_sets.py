"""Sort the classes of an APK into named sets, by patterns on their names.

Each set is grouped by its own strategy, and two sets never share a DEX file.

Patterns match binary class names, such as `com.myapp.Main` or `com.myapp.Outer$Inner`. In a
pattern, `*` matches any characters except a dot, and `**` matches any characters, dots
included. So `com.myapp.**` matches every class under `com.myapp`, `com.myapp.*` matches the
classes directly in `com.myapp`, and `**` matches every class.
"""

import collections.abc
import dataclasses
import re

import split_dex
from split_dex import strategy

# How many unmatched classes an error message names.
_EXAMPLES = 3


class AssignmentError(split_dex.Error):
    """Raised when the classes cannot be sorted into the sets."""


@dataclasses.dataclass(frozen=True)
class ClassSet:
    """A named set of classes, and the strategy that groups them.

    Attributes:
        name: The set's name, written to sets.csv next to each of its DEX files.
        strategy: How the classes of the set are grouped.
    """

    name: str
    strategy: strategy.Strategy


@dataclasses.dataclass(frozen=True)
class Assignment:
    """Sends the classes whose names match `pattern` to the set named `set_name`.

    Attributes:
        pattern: A pattern on binary class names, such as `com.myapp.**`.
        set_name: The name of a set.
    """

    pattern: str
    set_name: str


def binary_name(descriptor: str) -> str:
    """The binary name of a class: `Lcom/a/B$C;` gives `com.a.B$C`.

    Args:
        descriptor: A class descriptor.

    Returns:
        The binary name.
    """
    return descriptor[1:-1].replace("/", ".")


def assign(
    descriptors: collections.abc.Sequence[str],
    sets: collections.abc.Sequence[ClassSet],
    assignments: collections.abc.Sequence[Assignment],
) -> dict[str, list[int]]:
    """Sort classes into sets.

    Each class goes to the set of the first assignment whose pattern matches its name. With no
    assignments and a single set, every class goes to that set.

    Args:
        descriptors: The descriptor of every class, by class id.
        sets: The sets.
        assignments: The assignments, in the order they are tried.

    Returns:
        The ids of the classes of each set, in class id order, by set name. A set that no class
        matches has an empty list.

    Raises:
        AssignmentError: If two sets have the same name, an assignment names an unknown set,
            there are several sets but no assignments, or a class matches no pattern.
    """
    members: dict[str, list[int]] = {}
    for class_set in sets:
        if class_set.name in members:
            raise AssignmentError(f"two sets are named {class_set.name!r}")
        members[class_set.name] = []
    if len(assignments) == 0:
        if len(sets) != 1:
            raise AssignmentError("with several sets, assignments must say which classes go where")
        members[sets[0].name] = list(range(len(descriptors)))
        return members
    rules = [(_compile(rule.pattern), _members_of(members, rule.set_name)) for rule in assignments]
    unmatched: list[str] = []
    for class_id, descriptor in enumerate(descriptors):
        name = binary_name(descriptor)
        target = next((ids for pattern, ids in rules if pattern.fullmatch(name) is not None), None)
        if target is None:
            unmatched.append(name)
        else:
            target.append(class_id)
    if len(unmatched) > 0:
        raise AssignmentError(
            f"{len(unmatched)} classes match no pattern, such as "
            f"{', '.join(unmatched[:_EXAMPLES])}; add a last pattern such as '**' to catch them"
        )
    return members


def _members_of(members: dict[str, list[int]], set_name: str) -> list[int]:
    ids = members.get(set_name)
    if ids is None:
        raise AssignmentError(f"no set is named {set_name!r}")
    return ids


def _compile(pattern: str) -> re.Pattern[str]:
    """The regular expression of a pattern: `**` is any text, `*` any text without a dot."""
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^.]*")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts))
