# S6: Codex hooks and sandbox

Measured 2026-09-17 with codex-cli 0.154.0; generated hooks rechecked in the
real smortboard-card:multi-lab image on 2026-09-18.

Mounting `hooks.json` into `CODEX_HOME` invoked PreToolUse. The patch payload was:

```json
{"tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\n*** Update File: /workspace/docs/test.txt\n@@\n-original\n+outside\n*** End Patch"},"cwd":"/workspace","hook_event_name":"PreToolUse"}
```

Shell payloads used `tool_name=Bash` and `tool_input.command`.
The old lease hook ignored patch text and allowed both the patch outside the
lease and a relative shell write. The Codex wrapper now parses patch paths,
including moves, and checks shell commands against the declared command policy.
Local subprocess tests check its exit codes and refusal text.

The generated adapter hooks passed the container probe: src/test.txt changed
from original to inside; docs/test.txt remained original; a shell attempt to
create docs/shell.txt left no file. Both LEASE_CONFLICT and BASH_ESCAPE refusals
appeared in JSONL and the run exited successfully. The post-run git diff checks committed paths
against the worker attempt's starting commit, including both sides of a rename.
Prior base merges are excluded. Failed or interrupted checks retain their baseline
on retry so an unauthorized edit must be removed or its lease approved.

Docker's default seccomp policy blocked the nested sandbox with
`bwrap: No permissions to create a new namespace`. Legacy Landlock also failed.
A credential-free namespace probe isolated seccomp; the successful tool run used
the default policy with clone, unshare, setns, mount, umount2 and pivot_root
allowed. Capabilities stayed dropped, no-new-privileges stayed enabled, and
Codex stayed in workspace-write mode. The shipped policy records its source
and license in `smortboard/labs/NOTICE`.
