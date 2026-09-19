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

Rechecked 2026-09-18: an intentionally invalid API key produced a failed run
whose normalized result had auth_failed=true. No real credential was revoked.

A real OpenAI gpt-5.6-sol worker created and committed greeting.txt in an
isolated repo. The committed path lease check passed, the network-disabled
container test gate exited zero, and a named-profile Anthropic Haiku reviewer
approved the structured review with no findings. Stored event identities
correctly distinguished the OpenAI worker and Anthropic reviewer. OpenAI cost
remained unknown because no price was configured. The fixture did not publish
a throwaway pull request; normal PR behavior remains covered by integration tests.

The first worker attempt exposed Codex's read-only Git metadata default.
Writable workers now explicitly grant /workspace/.git inside their disposable
clone; workspace-write remains enabled. A credential-free sandbox probe proved
git add/commit works while /home/agent/outside-probe and .codex/forbidden remain
denied. Read-only roles receive no additional writable root.
