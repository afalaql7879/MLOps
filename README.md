# mlflow-demo

A small MLflow project for *MLOps: Operationalizing Machine Learning at Scale*. Runs are tracked in a local SQLite store (`sqlite:///mlflow.db`); `first_run.py` logs a first example run.

```bash
python first_run.py
mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000
```

## Claude Code skills and agents

Everything lives under [`.claude/`](.claude) and is committed to git, so anyone who clones the repo gets the same setup.

```
.claude/
├── agents/
│   └── experiment-reviewer.md      # own
└── skills/
    ├── mlflow-log-run/             # own
    ├── skill-creator/              # installed (anthropics/skills)
    └── webapp-testing/             # installed (anthropics/skills)
```

### Installed skills

Source repository: <https://github.com/anthropics/skills> (Apache 2.0, licence file kept in each skill folder)
Commit: [`1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064`](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064) (2026-11-14)
Installed: 2026-11-15, copied unmodified from `skills/<name>/` at that commit.

| Skill | Source URL | Why it's here | Review note |
|---|---|---|---|
| `webapp-testing` | [skills/webapp-testing](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064/skills/webapp-testing) | Drive and screenshot the MLflow UI (`mlflow ui`) with Playwright to check runs show up. | Read all 4 scripts. `scripts/with_server.py` starts the given server command with `shell=True`, waits for the port on `localhost`, runs your command, then terminates the servers. It only runs commands passed to it on the command line, so treat its arguments like any shell command. The `examples/` are templates: they write to `/mnt/user-data/outputs` and `/tmp` (Linux paths), so change those paths before using them on Windows. No network calls other than to `localhost`, no credential access. Needs `pip install playwright` and `playwright install chromium`. |
| `skill-creator` | [skills/skill-creator](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064/skills/skill-creator) | Write and test our own project skills (like `mlflow-log-run`) and tune their descriptions. | Read every script, focusing on subprocess, file and network calls. `run_eval.py` / `improve_description.py` shell out to `claude -p` (uses your Claude Code login and spends usage) and `run_eval.py` writes and then deletes a temporary file in `.claude/commands/`. `eval-viewer/generate_review.py` serves a review page on `127.0.0.1` only, opens your browser, and tries to free its port by killing whatever listens on it via `lsof` (absent on Windows, so it just prints a note). `generate_report.py` HTML pulls Google Fonts. `package_skill.py` only zips a skill folder. No secrets read, no uploads. **Windows caveat:** `run_eval.py` uses `select.select()` on a pipe, which fails on Windows, so the trigger-eval loop needs WSL; the authoring guidance in `SKILL.md` works everywhere. |

Considered but not installed: `mcp-builder` (no MCP server planned yet), `doc-coauthoring` (instructions only; not needed now).

### Own skills and agents

| Name | Type | File | What it does |
|---|---|---|---|
| `mlflow-log-run` | Skill | [.claude/skills/mlflow-log-run/SKILL.md](.claude/skills/mlflow-log-run/SKILL.md) | Project conventions for logging a run to `sqlite:///mlflow.db`: set the tracking URI and experiment first, what to log as params/metrics/tags, how to verify the run and open the UI. Never deletes runs or logs secrets. |
| `experiment-reviewer` | Agent | [.claude/agents/experiment-reviewer.md](.claude/agents/experiment-reviewer.md) | Read-only subagent (Sonnet; Read/Grep/Glob/Bash) that compares runs in `mlflow.db`, picks the best run per metric, flags gaps (missing metrics, no `git_commit` tag, failed runs) and suggests next experiments. |

### Checking what Claude Code sees

Skills and agents are loaded when a session starts, so open a new Claude Code session in this folder and ask:

> List the skills and agents available in this project.

Expected project entries: skills `mlflow-log-run`, `skill-creator`, `webapp-testing`; agent `experiment-reviewer`. In the CLI, `/agents` also shows the agent.

### Updating an installed skill

```bash
git clone https://github.com/anthropics/skills.git /tmp/anthropic-skills
git -C /tmp/anthropic-skills log -1 --format=%H
```

Read the diff of the skill's folder since the commit above, copy it over `.claude/skills/<name>/`, and update the commit and review note in this table.
