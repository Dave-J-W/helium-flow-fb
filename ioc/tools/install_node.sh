#!/bin/bash
set -e
pacman -S --needed --noconfirm mingw-w64-x86_64-nodejs
node --version
