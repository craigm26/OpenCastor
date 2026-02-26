"""Ensure setup catalog snippets in docs/site stay synchronized."""

from __future__ import annotations

from pathlib import Path

from scripts.sync_setup_docs import (
    API_REF_PATH,
    README_PATH,
    SITE_DOCS_PATH,
    _build_api_ref_block,
    _build_readme_block,
    _build_site_block,
    _replace_between_markers,
)

START = "<!-- SETUP_CATALOG:BEGIN -->"
END = "<!-- SETUP_CATALOG:END -->"


def _assert_synced(path: Path, body: str) -> None:
    original = path.read_text(encoding="utf-8")
    updated = _replace_between_markers(original, START, END, body)
    assert original == updated


def test_readme_setup_catalog_snippet_synced():
    _assert_synced(README_PATH, _build_readme_block())


def test_api_reference_setup_catalog_snippet_synced():
    _assert_synced(API_REF_PATH, _build_api_ref_block())


def test_site_docs_setup_catalog_snippet_synced():
    _assert_synced(SITE_DOCS_PATH, _build_site_block())

