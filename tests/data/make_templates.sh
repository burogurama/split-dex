#!/bin/sh
# Rebuild templates.dex from Templates.java and the generated class Wide, which
# declares 10,000 native methods (a few copies of it overflow the 64K method
# limit of one DEX file).  Needs javac and the d8 of the Android SDK; set D8
# when d8 is not on the PATH:
#
#   D8=~/Android/Sdk/build-tools/36.0.0/d8 tests/data/make_templates.sh
set -eu

here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

{
    echo 'package fix;'
    echo 'class Wide {'
    i=0
    while [ "$i" -lt 10000 ]; do
        echo "    native void m$i();"
        i=$((i + 1))
    done
    echo '}'
} >"$work/Wide.java"

"${JAVAC:-javac}" --release 8 -Xlint:-options -d "$work/classes" \
    "$here/Templates.java" "$work/Wide.java"
"${D8:-d8}" --output "$work" "$work"/classes/fix/*.class
mv "$work/classes.dex" "$here/templates.dex"
