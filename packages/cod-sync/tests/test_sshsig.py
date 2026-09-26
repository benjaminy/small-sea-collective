"""OpenSSH interoperability micro test for SSHSIG."""

import subprocess

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from cod_sync.sshsig import sign_git_commit


def test_sshsig_verifies_with_ssh_keygen(tmp_path):
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    payload = b"git commit body\n"
    signature, public_key = sign_git_commit(payload, raw)
    signers = tmp_path / "signers"
    signers.write_text(f"test@example {public_key}\n")
    path = tmp_path / "signature"
    path.write_text(signature)
    result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers),
                             "-I", "test@example", "-n", "git", "-s", str(path)],
                            input=payload, capture_output=True)
    assert result.returncode == 0, result.stderr
    private_path = tmp_path / "key"
    private_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()
    ))
    private_path.chmod(0o600)
    openssh = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(private_path),
                              "-n", "git"], input=payload, capture_output=True, check=True)
    assert openssh.stdout.decode() == signature
