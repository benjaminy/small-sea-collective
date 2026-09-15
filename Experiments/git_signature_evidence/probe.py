"""Observe Git signature evidence with and without signer recognition."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from cod_sync.repo import Repo
from cod_sync.verify import SshCommitVerifier, UnknownSignerError, SignatureInvalidError


def run():
    with tempfile.TemporaryDirectory(prefix='signature-evidence-') as tmp:
        root = Path(tmp)
        os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                          GIT_TERMINAL_PROMPT='0')
        def cmd(*args, binary=False, data=None):
            return subprocess.run(args, cwd=root, text=not binary, input=data,
                                  capture_output=True, check=True).stdout
        def git(*args, **kwargs):
            return cmd('git', *args, **kwargs)
        key = root/'key'
        cmd('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
        git('init', '-q')
        git('config', 'user.name', 'Signature evidence probe')
        git('config', 'user.email', 'probe@example.invalid')
        git('config', 'gpg.format', 'ssh')
        git('config', 'user.signingkey', str(key))
        git('config', 'commit.gpgsign', 'true')
        git('commit', '--allow-empty', '-qm', 'authentic work')
        head = git('rev-parse', 'HEAD').strip()
        raw = git('cat-file', 'commit', head, binary=True)
        altered = raw.replace(b'authentic work', b'altered work')
        assert altered != raw
        bad = git('hash-object', '-t', 'commit', '-w', '--stdin', binary=True, data=altered).decode().strip()
        empty = root/'empty-signers';empty.write_text('')
        known = root/'known-signers';known.write_text('local@probe.invalid ' + key.with_suffix('.pub').read_text())
        repo = Repo(root/'.git', root)
        outputs = {'git_version': git('--version').strip()}
        for name, commit, config in [('unrecognized_valid', head, empty),
                                     ('unrecognized_tampered', bad, empty),
                                     ('recognized_valid', head, known),
                                     ('recognized_tampered', bad, known)]:
            rows, diagnostics = repo.signature_report(commit, config)
            outputs[name] = {'rows': [vars(row) for row in rows], 'diagnostics': diagnostics}
        # The raw statuses are observations, not a policy API inferred from labels.
        valid_unknown = outputs['unrecognized_valid']['rows'][0]
        valid_known = outputs['recognized_valid']['rows'][0]
        assert valid_unknown['status'] == 'U' and valid_known['status'] == 'G'
        assert valid_unknown['fingerprint'] == valid_known['fingerprint']
        assert outputs['unrecognized_tampered']['rows'][0]['status'] == 'B'
        assert outputs['recognized_tampered']['rows'][0]['status'] == 'B'
        try:
            SshCommitVerifier([]).verify_history(repo, head)
        except UnknownSignerError as error:
            outputs['conservative_verifier'] = type(error).__name__
        else:
            raise AssertionError('empty set accepted a signer')
        try:
            SshCommitVerifier([]).verify_history(repo, bad)
        except SignatureInvalidError as error:
            outputs['tampered_verifier'] = type(error).__name__
        else:
            raise AssertionError('bad signature was not rejected')
        try:
            repo.signature_report(head, root/'missing-signers')
        except FileNotFoundError:
            outputs['missing_configuration'] = 'FileNotFoundError before signature judgment'
        else:
            raise AssertionError('missing configuration accepted')
        return outputs


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, sort_keys=True))
