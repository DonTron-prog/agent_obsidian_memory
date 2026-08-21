from pathlib import Path

from tests.fixtures.builders import build_vault, concept_text


def test_fixture_builder_is_deterministic(tmp_path: Path) -> None:
    first = build_vault(tmp_path / "first")
    second = build_vault(tmp_path / "second")

    first_files = {
        path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()
    }
    second_files = {
        path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()
    }
    assert first_files == second_files
    assert "2026-01-02T03:04:05Z" in concept_text()
