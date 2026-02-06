"""Pytest configuration for CVAS tests."""

import pytest


def pytest_addoption(parser):
    """Add custom command-line options."""
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Update expected snapshot files instead of comparing",
    )


@pytest.fixture
def update_snapshots(request):
    """Fixture to access --update-snapshots option."""
    return request.config.getoption("--update-snapshots")
