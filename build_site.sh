#!/bin/sh
# Puts the web page and the Python brief together in _site/ (what GitHub Pages serves).
set -e
rm -rf _site
mkdir -p _site
cp -r web/. _site/
cp brief.py _site/
touch _site/.nojekyll
