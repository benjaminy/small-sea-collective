"""Separate valid commit signatures, a finite accepted history, and berth authority.

Research probe, not runtime policy. The trailer names and record shapes here are
experimental. See README.md.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from cod_sync.repo import Repo
from cod_sync.verify import SshCommitVerifier, SignatureInvalidError, UnknownSignerError

BASIS_1 = 'basis-1-constitution-digest'
BASIS_2 = 'basis-2-constitution-digest'

# Explicit simulated local inputs. These are hand-written records standing in for
# what #266 must eventually supply. Nothing below derives them from the fetched
# signer list, from Git metadata, or from any global view.
def local_records(fps):
    return {
        'grants': {
            # fingerprint -> (berth, purpose, removed?)
            fps['A_old']: ('A', 'commit-signing', True),
            fps['A_new']: ('A', 'commit-signing', False),
            fps['B']: ('B', 'commit-signing', False),
        },
        'known_bases': {BASIS_2},          # bases this device currently holds as its view
        'accepted_history': {},            # commit -> fingerprint, filled in after commits exist
        'berth': 'A',
        'purpose': 'commit-signing',
        'device': 'newcomer-a',
    }


def union_policy(records, commit, claim, fingerprint):
    """Naive baseline 1: anyone who ever signed for this berth."""
    berth, _purpose, _removed = records['grants'].get(fingerprint, (None, None, None))
    if berth == records['berth']:
        return 'accept', 'signer held berth authority at some point'
    return 'reject', 'signer never held berth authority'


def current_only_policy(records, commit, claim, fingerprint):
    """Naive baseline 2: only keys with authority right now."""
    berth, purpose, removed = records['grants'].get(fingerprint, (None, None, None))
    if berth == records['berth'] and purpose == records['purpose'] and not removed:
        return 'accept', 'signer holds current berth authority'
    return 'reject', 'signer does not hold current berth authority'


def bounded_policy(records, commit, claim, fingerprint):
    """Finite exact acceptance for old work; current local policy for everything else."""
    accepted = records['accepted_history'].get(commit)
    if accepted is not None:
        if (accepted['fingerprint'] != fingerprint or accepted['berth'] != records['berth']
                or accepted['purpose'] != records['purpose'] or accepted['device'] != records['device']):
            return 'reject', 'acceptance record has another signer, scope or deciding device'
        return 'accept', 'exact commit is in the recorded human acceptance set'
    if claim.get('berth') is None:
        return 'pause', 'no berth claim; authority is unjudgeable, signature is unaffected'
    if claim.get('ambiguous_basis'):
        return 'pause', 'multiple basis claims; no unique authority basis'
    if claim.get('basis') is None:
        return 'pause', 'no Constitution basis claimed; authority is unjudgeable, signature is unaffected'
    if claim['berth'] != records['berth']:
        return 'reject', 'commit claims another berth'
    berth, purpose, removed = records['grants'].get(fingerprint, (None, None, None))
    if berth != records['berth'] or purpose != records['purpose']:
        return 'reject', 'valid signature, but this key has no authority in this berth'
    if removed:
        # A cited old basis is signed bytes the signer chose, and so are the dates.
        return 'reject', ('signer was removed; the cited basis and commit dates do not '
                          'prove this commit existed before the removal')
    if claim['basis'] not in records['known_bases']:
        return 'pause', 'claimed basis is not in this device\'s view; cannot judge'
    return 'accept', 'simulated current grant permits signer; claimed basis label is locally listed'


POLICIES = {'historical_union': union_policy,
            'current_only': current_only_policy,
            'bounded_contextual': bounded_policy}


def run():
    with tempfile.TemporaryDirectory(prefix='berth-authority-') as tmp:
        root = Path(tmp)
        repo_dir = root / 'berthA'
        repo_dir.mkdir()
        env = os.environ.copy()
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                   GIT_TERMINAL_PROMPT='0')
        # Repo() shells out with this process's environment, so isolate it too.
        os.environ.update({k: env[k] for k in
                           ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM', 'GIT_TERMINAL_PROMPT')})

        def git(*args, extra_env=None, check=True):
            e = dict(env, **(extra_env or {}))
            return subprocess.run(('git',) + args, cwd=repo_dir, env=e, text=True,
                                  capture_output=True, check=check)

        keys = {}
        for name in ('A_old', 'A_new', 'B'):
            path = root / name
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', name,
                            '-f', str(path)], check=True, capture_output=True)
            keys[name] = path
        fps = {}
        for name, path in keys.items():
            out = subprocess.run(['ssh-keygen', '-lf', str(path.with_suffix('.pub'))],
                                 check=True, capture_output=True, text=True).stdout
            fps[name] = out.split()[1]

        git('init', '-q')
        git('config', 'user.name', 'Basis probe')
        git('config', 'user.email', 'probe@example.invalid')
        git('config', 'gpg.format', 'ssh')
        git('config', 'commit.gpgsign', 'true')
        git('config', 'gc.auto', '0')

        def commit(signer, message, berth=None, basis=None, date=None):
            body = [message, '']
            if berth:
                body.append(f'X-Experimental-Berth: {berth}')
            if basis:
                body.append(f'X-Experimental-Basis: {basis}')
            dates = {'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date} if date else None
            git('-c', f'user.signingkey={keys[signer]}', 'commit', '-q', '--allow-empty',
                '-m', '\n'.join(body), extra_env=dates)
            return git('rev-parse', 'HEAD').stdout.strip()

        oids = {}
        oids['old'] = commit('A_old', 'old accepted work', 'A', BASIS_1, '2020-01-01T00:00:00Z')
        oids['current'] = commit('A_new', 'current work', 'A', BASIS_2)
        oids['no_basis'] = commit('A_new', 'work with no basis claim', 'A')
        oids['unknown_basis'] = commit('A_new', 'unknown basis', 'A', 'unknown-basis')
        oids['ambiguous_basis'] = commit('A_new', 'ambiguous basis\n\nX-Experimental-Basis: other-basis', 'A', BASIS_2)
        # A removal happens here, in the simulated local records only.
        git('checkout', '-q', '-b', 'late', oids['old'])
        oids['backdated'] = commit('A_old', 'new work, old basis, early dates', 'A', BASIS_1,
                                   '2020-01-02T00:00:00Z')
        git('checkout', '-q', '-b', 'cross', oids['old'])
        oids['cross_berth'] = commit('B', 'work signed from another berth', 'A', BASIS_2)

        # Forge: take the signed object for `current` and change one byte of its message.
        raw = subprocess.run(['git', 'cat-file', 'commit', oids['current']], cwd=repo_dir,
                             env=env, check=True, capture_output=True).stdout
        forged = raw.replace(b'current work', b'CURRENT work')
        assert forged != raw
        tampered = subprocess.run(['git', 'hash-object', '-t', 'commit', '-w', '--stdin'],
                                  cwd=repo_dir, env=env, check=True, capture_output=True,
                                  input=forged, text=False).stdout.decode().strip()

        repo = Repo(repo_dir / '.git', repo_dir)
        all_keys = [keys[n].with_suffix('.pub').read_text() for n in keys]
        verifier = SshCommitVerifier(all_keys)

        # Independent check: real Git signature verification, with no policy involved.
        evidence = {}
        for head in ('current', 'backdated', 'cross_berth'):
            evidence.update(verifier.verify_history(repo, oids[head]))
        evidence.update(verifier.verify_history(repo, oids['ambiguous_basis']))
        by_name = {fp: n for n, fp in fps.items()}
        signature_evidence = {name: {'commit': oid, 'git_status': 'G',
                                     'fingerprint': evidence[oid],
                                     'signer': by_name[evidence[oid]]}
                              for name, oid in oids.items()}

        try:
            verifier.verify_history(repo, tampered)
        except SignatureInvalidError as error:
            tamper_result = {'commit': tampered, 'outcome': type(error).__name__,
                             'detail': str(error)}
        else:
            raise AssertionError('tampered commit verified; the control is broken')

        def claims(oid):
            body = git('show', '-s', '--format=%B', oid).stdout
            out = {'berth': None, 'basis': None, 'ambiguous_basis': False}
            basis_claims = []
            for line in body.splitlines():
                if line.startswith('X-Experimental-Berth:'):
                    out['berth'] = line.split(':', 1)[1].strip()
                if line.startswith('X-Experimental-Basis:'):
                    basis_claims.append(line.split(':', 1)[1].strip())
            out['ambiguous_basis'] = len(basis_claims) > 1
            out['basis'] = basis_claims[0] if len(basis_claims) == 1 else None
            return out

        records = local_records(fps)
        # The human's finite acceptance decision: this exact commit, by this exact key.
        records['accepted_history'] = {oids['old']: {
            'fingerprint': fps['A_old'], 'berth': 'A', 'purpose': 'commit-signing',
            'device': 'newcomer-a', 'decision_view': BASIS_2}}

        def judge(records):
            table = {}
            for name, oid in oids.items():
                claim = claims(oid)
                fp = evidence[oid]
                table[name] = {'claim': claim, 'signer': by_name[fp]}
                for pname, policy in POLICIES.items():
                    verdict, why = policy(records, oid, claim, fp)
                    table[name][pname] = {'verdict': verdict, 'why': why}
            return table

        before = judge(records)
        # Keep berth keys distinct: revoke A_new within A rather than moving B's
        # key into A. The same signed commit now needs a different local decision.
        revised = local_records(fps)
        revised['accepted_history'] = dict(records['accepted_history'])
        revised['grants'][fps['A_new']] = ('A', 'commit-signing', True)
        after = judge(revised)
        assert before['current']['bounded_contextual']['verdict'] == 'accept'
        assert after['current']['bounded_contextual']['verdict'] == 'reject'
        assert verifier.verify_history(repo, oids['current'])[oids['current']] == evidence[oids['current']]

        # Exercise the actual verifier with the two naive key-set choices too.
        historical_keys = [keys[n].with_suffix('.pub').read_text() for n in ('A_old', 'A_new')]
        assert oids['backdated'] in SshCommitVerifier(historical_keys).verify_history(repo, oids['backdated'])
        try:
            SshCommitVerifier([keys['A_new'].with_suffix('.pub').read_text()]).verify_history(repo, oids['current'])
        except UnknownSignerError as error:
            assert error.commit == oids['old']
        else:
            raise AssertionError('current-only verifier accepted the removed ancestor')
        wrong_scope = {**records, 'berth': 'B'}
        assert bounded_policy(wrong_scope, oids['old'], claims(oids['old']), fps['A_old'])[0] == 'reject'
        reconsidered = {**records, 'accepted_history': {}}
        assert bounded_policy(reconsidered, oids['old'], claims(oids['old']), fps['A_old'])[0] == 'reject'
        assert before['old']['current_only']['verdict'] == 'reject'
        assert before['backdated']['historical_union']['verdict'] == 'accept'
        assert before['backdated']['bounded_contextual']['verdict'] == 'reject'
        assert before['no_basis']['bounded_contextual']['verdict'] == 'pause'
        assert before['unknown_basis']['bounded_contextual']['verdict'] == 'pause'
        assert before['ambiguous_basis']['bounded_contextual']['verdict'] == 'pause'

        return {
            'git_version': git('--version').stdout.strip(),
            'signature_evidence_independent_of_policy': signature_evidence,
            'tampered_commit_control': tamper_result,
            'accepted_history_record': records['accepted_history'],
            'judgments': before,
            'actual_key_set_baselines': {
                'historical_union_accepts_backdated': True,
                'current_only_rejects_current_head_due_to_old_ancestor': True},
            'acceptance_scope_and_reconsideration': {
                'another_berth_rejected': True, 'withdrawn_acceptance_rejected': True},
            'after_local_view_change': {
                'change': 'local records now remove A_new within berth A',
                'current': after['current'],
                'signature_evidence_unchanged': True,
            },
        }


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, sort_keys=True))
