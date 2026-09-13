"""Characterize current gaps; passing observations are NOT security guarantees."""
import sqlite3

import pytest

from small_sea_manager import provisioning
from small_sea_manager.manager import TeamManager, bootstrap_existing_identity, create_identity_join_request
from small_sea_note_to_self.db import note_to_self_sync_db_path


@pytest.fixture
def prepared_pair(tmp_path, monkeypatch):
    # Repository helpers can change cwd; restore it before pytest removes tmp_path.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    source, target, cloud = [tmp_path / name for name in ('source', 'target', 'cloud')]
    for path in (source, target, cloud):
        path.mkdir()
    participant = provisioning.create_new_participant(source, 'Alice')
    provisioning.add_cloud_storage(source, participant, protocol='localfolder', url=str(cloud))
    request = create_identity_join_request(target)
    welcome = TeamManager(source, participant).authorize_identity_join(request['join_request_artifact'])
    return source, target, participant, welcome


@pytest.mark.parametrize('substitute', [False, True], ids=['valid-control', 'storage-substitution'])
def test_observe_unsigned_baseline_change_preserves_confirmation(prepared_pair, substitute):
    source, target, participant, welcome = prepared_pair
    source_db = note_to_self_sync_db_path(source, participant)
    with sqlite3.connect(source_db) as conn:
        original = conn.execute('SELECT id, label FROM user_device ORDER BY id LIMIT 1').fetchone()
        if substitute:
            conn.execute('UPDATE user_device SET label = ? WHERE id = ?', ('STORAGE CHANGED THIS', original[0]))
    if substitute:
        repo_dir = source_db.parent
        repo = provisioning._Repo(repo_dir / '.git', repo_dir)
        repo.stage(['core.db'])
        repo.commit('Experiment: storage substitutes bootstrap device label')
        provisioning._push_note_to_self_to_local_remote(
            source, participant, provisioning._single_note_to_self_remote_descriptor(source, participant)
        )
    result = bootstrap_existing_identity(target, welcome['welcome_bundle'])
    assert result['second_confirmation_string'] == welcome['second_confirmation_string']
    with sqlite3.connect(note_to_self_sync_db_path(target, participant)) as conn:
        label = conn.execute('SELECT label FROM user_device WHERE id = ?', (original[0],)).fetchone()[0]
    assert label == ('STORAGE CHANGED THIS' if substitute else original[1])


def test_interruption_before_signature_verification_blocks_identity(prepared_pair, monkeypatch):
    source, target, participant, welcome = prepared_pair

    def interrupt(*args, **kwargs):
        raise RuntimeError('injected interruption before signature verification')

    with monkeypatch.context() as patch:
        patch.setattr(provisioning, 'finalize_identity_bootstrap', interrupt)
        with pytest.raises(RuntimeError, match='injected interruption'):
            bootstrap_existing_identity(target, welcome['welcome_bundle'])

    with pytest.raises(ValueError, match='blocked'):
        TeamManager(target, participant)
    assert (target / '.small-sea-manager' / 'pending_identity_join.json').exists()


def test_storage_replacement_of_joining_signing_key_is_rejected(prepared_pair):
    from cuttlefish import generate_bootstrap_signing_keypair
    from small_sea_note_to_self.bootstrap import deserialize_join_request_artifact
    import json

    source, target, participant, welcome = prepared_pair
    state = json.loads((target / '.small-sea-manager' / 'pending_identity_join.json').read_text())
    artifact = deserialize_join_request_artifact(state['join_request_artifact'])
    joining_id = bytes.fromhex(artifact.device_id_hex)
    _, attacker_public_key = generate_bootstrap_signing_keypair()
    source_db = note_to_self_sync_db_path(source, participant)
    with sqlite3.connect(source_db) as conn:
        conn.execute('UPDATE user_device SET signing_key = ? WHERE id = ?', (attacker_public_key, joining_id))
    repo = provisioning._Repo(source_db.parent / '.git', source_db.parent)
    repo.stage(['core.db'])
    repo.commit('Experiment: storage replaces joining signing key')
    provisioning._push_note_to_self_to_local_remote(
        source, participant, provisioning._single_note_to_self_remote_descriptor(source, participant)
    )
    with pytest.raises(ValueError, match='joining device keys'):
        bootstrap_existing_identity(target, welcome['welcome_bundle'])
    with pytest.raises(ValueError, match='blocked'):
        TeamManager(target, participant)


def test_hub_rejects_pending_identity_before_reading_shared_database(prepared_pair):
    from small_sea_hub.backend import SmallSeaBackend, SmallSeaNotFoundExn

    source, target, participant, welcome = prepared_pair
    provisioning.prepare_identity_bootstrap(target, welcome['welcome_bundle'])
    backend = SmallSeaBackend(str(target), auto_approve_sessions=True)
    # There is no shared DB yet; discovery must not try opening one.
    with pytest.raises(SmallSeaNotFoundExn):
        backend.request_session('Alice', 'SmallSeaCollectiveCore', 'NoteToSelf', 'Smoke Tests')
    with pytest.raises(ValueError, match='blocked'):
        backend.request_session(participant, 'SmallSeaCollectiveCore', 'NoteToSelf', 'Smoke Tests')


def test_hub_rechecks_block_for_pending_and_existing_sessions(prepared_pair):
    from small_sea_hub.backend import SmallSeaBackend

    source, target, participant, welcome = prepared_pair
    bootstrap_existing_identity(target, welcome['welcome_bundle'])
    backend = SmallSeaBackend(str(target), auto_approve_sessions=True)
    pending_id, pin = backend.request_session(participant, 'SmallSeaCollectiveCore', 'NoteToSelf', 'Smoke Tests')
    token = backend.open_session(participant, 'SmallSeaCollectiveCore', 'NoteToSelf', 'Smoke Tests')
    provisioning._mark_identity_bootstrap_untrusted(target, participant, reason='experiment')
    with pytest.raises(ValueError, match='blocked'):
        backend.confirm_session(pending_id, pin)
    with pytest.raises(ValueError, match='blocked'):
        backend._lookup_session(token.hex())


def test_prepare_can_retry_before_source_adoption(prepared_pair):
    source, target, participant, welcome = prepared_pair
    provisioning.prepare_identity_bootstrap(target, welcome['welcome_bundle'])
    with pytest.raises(ValueError, match='blocked'):
        TeamManager(target, participant)
    result = bootstrap_existing_identity(target, welcome['welcome_bundle'])
    assert result['participant_hex'] == participant
    TeamManager(target, participant)


@pytest.mark.parametrize('contents', ['{', '[]', 'null'])
def test_hub_treats_incomplete_status_marker_as_blocked(prepared_pair, contents):
    from small_sea_hub.backend import SmallSeaBackend, SmallSeaNotFoundExn
    source, target, participant, welcome = prepared_pair
    provisioning.prepare_identity_bootstrap(target, welcome['welcome_bundle'])
    provisioning._identity_bootstrap_status_path(target, participant).write_text(contents)
    backend = SmallSeaBackend(str(target), auto_approve_sessions=True)
    with pytest.raises(SmallSeaNotFoundExn):
        backend.request_session('Alice', 'SmallSeaCollectiveCore', 'NoteToSelf', 'Smoke Tests')
    with pytest.raises(ValueError, match='blocked'):
        backend.request_session(participant, 'SmallSeaCollectiveCore', 'NoteToSelf', 'Smoke Tests')
