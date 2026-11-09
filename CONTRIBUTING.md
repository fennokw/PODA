# Contributing to PODA Desktop

Application code lives in `poda_app/`, platform startup and packaging in `desktop/`, and regression tests in `tests/`. Use the installation command in the README to build and launch local changes.

Run `python -m pytest -q tests` after setting up a Python environment with `requirements.txt`. Use synthetic accounts and temporary data directories in tests. Keep databases, backups, credentials, and logs outside the source checkout.
