# Migration from pip to Poetry

This document outlines the steps we took to migrate from pip-based dependency management to Poetry.

## Changes Made

1. Created a `pyproject.toml` file with all dependencies
2. Created a `setup_poetry.sh` script to help with progressive installation of dependencies
3. Updated `render.yaml` to use Poetry for deployments
4. Updated README.md to focus on Poetry-based setup
5. Moved old requirements files to the `reference/` directory for historical purposes

## Benefits of Poetry

- Better dependency resolution with proper version constraints
- Lock file (`poetry.lock`) for deterministic builds
- Isolated virtual environments for each project
- Better handling of development vs production dependencies
- Cleaner command-line interface with `poetry run` and `poetry shell`
- Simplified deployment process

## Common Commands

```bash
# Install all dependencies
poetry install

# Add a new dependency
poetry add package_name

# Add a development dependency
poetry add --group dev package_name

# Update dependencies
poetry update

# Update the lock file without changing dependencies
poetry lock --no-update

# Activate the virtual environment
poetry shell

# Run a command in the virtual environment
poetry run command

# Show information about the current environment
poetry env info
```

## Deployment Notes

For Render.com, the `render.yaml` file has been updated to:

1. Install Poetry
2. Configure Poetry not to create virtualenvs (Render provides its own environment)
3. Install project dependencies without development dependencies
4. Start the application

## Troubleshooting

If encountering dependency resolution issues:

1. Try updating the lock file: `poetry lock --no-update`
2. Specify version constraints in `pyproject.toml` to avoid conflicts
3. Use the `setup_poetry.sh` script to install dependencies progressively
4. Check Render logs for specific errors related to dependency installation 