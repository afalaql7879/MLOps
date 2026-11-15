---
name: mlflow-log-run
description: Log an experiment run to this project's local MLflow tracking store (sqlite:///mlflow.db). Use when the user wants to record parameters, metrics, tags or artifacts for a training run, add a new experiment script, or check that a run was logged in the mlflow-demo project.
---

# Log a run to the local MLflow store

This project tracks experiments in a SQLite file, `mlflow.db`, at the repo root.
Every script must point MLflow at it, or runs silently go to a `./mlruns` folder instead.

## Steps

1. Set the tracking URI and experiment **before** `start_run`:

   ```python
   import mlflow

   mlflow.set_tracking_uri("sqlite:///mlflow.db")
   mlflow.set_experiment("<experiment-name>")   # reuse an existing name where possible
   ```

2. Log inside one `with mlflow.start_run(run_name=...)` block:
   - `mlflow.log_param(s)` for inputs (hyperparameters, data version, seed).
   - `mlflow.log_metric(s)` for outputs (accuracy, loss); pass `step=` for per-epoch values.
   - `mlflow.set_tag("git_commit", <sha>)` so a run can be traced back to code.
   - `mlflow.log_artifact(path)` for plots, reports or model files.

3. Run the script from the repo root (the relative SQLite path depends on it):

   ```bash
   python <script>.py
   ```

4. Verify the run landed:

   ```bash
   python -c "import mlflow; mlflow.set_tracking_uri('sqlite:///mlflow.db'); print(mlflow.search_runs(experiment_names=['<experiment-name>'])[['run_id','status','start_time']].tail())"
   ```

5. To browse runs in the UI:

   ```bash
   mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000
   ```

## Rules

- Never delete or rewrite `mlflow.db` or runs in it without asking the user.
- Don't log secrets (API keys, tokens, connection strings) as params or tags.
- Keep param names stable across runs so they can be compared side by side.

See `first_run.py` for the minimal working example.
