# Engineering Workflows

The repository includes a local specification-to-implementation workflow
runner in `graphrag/engineering_workflows/`. It composes existing repository
quality gates and extension points without pretending that an LLM subagent
runtime exists where one is not configured.

## What It Provides

- YAML workflow specifications with validated task IDs and acyclic dependencies.
- An allowlisted skill registry for linting, unit tests, integration tests and
  the full test suite.
- Injectable agent handlers for future specialist subagents. An agent must be
  explicitly registered by the host process; unknown agents fail closed.
- Async lifecycle hooks for workflow start, task completion/failure, approval
  requests and workflow completion/failure.
- Atomic JSON state under `.workflows/runs/`, allowing an interrupted run to be
  resumed without repeating completed tasks.
- Argument-list command execution with no implicit shell, bounded output and a
  per-task timeout.
- An approval gate for explicit approval tasks and commands that contain Git
  mutation operations such as `git commit` or `git push`.

## Example

The sample workflow is [workflows/example.yaml](../workflows/example.yaml):

```powershell
python scripts/run_engineering_workflow.py workflows/example.yaml
```

The first invocation runs the safe validation tasks and stops at
`waiting_approval`. The returned JSON includes `run_id`. Resume it only after a
human has inspected the outputs:

```powershell
python scripts/run_engineering_workflow.py workflows/example.yaml `
  --run-id <run-id> --resume --approve
```

The runner never performs a commit or push implicitly. A future workflow may
declare those actions, but they remain blocked until the explicit approval flag
is supplied.

## Extension Model

`SkillRegistry.register()` adds a safe skill backed by a command builder or an
async handler. `SkillRegistry.register_agent()` adds a host-owned specialist
agent. `HookRegistry.register()` attaches observability, policy or notification
behavior to a lifecycle event.

This gives the project a stable foundation for custom skills, specialist
subagents and automated engineering processes. Native Codex slash commands,
Codex UI hooks and account-level model selection remain Codex features; this
repository exposes equivalent local workflow contracts and a CLI entry point.

## Boundary

The current runner coordinates deterministic tools and registered handlers. It
does not generate code autonomously, open pull requests, or select an LLM by
itself. Those capabilities can be added behind the agent registry and approval
gate while keeping tests, security checks and human review explicit.
