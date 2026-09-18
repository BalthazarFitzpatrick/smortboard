# S7: Codex container credentials

Measured 2026-09-17 with codex-cli 0.154.0 in smortboard-card:multi-lab.

Sending the existing ChatGPT access token to `codex login --with-access-token`
failed with `agent identity JWT payload is not valid JSON`. This flag was not
proven to accept the normal ChatGPT OAuth access token.

Sending the compact existing login JSON over stdin succeeded: the container
returned `CONTAINER_OK`. The shell wrote it with umask 077 into `CODEX_HOME`
on tmpfs. `docker inspect` contained no credential, and the probe created no
host credential copy. The original login file stayed untouched.

The adapter therefore accepts `auth_json`, plus the API-key login path.
The UI offers ChatGPT login JSON and API key. The API retains `access_token`
for credentials compatible with that CLI flag; it is not the recommended
ChatGPT path. Named board credentials are stored separately with mode 600.

Still required: a revoked-credential probe and a full mixed-lab card run through
the test gate, reviewer and pull request. The resumed session denied access to
the Docker socket, so it could not repeat the container proof.
