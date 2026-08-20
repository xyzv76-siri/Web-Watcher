# Pipeline Authority Matrix — Phase 20-02

## Scheduler (`scheduled_runner.py`)
- **Can:** schedule / claim / orchestrate target execution, release leases, dispatch adapters
- **Cannot:** directly create Signal/Event/Notification; implement business state machine; bypass `claim_targets`; bypass `finalize_execution`

## Fetcher / Adapter (`fetcher.py`, `generic_web_target.py`, `github_target.py`)
- **Can:** observe resources, return `FetchResult` / `TargetExecutionResult` / `GitHubTargetExecutionResult`, compute `ObservationResult`
- **Cannot:** directly persist downstream Signal/Event/Notification; bypass `Repository`; bypass `FetchPolicy`; bypass `finalize_execution`

## FetchPolicy (`fetch_policy.py`)
- **Can:** decide retry / backoff / cooldown / `next_allowed_at` based on HTTP/fetch outcome
- **Cannot:** generate business events; bypass `Repository`

## Repository (`repository.py`)
- **Can:** persistence boundary, transaction, fencing, serialization/normalization, `finalize_execution`
- **Cannot:** perform Fetch business logic; bypass adapter results

## EventCorrelator (`event_correlator.py`)
- **Can:** produce `CorrelationPlan` from Signals
- **Cannot:** bypass atomic persistence (`finalize_execution` / `commit_plan`)

## InvestigationWorker (`investigation_worker.py`)
- **Can:** process only persisted Events; retry/backoff with persisted state
- **Cannot:** repeat producing uncontrolled side effects; process unpersisted Events

## Notification (`notification_dispatcher.py`, `channel_senders.py`)
- **Can:** last-mile delivery of persisted Notifications
- **Cannot:** be called directly by Fetcher/Adapter; bypass `Repository`
