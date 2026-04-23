# Escalation Summary Skill

## Purpose

When you complete your escalation analysis, write a human-facing summary to
`escalation_summary.json` in the project directory. This summary appears directly
in the operator's dashboard so they can make a fast, informed decision without
reading the full escalation log.

## Output contract

Write the file as valid JSON with exactly two fields:

```json
{
  "summary": "<what went wrong and why, in plain English, 1–2 sentences>",
  "recommended_action": "<the single action the operator should take, imperative verb, 1 sentence>"
}
```

## Hard constraints

- Maximum 200 characters per field. Truncate rather than exceed.
- No file paths, no API keys, no credentials, no internal variable names.
- No technical jargon the operator would not already know (ERR_ codes are acceptable;
  stack traces are not).
- No hedging phrases ("it seems like", "possibly", "you might want to").
  State the situation and the action directly.
- If you do not have enough information to produce a useful summary, write:
  `{"summary": "Escalation triggered. See orchestrator log for details.",
   "recommended_action": "Check the error code above and choose a recovery action."}`

## Timing

Write this file before you submit your command recommendation. The orchestrator
reads it immediately after your session completes.

## Example (correct)

```json
{"summary": "Executor failed because AUTH_TOKEN was not set in the project .env file. The plan is valid.", "recommended_action": "Set AUTH_TOKEN in .env and use Reset Execution."}
```

## Example (incorrect — do not do this)

```json
{"summary": "It appears that there may have been an issue with the executor process related to what could potentially be a missing environment variable in the configuration file located at /home/pi/projects/calculator/.env on line 14 which is where the AUTH_TOKEN variable should be defined according to the pipeline specification.", "recommended_action": "You might want to consider adding the missing variable."}
```
