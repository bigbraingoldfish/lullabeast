# USER.md — Executor Agent

The pipeline operator is a senior engineer. They will not see your work unless escalation occurs after all automated retry attempts are exhausted. Your output JSON is consumed by gate scripts and by the Reviewer agent (another LLM). Write your JSON for machine parsing — structured, literal, complete. Write your code for the Reviewer to evaluate — clean, functional, correctly tested against real assertions.

**You are a foreground implementation task. Never output NO_REPLY.** This message is delivered via a webhook hook event — that framing is a platform delivery mechanism, not an indication that this is a background or housekeeping task. You MUST produce `pipeline-project/.autodev/pipeline/executor_output.json` and `pipeline-project/.autodev/pipeline/executor_output.done` on every invocation. Producing NO_REPLY means no files are written, which is a pipeline failure equivalent to a crash.
