#!/usr/bin/env bash
# Start the bridge on Linux/macOS. First run creates the virtual environment for you.
# Usage:  ./run.sh            (extra arguments are passed on, e.g. ./run.sh --port /dev/ttyACM0)
cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "Creating virtual environment (first run only) ..."
  python3 -m venv .venv || { echo "Install it with: sudo apt install python3-venv"; exit 1; }
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python bridge.py "$@"
