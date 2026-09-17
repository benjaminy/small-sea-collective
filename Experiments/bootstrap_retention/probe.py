"""Separate retained commit evidence from unavailable historical file content."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from cod_sync.repo import Repo
from cod_sync.verify import SshCommitVerifier, VerificationUnavailableError


def run():
    with tempfile.TemporaryDirectory(prefix='bootstrap-retention-') as tmp:
        root = Path(tmp)
        env = os.environ.copy()
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                   GIT_TERMINAL_PROMPT='0')
        # Repo uses this process environment, so isolate its Git configuration too.
        os.environ.update({k: env[k] for k in
                           ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM', 'GIT_TERMINAL_PROMPT')})
        def command(*args, check=True):
            return subprocess.run(args, cwd=root, env=env, text=True,
                                  capture_output=True, check=check)
        def git(*args, check=True):
            return command('git', *args, check=check)
        git('init', '-q')
        git('config', 'user.name', 'Retention probe')
        git('config', 'user.email', 'probe@example.invalid')
        key = root / 'signer'
        command('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
        git('config', 'gpg.format', 'ssh')
        git('config', 'user.signingkey', str(key))
        git('config', 'commit.gpgsign', 'true')
        git('config', 'gc.auto', '0')
        (root/'data.txt').write_text('old content\n')
        git('add', 'data.txt');git('commit', '-qm', 'old')
        parent = git('rev-parse', 'HEAD').stdout.strip()
        old_tree = git('rev-parse', 'HEAD^{tree}').stdout.strip()
        old_blob = git('rev-parse', 'HEAD:data.txt').stdout.strip()
        (root/'data.txt').write_text('new content\n')
        git('add', 'data.txt');git('commit', '-qm', 'new')
        head = git('rev-parse', 'HEAD').stdout.strip()
        repo = Repo(root/'.git', root)
        verifier = SshCommitVerifier([key.with_suffix('.pub').read_text()])
        baseline = verifier.verify_history(repo, head)
        assert set(baseline) == {parent, head}
        output = {'control': {'verified_commits': len(baseline)},
                  'git_version': git('--version').stdout.strip()}
        for name, oid in [('old_blob', old_blob), ('old_tree', old_tree), ('parent_commit', parent)]:
            obj = root/'.git'/'objects'/oid[:2]/oid[2:]
            saved = obj.read_bytes();obj.unlink()
            try:
                content = git('show', parent + ':data.txt', check=False)
                assert content.returncode != 0, name
                try:
                    evidence = verifier.verify_history(repo, head)
                except VerificationUnavailableError as error:
                    assert name == 'parent_commit', (name, str(error))
                    output[name] = {'verification': 'unavailable', 'content_available': False,
                                    'error': str(error)}
                else:
                    assert name != 'parent_commit'
                    assert evidence == baseline
                    output[name] = {'verification': 'both commit signatures verified',
                                    'content_available': False}
            finally:
                obj.write_bytes(saved)
            assert verifier.verify_history(repo, head) == baseline
            assert git('show', parent + ':data.txt').stdout == 'old content\n'
        return output


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, sort_keys=True))
