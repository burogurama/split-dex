"""Build small APKs for the tests, from the template classes in `data/templates.dex`.

A test class is a copy of a template class with its types renamed (see `data/Templates.java`),
so each test can say which classes it needs and how they are linked. Each class is returned in a
model of its own; `write_apk` puts the classes into the DEX files of an APK.
"""

import collections.abc
import functools
import pathlib
import zipfile

import dexpp

OBJECT = "Ljava/lang/Object;"

# Methods of a class from make_wide_class: 10,000 native methods and the constructor.
WIDE_CLASS_METHODS = 10_001

_TEMPLATES = pathlib.Path(__file__).parent / "data" / "templates.dex"


def make_class(descriptor: str, superclass: str = OBJECT) -> dexpp.Model:
    """A class with no members besides its constructor.

    Args:
        descriptor: The class descriptor, such as `Lcom/app/a/A;`.
        superclass: The descriptor of its superclass.

    Returns:
        A model that holds the one class.
    """
    if superclass == OBJECT:
        return _from_template("Lfix/Plain;", {"Lfix/Plain;": descriptor})
    return _from_template("Lfix/Child;", {"Lfix/Child;": descriptor, "Lfix/Parent;": superclass})


def make_caller(descriptor: str, target: str) -> dexpp.Model:
    """A class with a static method that calls the static method `go()V` of `target`.

    Args:
        descriptor: The class descriptor.
        target: The descriptor of the class whose method is called.

    Returns:
        A model that holds the one class.
    """
    return _from_template("Lfix/Caller;", {"Lfix/Caller;": descriptor, "Lfix/Target;": target})


def make_wide_class(descriptor: str) -> dexpp.Model:
    """A class with `WIDE_CLASS_METHODS` methods: seven of them overflow one DEX file.

    Args:
        descriptor: The class descriptor.

    Returns:
        A model that holds the one class.
    """
    return _from_template("Lfix/Wide;", {"Lfix/Wide;": descriptor})


def write_apk(
    path: pathlib.Path, dex_files: collections.abc.Sequence[collections.abc.Sequence[dexpp.Model]]
) -> None:
    """Write an APK whose `classes*.dex` entries hold the given classes.

    Args:
        path: Where to write the APK.
        dex_files: The classes of `classes.dex`, then those of `classes2.dex`, and so on. Each
            class is a model from `make_class` or a similar function.
    """
    entries: list[tuple[str, bytes]] = []
    for number, classes in enumerate(dex_files, start=1):
        name = "classes.dex" if number == 1 else f"classes{number}.dex"
        entries.append((name, write_dex(classes)))
    write_zip(path, entries)


def write_dex(classes: collections.abc.Sequence[dexpp.Model]) -> bytes:
    """A DEX file that holds the given classes, in order.

    Args:
        classes: The classes, each a model from `make_class` or a similar function.

    Returns:
        The DEX file.
    """
    dex = dexpp.Model()
    for cls in classes:
        dex.copy_class(cls, cls.class_descriptors[0])
    return dex.write()


def write_zip(path: pathlib.Path, entries: collections.abc.Sequence[tuple[str, bytes]]) -> None:
    """Write a ZIP archive of uncompressed entries, in the given order.

    Args:
        path: Where to write the archive.
        entries: The name and the content of each entry.
    """
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in entries:
            archive.writestr(name, data)


@functools.cache
def _templates() -> dexpp.Model:
    return dexpp.Model.read_file(str(_TEMPLATES))


def _from_template(template: str, renames: dict[str, str]) -> dexpp.Model:
    model = dexpp.Model()
    model.copy_class(_templates(), template)
    model.rename_types(renames)
    return model
