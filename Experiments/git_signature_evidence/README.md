# Authentic signature versus recognized signer

#266 needs signature evidence and local authority decisions to remain separate.
This probe asks what the existing Repo.signature_report can report before the caller recognizes the signer.
It does not change the conservative SshCommitVerifier acceptance contract.

## Prediction before execution

With a deliberately empty allowed-signers file, Git should distinguish a valid but unrecognized SSH signature from a tampered signature.
The report should identify the signing fingerprint even though it authorizes nothing.
Adding the public key should change recognition while preserving the signature's cryptographic identity.
A missing configuration file must remain a setup failure, not an unknown-author verdict.

Run from the repository root:

```sh
.venv/bin/python Experiments/git_signature_evidence/probe.py
```

The probe creates only temporary local repositories and disposable keys.
It uses the existing Repo signature-report method and verifier, with all normal history checks intact.
No runtime authority evaluator or Hub API is implemented.

## Observed result

On Git 2.54.0 (Apple Git-157), the valid signature reports `U` with its fingerprint under the empty signer file and `G` with the same fingerprint under the known signer file.
Both tampered cases report `B` without a fingerprint.
The existing conservative verifier raises `UnknownSignerError` for the valid unrecognized signature and `SignatureInvalidError` for the tampered one.
The missing configuration file fails before a signature judgment.

This gives #266 a concrete candidate for separating low-level signature evidence from Manager's local authority policy.
It does not authorize accepting `U` through the existing verifier, prove a human identity, or establish berth authority.
An implementation would need to retain the current original-object and complete-ancestry checks, preserve unavailable/error states, and validate the evidence contract on its supported Git versions.
