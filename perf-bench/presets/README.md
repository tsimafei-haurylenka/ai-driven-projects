# Presets

Copy `targets.example.json` to `targets.json` and edit it; `targets.json` is picked
automatically when present (the example is the fallback). Each entry supplies
`host`, `port`, `model` and optionally `scheme` and `api_key_env` (name of an
environment variable holding the API key - never put secrets in this file).
Select presets with `--target name` (repeatable) or a `targets` list in the config.
