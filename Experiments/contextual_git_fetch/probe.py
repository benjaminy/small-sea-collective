"""Put a simulated per-commit authority check on the real CodSync fetch path.

Research only. No runtime module is changed, no Constitution evaluator is
implemented, and no Hub API is approved. The policy view and its grant records
are hand-written local inputs, not globally current authority.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/cod-sync'))

from cod_sync.protocol import MAIN_REF, CodSync
from cod_sync.repo import Repo, RepoError
from cod_sync.store import LocalFolderStore
from cod_sync.verify import (
    SignatureInvalidError,
    UnsignedCommitError,
    VerificationError,
    VerificationUnavailableError,
)

BERTH = 'berth-A'
PURPOSE = 'commit-signing'


class AuthorityPaused(VerificationError):
    """No local record says whether this signer may write here. Ask a human."""


class AuthorityRefused(VerificationError):
    """A local record says this signer may not write here."""


class PolicyViewChanged(VerificationError):
    """The local policy view changed while this decision was being made."""


class PolicyView:
    """A simulated local view: which fingerprint holds which berth and purpose,
    plus a finite set of exact commit receipts a human already accepted."""

    def __init__(self, view_id, grants, receipts):
        self.view_id = view_id
        self.grants = dict(grants)
        self.receipts = set(receipts)


class ContextualVerifier:
    """Signature evidence first, then an explicit simulated policy decision.

    Evidence comes from Repo.signature_report under an intentionally empty
    allowed-signers file, so a valid signature by an unrecognized key reports
    U with its fingerprint instead of collapsing into a rejection. B fails as
    an invalid signature, N as an unsigned commit, and setup or ancestry
    problems stay unavailable.

    The verifier deep-copies the caller's policy view on entry and decides
    against that copy, then rechecks the live view id before returning. Its
    records live in memory in this object; nothing is written to disk.
    """

    def __init__(self, view, empty_signers, mutate_after_capture=None):
        self.view = view
        self.empty_signers = empty_signers
        self.mutate_after_capture = mutate_after_capture
        self.records = []

    def verify_history(self, repo, head):
        captured = copy.deepcopy(self.view)
        if self.mutate_after_capture is not None:
            # Experiment-only: a policy change that lands while the verifier
            # is still deciding, so the final view-id check can see it.
            self.mutate_after_capture()
        record = {'head': head, 'policy_view_captured': captured.view_id,
                  'evidence': {}, 'outcome': None}
        self.records.append(record)
        evidence = record['evidence']
        try:
            try:
                rows, diagnostics = repo.signature_report(head, self.empty_signers)
            except (RepoError, OSError) as exc:
                raise VerificationUnavailableError(str(exc), commit=head) from exc
            if diagnostics.strip():
                raise VerificationUnavailableError(diagnostics.strip(), commit=head)
            if not rows:
                raise VerificationUnavailableError('no commits reported', commit=head)

            for row in rows:
                if row.status == 'B':
                    raise SignatureInvalidError('bad signature', commit=row.commit)
                if row.status == 'N':
                    raise UnsignedCommitError('unsigned commit', commit=row.commit)
                if row.status not in ('U', 'G') or not row.fingerprint:
                    raise VerificationUnavailableError(row.status, commit=row.commit)
                evidence[row.commit] = row.fingerprint
            for row in rows:
                if row.commit in captured.receipts:
                    continue  # finite exact previously accepted commit
                grant = captured.grants.get(row.fingerprint)
                if grant is None:
                    raise AuthorityPaused('unknown authority', commit=row.commit)
                if grant != (BERTH, PURPOSE):
                    raise AuthorityRefused(f'granted {grant}', commit=row.commit)
            if self.view.view_id != captured.view_id:
                raise PolicyViewChanged(
                    f'{captured.view_id} -> {self.view.view_id}', commit=head)
        except VerificationError as exc:
            # Keep the captured view id and verified signing fingerprints on
            # failures too: an unknown authority must not erase what the
            # signature check already established.
            record['outcome'] = type(exc).__name__
            raise
        record['outcome'] = 'accepted'
        return dict(evidence)


def run():
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                      GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
    os.environ.pop('SSH_AUTH_SOCK', None)
    out = {}
    with tempfile.TemporaryDirectory(prefix='contextual-fetch-') as tmp:
        root = Path(tmp)
        empty = root / 'empty-signers'
        empty.write_text('')

        def keygen(name):
            path = root / name
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                            '-f', str(path)], check=True, capture_output=True)
            fp = subprocess.run(['ssh-keygen', '-lf', str(path) + '.pub'],
                                check=True, capture_output=True, text=True)
            return path, fp.stdout.split()[1]

        keys = {n: keygen(n) for n in ('A_new', 'A_old', 'B')}
        out['fingerprints'] = {n: k[1] for n, k in keys.items()}

        def peer(name, commits, sign=True):
            """Build a peer repo and publish it to its own local store."""
            work = root / name
            work.mkdir()
            repo = Repo.init(work / '.git').with_work_tree(work)
            for key, opt in [('user.name', name), ('user.email', f'{name}@invalid'),
                             ('gpg.format', 'ssh'),
                             ('commit.gpgsign', 'true' if sign else 'false')]:
                repo.config(key, opt)
            oids = {}
            for label, signer in commits:
                repo.config('user.signingkey', str(keys[signer][0]))
                (work / label).write_text(label)
                repo.stage([label])
                oids[label] = repo.commit(label)
            store_dir = root / f'{name}-store'
            store_dir.mkdir()
            store = LocalFolderStore(str(store_dir))
            CodSync(repo, store).publish()
            return store, oids, repo

        def fetch_case(name, store, view, mutate_after_capture=None,
                       before_pin=None, signers=None):
            """Fetch once and report what the one durable ref did.

            before_pin is experiment-only: it wraps the local Repo's
            advance_ref and runs immediately before delegating to the real
            method, which is after CodSync's verifier call has returned.
            """
            local_dir = root / f'local-{name}'
            local_dir.mkdir()
            local = Repo.init(local_dir / '.git').with_work_tree(local_dir)
            if before_pin is not None:
                original_advance_ref = local.advance_ref

                def wrapped(ref_name, new_sha, _original=original_advance_ref):
                    before_pin()
                    return _original(ref_name, new_sha)

                local.advance_ref = wrapped
            pin = 'refs/smallsea/peer'
            before = local.resolve_ref(pin)
            verifier = ContextualVerifier(
                view, empty if signers is None else signers, mutate_after_capture)
            record = {'pin_before': before}
            try:
                result = CodSync(local, store, verifier=verifier).fetch(pin_to_ref=pin)
                record['outcome'] = 'fetched'
                record['pin_disposition'] = result.pin_disposition
            except VerificationError as exc:
                record['outcome'] = type(exc).__name__
            record['pin_after'] = local.resolve_ref(pin)
            record['advanced'] = record['pin_after'] not in (None, before)
            record['verifier_records'] = verifier.records
            out[name] = record
            return record

        grants_ok = {keys['A_new'][1]: (BERTH, PURPOSE)}
        grants_b = {keys['B'][1]: ('berth-B', PURPOSE)}

        # 1. Authorized scoped work.
        store, oids, _ = peer('scoped', [('w1', 'A_new'), ('w2', 'A_new')])
        r = fetch_case('scoped', store, PolicyView('v1', grants_ok, []))
        assert r['advanced'] and r['pin_after'] == oids['w2'], r

        # 2. Unknown authority pauses; it is not a bad signature.
        store, _, _ = peer('unknown', [('w1', 'A_new')])
        r = fetch_case('unknown_authority', store, PolicyView('v1', {}, []))
        assert r['outcome'] == 'AuthorityPaused' and not r['advanced'], r
        assert r['verifier_records'][0]['evidence'], r

        # 3. The caller's fixed scope does not match this key's grant.
        store, _, _ = peer('wrongberth', [('w1', 'B')])
        r = fetch_case('grant_scope_mismatch', store,
                       PolicyView('v1', {**grants_ok, **grants_b}, []))
        assert r['outcome'] == 'AuthorityRefused' and not r['advanced'], r

        # 4. Invalid signature: tamper with a published commit's bytes locally.
        store, oids, src = peer('tampered', [('w1', 'A_new')])
        raw = subprocess.run(['git', '--git-dir', str(root / 'tampered/.git'),
                              'cat-file', 'commit', oids['w1']],
                             check=True, capture_output=True).stdout
        bad = raw.replace(b'\nw1\n', b'\nw9\n')
        assert bad != raw
        tam = subprocess.run(['git', '--git-dir', str(root / 'tampered/.git'),
                              'hash-object', '-t', 'commit', '-w', '--stdin'],
                             input=bad, check=True, capture_output=True).stdout.decode().strip()
        subprocess.run(['git', '--git-dir', str(root / 'tampered/.git'),
                        'update-ref', MAIN_REF, tam], check=True, capture_output=True)
        (root / 'tampered-store-2').mkdir()
        store2 = LocalFolderStore(str(root / 'tampered-store-2'))
        CodSync(src, store2).publish()
        r = fetch_case('invalid_signature', store2, PolicyView('v1', grants_ok, []))
        assert r['outcome'] == 'SignatureInvalidError' and not r['advanced'], r

        # 5. Removed key: an accepted ancestor coexists with current work, but
        # later work by the removed key does not become acceptable.
        store, oids, src = peer('removed', [('old', 'A_old'), ('cur', 'A_new')])
        r = fetch_case('accepted_ancestor', store,
                       PolicyView('v2', grants_ok, [oids['old']]))
        assert r['advanced'] and r['pin_after'] == oids['cur'], r
        r = fetch_case('ancestor_without_receipt', store, PolicyView('v2', grants_ok, []))
        assert r['outcome'] == 'AuthorityPaused' and not r['advanced'], r
        src.config('user.signingkey', str(keys['A_old'][0]))
        (root / 'removed/late').write_text('late')
        src.stage(['late'])
        late = src.commit('late')
        (root / 'removed-store-2').mkdir()
        store3 = LocalFolderStore(str(root / 'removed-store-2'))
        CodSync(src, store3).publish()
        r = fetch_case('removed_key_later_work', store3,
                       PolicyView('v2', grants_ok, [oids['old']]))
        assert r['outcome'] == 'AuthorityPaused' and not r['advanced'], r
        out['late_commit'] = late

        # 6. Unsigned work is unsigned, not unavailable. Signed control is
        # case 1 above, which advanced under the same code path.
        store, _, _ = peer('unsigned', [('w1', 'A_new')], sign=False)
        r = fetch_case('unsigned_commit', store, PolicyView('v1', grants_ok, []))
        assert r['outcome'] == 'UnsignedCommitError' and not r['advanced'], r

        # 7. A missing allowed-signers file is a setup failure: unavailable,
        # pin unmoved.
        store, _, _ = peer('nosigners', [('w1', 'A_new')])
        r = fetch_case('missing_signers_file', store, PolicyView('v1', grants_ok, []),
                       signers=root / 'no-such-signers')
        assert r['outcome'] == 'VerificationUnavailableError' and not r['advanced'], r

        # 8a. A policy change the verifier can still see: the final view-id
        # check catches it and the pin does not move.
        store, oids, _ = peer('race_before', [('w1', 'A_new')])
        view_a = PolicyView('v3', grants_ok, [])

        def revoke_a():
            view_a.view_id = 'v4'
            view_a.grants = {}

        r = fetch_case('policy_change_before_return', store, view_a,
                       mutate_after_capture=revoke_a)
        assert r['outcome'] == 'PolicyViewChanged' and not r['advanced'], r

        # 8b. A policy change after the verifier returned and before the pin
        # moves. Nothing in the interface rechecks it, so the pin advances.
        store, oids, _ = peer('race_after', [('w1', 'A_new')])
        view_b = PolicyView('v3', grants_ok, [])

        def revoke_b():
            view_b.view_id = 'v4'
            view_b.grants = {}

        r = fetch_case('policy_change_after_return', store, view_b,
                       before_pin=revoke_b)
        out['policy_change_after_return']['view_at_pin_move'] = view_b.view_id
        out['policy_change_after_return']['grant_holds_now'] = bool(view_b.grants)
        assert r['advanced'] and r['pin_after'] == oids['w1'], r
    return out


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, sort_keys=True))
