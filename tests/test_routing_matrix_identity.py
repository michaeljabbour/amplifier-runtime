"""Discovery uses loadable filenames and exposes uncertainty about cached revisions."""

from pathlib import Path

from amplifier_runtime.kernel import routing_admin


def matrix(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_filename_identity_includes_nameless_matrix(tmp_path: Path) -> None:
    nameless = matrix(tmp_path, "custom/fast.yaml", "roles: {fast: {candidates: []}}\n")
    mismatch = matrix(tmp_path, "custom/other.yaml", "name: fast\ndescription: other\n")
    loaded = routing_admin.load_all_matrices([nameless, mismatch])
    assert set(loaded) == {"fast", "other"}
    assert loaded["other"]["description"] == "other"
    assert "name" not in loaded["fast"]


def test_custom_wins_even_when_global_sort_would_choose_bundle(tmp_path: Path) -> None:
    custom = matrix(tmp_path, "aaa-custom/fast.yaml", "description: custom\n")
    bundle = matrix(
        tmp_path,
        "zzz/amplifier-bundle-routing-matrix-one/routing/fast.yaml",
        "description: bundle\n",
    )
    files = sorted([custom, bundle])
    assert files[-1] == bundle  # The old last-write-wins rule selects the other file.
    assert routing_admin.load_all_matrices(files)["fast"]["description"] == "custom"
    selected = routing_admin.select_matrix_files(files)["fast"]
    assert selected.path == custom
    assert selected.shadowed_files == (bundle,)
    assert not selected.ambiguous_bundle


def test_first_custom_directory_wins_and_broken_winner_does_not_fall_back(tmp_path: Path) -> None:
    first = matrix(tmp_path, "first/fast.yaml", "[broken\n")
    second = matrix(tmp_path, "second/fast.yaml", "description: usable\n")
    assert routing_admin.select_matrix_files([first, second])["fast"].path == first
    assert routing_admin.load_all_matrices([first, second]) == {}
    assert routing_admin.load_all_matrices([second, first])["fast"]["description"] == "usable"


def test_distinct_cached_versions_do_not_claim_known_shadowing(tmp_path: Path) -> None:
    first = matrix(tmp_path, "amplifier-bundle-routing-matrix-one/routing/fast.yaml", "roles: {}\n")
    second = matrix(
        tmp_path, "amplifier-bundle-routing-matrix-two/routing/fast.yaml", "roles: {}\n"
    )
    selected = routing_admin.select_matrix_files([first, second])["fast"]
    assert selected.ambiguous_bundle
    assert selected.shadowed_files is None
    assert selected.candidate_files == (first, second)


def test_duplicate_paths_cannot_shadow_themselves(tmp_path: Path) -> None:
    path = matrix(tmp_path, "custom/fast.yaml", "roles: {}\n")
    selected = routing_admin.select_matrix_files([path, path.parent / "." / path.name])["fast"]
    assert selected.candidate_files == (path,)
    assert selected.shadowed_files == ()


def test_listing_exposes_filename_and_mismatch_without_changing_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = matrix(home, "settings.yaml", "routing: {matrix: fast}\n")
    before = settings.read_bytes()
    path = matrix(home, "routing/fast.yaml", "description: no name\nroles: {}\n")
    (row,) = routing_admin.list_matrices(project_dir=tmp_path / "project", amplifier_home=home)
    assert row.name == "fast"
    assert row.active
    assert row.matrix_file == path
    assert row.declared_name_mismatch is None
    path.write_text("name: wrong\nroles: {}\n", encoding="utf-8")
    (row,) = routing_admin.list_matrices(project_dir=tmp_path / "project", amplifier_home=home)
    assert row.name == "fast"
    assert row.declared_name_mismatch == "wrong"
    assert settings.read_bytes() == before
