#!/bin/sh
# Puts the web page and the Python brief together in _site/ (what GitHub Pages serves).
set -e
rm -rf _site
mkdir -p _site
cp -r web/. _site/
cp brief.py _site/
touch _site/.nojekyll
# Tell the service worker which Pyodide version this is, so every app on the site can share one saved copy.
PYODIDE_VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' web/pyodide/package.json | head -1)
[ -n "$PYODIDE_VERSION" ] && sed -i "s/^const PYODIDE_VERSION = \"dev\";/const PYODIDE_VERSION = \"$PYODIDE_VERSION\";/" _site/sw.js
true
