import pathlib

from Experiments.git_history_pruning import run_experiment as exp


def test_compute_boundary_uses_dag_closure(tmp_path: pathlib.Path):
    repo = exp.build_repo(tmp_path, "repo_a_typical", 24, 101)

    boundary, _boundary_parent, window_commits, old_commits = exp.compute_boundary(repo, 5)

    assert boundary
    assert window_commits
    assert old_commits

    main_first_parent = exp.get_commit_list(repo, "main", first_parent=True)
    main_head = exp.git(repo, "rev-parse", "main").stdout.strip()
    recent_feature = exp.git(repo, "rev-parse", "recent-feature").stdout.strip()
    legacy_feature = exp.git(repo, "rev-parse", "legacy-feature").stdout.strip()
    pre_boundary_main = main_first_parent[-6]

    assert main_head in window_commits
    assert recent_feature in window_commits
    assert pre_boundary_main in old_commits
    assert legacy_feature not in window_commits


def test_filtered_finalize_preserves_missing_objects(tmp_path: pathlib.Path):
    source = exp.build_repo(tmp_path, "repo_c_large_files", 24, 303)
    boundary, _boundary_parent, window_commits, _old_commits = exp.compute_boundary(source, 5)

    clone = tmp_path / "clone"
    exp.make_blobless_clone(source, clone)
    checkout_result = exp.rehydrate_checkout(clone, boundary, window_commits)

    assert checkout_result.ok

    before_missing = exp.count_missing_objects(clone)
    before_size = exp.git_size_kib(clone)

    notes = exp.finalize_pruned_repo(clone)

    after_missing = exp.count_missing_objects(clone)
    after_size = exp.git_size_kib(clone)

    assert notes == []
    assert before_missing > 0
    assert after_missing > 0
    assert after_size <= before_size
