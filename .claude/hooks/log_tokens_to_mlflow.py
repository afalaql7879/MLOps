"""Log Claude Code token usage to MLflow.

One MLflow run per Claude Code session (experiment "claude-code-usage"),
updated after every turn by a Claude Code Stop hook. Metrics are cumulative
totals for the session; the step is the number of API calls so far, so the
metric charts show how usage grew over the session.

Usage:
  As a hook:   reads the hook JSON from stdin (session_id, transcript_path, cwd)
  Backfill:    python log_tokens_to_mlflow.py --backfill   (all past sessions)

Env overrides:
  CLAUDE_USAGE_TRACKING_URI   default: sqlite:///<repo>/mlflow.db
  CLAUDE_USAGE_EXPERIMENT     default: claude-code-usage
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKING_URI = os.environ.get(
    "CLAUDE_USAGE_TRACKING_URI", f"sqlite:///{(REPO_ROOT / 'mlflow.db').as_posix()}"
)
EXPERIMENT = os.environ.get("CLAUDE_USAGE_EXPERIMENT", "claude-code-usage")
PROJECTS_DIR = Path.home() / ".claude" / "projects"
LOG_FILE = Path.home() / ".claude" / "mlflow-usage-hook.log"

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _log_error(msg: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def summarize_transcript(transcript: Path) -> dict:
    """Aggregate token usage from a session transcript (+ its subagent transcripts)."""
    files = [transcript]
    sub_dir = transcript.with_suffix("") / "subagents"
    if sub_dir.is_dir():
        files += sorted(sub_dir.glob("*.jsonl"))

    # A single API response is written as several lines (one per content block)
    # that share message.id and repeat the same usage -> keep one per id.
    by_msg: dict[str, tuple[str, dict]] = {}
    first_ts = last_ts = None
    first_prompt = None
    branch = cwd = None
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("timestamp")
            if ts:
                first_ts = min(first_ts or ts, ts)
                last_ts = max(last_ts or ts, ts)
            branch = branch or d.get("gitBranch")
            cwd = cwd or d.get("cwd")
            msg = d.get("message") or {}
            if (
                first_prompt is None
                and path == transcript
                and d.get("type") == "user"
                and isinstance(msg.get("content"), str)
                and not msg["content"].startswith("<")
            ):
                first_prompt = msg["content"]
            if d.get("type") != "assistant" or not isinstance(msg.get("usage"), dict):
                continue
            model = msg.get("model") or "unknown"
            if model == "<synthetic>":
                continue
            by_msg[msg.get("id") or f"{path}:{len(by_msg)}"] = (model, msg["usage"])

    totals: dict[str, float] = defaultdict(float)
    per_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for model, u in by_msg.values():
        for k in TOKEN_FIELDS:
            v = u.get(k) or 0
            totals[k] += v
            per_model[model][k] += v
        totals["thinking_tokens"] += (u.get("output_tokens_details") or {}).get(
            "thinking_tokens", 0
        ) or 0
        stu = u.get("server_tool_use") or {}
        totals["web_search_requests"] += stu.get("web_search_requests", 0) or 0
        totals["web_fetch_requests"] += stu.get("web_fetch_requests", 0) or 0
        per_model[model]["api_calls"] += 1

    totals["total_tokens"] = sum(totals[k] for k in TOKEN_FIELDS)
    totals["total_input_tokens"] = (
        totals["input_tokens"]
        + totals["cache_creation_input_tokens"]
        + totals["cache_read_input_tokens"]
    )
    totals["cache_hit_ratio"] = (
        totals["cache_read_input_tokens"] / totals["total_input_tokens"]
        if totals["total_input_tokens"]
        else 0.0
    )
    totals["api_calls"] = len(by_msg)

    return {
        "totals": dict(totals),
        "per_model": {m: dict(v) for m, v in per_model.items()},
        "first_ts": first_ts,
        "last_ts": last_ts,
        "first_prompt": first_prompt,
        "git_branch": branch,
        "cwd": cwd,
    }


def _iso_to_ms(ts: str | None) -> int | None:
    if not ts:
        return None
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def log_session(client, experiment_id: str, session_id: str, transcript: Path) -> bool:
    from mlflow.entities import Metric, RunTag

    s = summarize_transcript(transcript)
    if not s["totals"].get("api_calls"):
        return False

    existing = client.search_runs(
        [experiment_id],
        filter_string=f"tags.session_id = '{session_id}'",
        max_results=1,
    )
    start_ms = _iso_to_ms(s["first_ts"])
    end_ms = _iso_to_ms(s["last_ts"]) or int(time.time() * 1000)
    if existing:
        run_id = existing[0].info.run_id
    else:
        prompt = (s["first_prompt"] or "").strip().replace("\n", " ")
        run_name = (prompt[:60] + "…") if len(prompt) > 60 else (prompt or session_id[:8])
        run_id = client.create_run(
            experiment_id, start_time=start_ms, run_name=run_name
        ).info.run_id

    project = Path(s["cwd"]).name if s["cwd"] else transcript.parent.name
    models = sorted(s["per_model"])
    tags = {
        "session_id": session_id,
        "project": project,
        "cwd": s["cwd"] or "",
        "git_branch": s["git_branch"] or "",
        "models": ", ".join(models),
        "primary_model": max(
            models, key=lambda m: s["per_model"][m].get("output_tokens", 0)
        ),
        "first_prompt": (s["first_prompt"] or "")[:4000],
        "transcript_path": str(transcript),
    }
    step = int(s["totals"]["api_calls"])
    metrics = [Metric(k, float(v), end_ms, step) for k, v in s["totals"].items()]
    for model, vals in s["per_model"].items():
        for k, v in vals.items():
            metrics.append(Metric(f"{model}/{k}", float(v), end_ms, step))

    client.log_batch(run_id, metrics=metrics, tags=[RunTag(k, v) for k, v in tags.items()])
    client.set_terminated(run_id, status="FINISHED", end_time=end_ms)
    return True


# ---------------------------------------------------------------------------
# Traces: one trace per user turn, with an LLM span per API call (carrying
# model + token usage, which feeds the GenAI Dashboard) and a span per tool call.
# ---------------------------------------------------------------------------

MAX_TEXT = 10_000
TURN_ID_KEY = "claude_code.turn_id"


def _iso_to_ns(ts: str | None) -> int | None:
    ms = _iso_to_ms(ts)
    return ms * 1_000_000 if ms is not None else None


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _text_of(content) -> str:
    parts = []
    for b in _blocks(content):
        if b.get("type") == "text":
            parts.append(b.get("text") or "")
        elif b.get("type") == "tool_result":
            parts.append(_text_of(b.get("content")))
    return "\n".join(p for p in parts if p)


def _is_prompt(d: dict) -> bool:
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
        return False
    blocks = _blocks((d.get("message") or {}).get("content"))
    return bool(blocks) and not any(b.get("type") == "tool_result" for b in blocks)


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return out


def parse_turns(transcript: Path) -> list[dict]:
    entries = _read_jsonl(transcript)
    sub_dir = transcript.with_suffix("") / "subagents"
    sub_entries = []
    if sub_dir.is_dir():
        for p in sorted(sub_dir.glob("*.jsonl")):
            sub_entries += [dict(e, _subagent=p.stem) for e in _read_jsonl(p)]

    turns: list[dict] = []
    tool_results: dict[str, tuple[str, str | None]] = {}

    def add_llm(turn, d):
        msg = d["message"]
        mid = msg.get("id") or d.get("uuid")
        call = turn["calls"].get(mid)
        if call is None:
            call = turn["calls"][mid] = {
                "model": msg.get("model") or "unknown",
                "usage": msg.get("usage") or {},
                "start": d.get("timestamp"),
                "end": d.get("timestamp"),
                "blocks": [],
                "subagent": d.get("_subagent") or (d.get("isSidechain") and "sidechain"),
            }
        call["end"] = d.get("timestamp") or call["end"]
        call["usage"] = msg.get("usage") or call["usage"]
        call["blocks"] += _blocks(msg.get("content"))

    for d in entries:
        if _is_prompt(d):
            turns.append({
                "id": d.get("uuid"),
                "prompt": _text_of(d["message"].get("content")),
                "start": d.get("timestamp"),
                "cwd": d.get("cwd"),
                "branch": d.get("gitBranch"),
                "calls": {},
            })
        elif not turns:
            continue
        elif d.get("type") == "assistant" and isinstance((d.get("message") or {}).get("usage"), dict):
            if (d["message"].get("model") or "") != "<synthetic>":
                add_llm(turns[-1], d)
        if d.get("type") == "user":
            for b in _blocks((d.get("message") or {}).get("content")):
                if b.get("type") == "tool_result":
                    tool_results[b.get("tool_use_id")] = (_text_of(b.get("content")), d.get("timestamp"))

    # Attach subagent API calls to the turn that was running at the time.
    starts = [t["start"] or "" for t in turns]
    for d in sub_entries:
        if d.get("type") == "assistant" and isinstance((d.get("message") or {}).get("usage"), dict):
            ts = d.get("timestamp") or ""
            idx = max((i for i, s in enumerate(starts) if s <= ts), default=None)
            if idx is not None:
                add_llm(turns[idx], d)
        if d.get("type") == "user":
            for b in _blocks((d.get("message") or {}).get("content")):
                if b.get("type") == "tool_result":
                    tool_results[b.get("tool_use_id")] = (_text_of(b.get("content")), d.get("timestamp"))

    for t in turns:
        t["tool_results"] = tool_results
    return [t for t in turns if t["calls"]]


def log_traces(experiment_id: str, session_id: str, transcript: Path, skip_recent_s: int = 0) -> int:
    import mlflow
    from mlflow.entities import SpanType
    from mlflow.tracing.constant import SpanAttributeKey, TraceMetadataKey
    from mlflow.claude_code.tracing import _build_usage_dict

    turns = parse_turns(transcript)
    if not turns:
        return 0
    # Skip the newest turn if it may still be in progress (backfill only).
    if skip_recent_s and time.time() - transcript.stat().st_mtime < skip_recent_s:
        turns = turns[:-1]

    done = set()
    for tr in mlflow.search_traces(
        locations=[experiment_id],
        filter_string=f"metadata.`{TraceMetadataKey.TRACE_SESSION}` = '{session_id}'",
        return_type="list",
        include_spans=False,
    ):
        tid = tr.info.trace_metadata.get(TURN_ID_KEY) or tr.info.tags.get(TURN_ID_KEY)
        if tid:
            done.add(tid)

    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    n = 0
    for t in turns:
        if t["id"] in done:
            continue
        calls = sorted(t["calls"].values(), key=lambda c: c["start"] or "")
        end_ts = max(c["end"] or "" for c in calls)
        final = next(
            (_text_of([b for b in c["blocks"] if b.get("type") == "text"])
             for c in reversed(calls) if not c["subagent"] and any(
                 b.get("type") == "text" and b.get("text") for b in c["blocks"])),
            "",
        )
        project = Path(t["cwd"]).name if t["cwd"] else transcript.parent.name
        root = mlflow.start_span_no_context(
            name="claude_code_turn",
            span_type=SpanType.AGENT,
            inputs={"prompt": t["prompt"][:MAX_TEXT]},
            experiment_id=experiment_id,
            start_time_ns=_iso_to_ns(t["start"]),
            metadata={
                TraceMetadataKey.TRACE_SESSION: session_id,
                TraceMetadataKey.TRACE_USER: user,
                TURN_ID_KEY: t["id"],
                "mlflow.trace.working_directory": t["cwd"] or "",
            },
            tags={"project": project, "git_branch": t["branch"] or "",
                  "models": ", ".join(sorted({c["model"] for c in calls}))},
        )
        for c in calls:
            span = mlflow.start_span_no_context(
                name=f"{'subagent ' if c['subagent'] else ''}llm:{c['model']}",
                span_type=SpanType.CHAT_MODEL,
                parent_span=root,
                inputs={"model": c["model"]},
                start_time_ns=_iso_to_ns(c["start"]),
                attributes={
                    SpanAttributeKey.MODEL: c["model"],
                    SpanAttributeKey.MODEL_PROVIDER: "anthropic",
                    SpanAttributeKey.CHAT_USAGE: _build_usage_dict(c["usage"]),
                },
            )
            out = [
                {"text": b.get("text", "")[:MAX_TEXT]} if b.get("type") == "text"
                else {"tool_use": b.get("name"), "input": b.get("input")}
                for b in c["blocks"] if b.get("type") in ("text", "tool_use")
            ]
            for b in c["blocks"]:
                if b.get("type") != "tool_use":
                    continue
                result, rts = t["tool_results"].get(b.get("id"), (None, None))
                tool = mlflow.start_span_no_context(
                    name=b.get("name") or "tool",
                    span_type=SpanType.TOOL,
                    parent_span=root,
                    inputs=b.get("input"),
                    start_time_ns=_iso_to_ns(c["end"]),
                )
                tool.set_outputs((result or "")[:MAX_TEXT])
                tool.end(end_time_ns=_iso_to_ns(rts or c["end"]))
            span.set_outputs(out)
            span.end(end_time_ns=_iso_to_ns(c["end"]))
        root.set_outputs({"response": final[:MAX_TEXT]})
        root.end(end_time_ns=_iso_to_ns(end_ts))
        n += 1
    try:
        mlflow.flush_trace_async_logging()
    except Exception:
        pass
    return n


def _client():
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow
    from mlflow import MlflowClient

    mlflow.set_tracking_uri(TRACKING_URI)
    client = MlflowClient()
    exp = client.get_experiment_by_name(EXPERIMENT)
    exp_id = exp.experiment_id if exp else client.create_experiment(EXPERIMENT)
    return client, exp_id


def backfill() -> None:
    client, exp_id = _client()
    n = 0
    for transcript in sorted(PROJECTS_DIR.glob("*/*.jsonl")):
        try:
            if log_session(client, exp_id, transcript.stem, transcript):
                n += 1
                t = log_traces(exp_id, transcript.stem, transcript, skip_recent_s=120)
                print(f"logged {transcript.parent.name}/{transcript.stem} (+{t} traces)")
        except Exception as e:  # keep going on bad transcripts
            print(f"skipped {transcript}: {e}", file=sys.stderr)
    print(f"Done: {n} sessions logged to {TRACKING_URI} (experiment '{EXPERIMENT}')")


def worker(session_id: str, transcript: str) -> None:
    try:
        client, exp_id = _client()
        log_session(client, exp_id, session_id, Path(transcript))
        log_traces(exp_id, session_id, Path(transcript))
    except Exception as e:
        _log_error(f"session {session_id}: {e!r}")


def hook_entry() -> None:
    """Called by Claude Code. Hands off to a background process so the
    (slow) MLflow import never delays Claude Code."""
    try:
        data = json.load(sys.stdin)
        session_id, transcript = data["session_id"], data["transcript_path"]
    except Exception as e:
        _log_error(f"bad hook input: {e!r}")
        return
    args = [sys.executable, str(Path(__file__).resolve()), "--worker", session_id, transcript]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen(
                args, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs
            )
            return
        except OSError:
            kwargs["creationflags"] = base
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2 and sys.argv[1] == "--backfill":
        backfill()
    else:
        hook_entry()
