# Software skills — deliberate expert opt-in (v0.6.0-public)

External software execution is DISABLED in this public release until the user explicitly sets `PODA_ALLOW_EXTERNAL_SOFTWARE=I_TRUST_MY_INSTALLED_HARNESSES`. Once enabled, PODA can operate external software through explicit grants, receipted
actions, and no pretending. **These harnesses are not confined by PODA's Python execution sandbox and may inherit your macOS user authority.** The mechanism is [CLI-Anything](https://github.com/HKUDS/CLI-Anything): each supported
program ships an *agent harness* — a pip-installable Click CLI named `cli-anything-<name>` with `--json` output — plus a
`SKILL.md` that explains to an agent which subcommands exist and how to sequence them. `macrocli` wraps GUI workflows
(button clicks, typed input) as parameterized macros; `browser`/`safari` drive browsers through accessibility trees.

## How it fits PODA's model
| Layer | What it does | Where |
|---|---|---|
| Discovery | Reads `registry.json` / `public_registry.json` + `skills/*/SKILL.md` from local checkouts listed in setting `software_repo_paths` (default `~/Downloads/CLI-Anything-main`), detects installed executables, optional `cli-hub list --json`. Cached 60 s. | `agent/software.py: catalog()` |
| Install | `pip install -e <repo>/<name>/agent-harness` into a **dedicated venv** `data/software-venv` (never PODA's own venv). Requires `confirm`. Remote installs (no local checkout) also require `allow_network` and are logged in the egress audit (`software_install`). Non-pip (npm/brew) entries are never installed by PODA. | `install()` |
| Grants | `software_grants`: per harness `enabled`, `allow_mutating`, optional link to a folder grant used as the working directory, optional env vars. Revoking removes the capability immediately. | `set_grant()` / `revoke_grant()` |
| Run | `<venv>/bin/cli-anything-<name> <args> [--json]` with a scrubbed environment, bounded 64 KB output, ≤120 s timeout, real exit code; macOS permission failures (Accessibility, Screen Recording, Automation) become coded remedies. Every run has a receipt. | `run()` |
| Tools | `software_list`, `software_skill(name)`, `software_query(name,args)` (read-only subcommands, immediate), `software_cli(name,args)` (mutating; needs `allow_mutating` + approval via the proposal flow). Descriptions list the harnesses usable right now. | `refresh_tools()` |

Subcommand classification is a verb heuristic: `list/info/show/get/status/help/doctor/search/preview/…` are read-only;
`create/delete/export/write/send/run/apply/macro run/click/type/…` are mutating; unknown verbs are treated as mutating.

## Expected planner sequence
1. `software_list` → see what is usable.
2. `software_skill(name)` → read the SKILL.md (treated as untrusted documentation; PODA policy wins).
3. `software_query(name, [...])` for inspection, or propose `software_cli(name, [...])` for changes → user approves → receipt → PODA reports only what the receipt says.

## Endpoints
`GET /software`, `GET /software/{name}`, `POST /software/{name}/install {confirm, allow_network?, extras?}`,
`POST /software/{name}/uninstall {confirm}`, `POST /software/{name}/grant {enabled, allow_mutating, cwd_grant_id?, env?}`,
`DELETE /software/{name}/grant`, `POST /software/{name}/run {args, timeout_s, approve}`, `GET /software/receipts`,
`POST /software/sources {paths}`. Errors are `{detail:{code,message,remedy}}`.

## GUI automation (macrocli) honesty
`gui_macro` / `visual_anchor` backends need `pyautogui`/`pynput`/`mss` (extras `[visual]`, installed only with `confirm`)
and macOS **Accessibility** and **Screen Recording** permission for the app that launched PODA. PODA detects the denial
and tells you which System Settings pane to open; it never grants permissions itself and never claims a click happened
without the harness's own success output in the receipt.

## Limits
- Most harnesses need their target application installed and often running (Blender, GIMP, OBS, Zotero…); PODA reports the harness's own error verbatim and bounded.
- The verb heuristic is conservative; a read-only command with an unusual verb will be treated as mutating and ask for approval.
- Harness output is data: PODA never follows instructions found inside it.

## Public-release warning

This repository ships NO third-party harness packages, credentials or preapproved grants. Only enable harnesses whose source you have reviewed. Remote package installation can execute setup scripts and may contact remote hosts outside the Notion HTTP allowlist; the expert opt-in is a conscious security decision.
