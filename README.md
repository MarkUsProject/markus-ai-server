# markus-ai-server

markus-ai-server runs model prompts for MarkUs. It passes each prompt to a local
model (Ollama or llama.cpp) and returns the answer.

Every request needs an API key. The server checks the key before it runs the prompt.
It records each failed check as an audit event. Standing rules read those events and
send an email when one address fails the key too many times. This catches brute-force
and spray attacks.

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) to set it up, configure it, and test it.
For a plain-language, step-by-step test from the MarkUs UI, see
[docs/markus-testing-guide.md](docs/markus-testing-guide.md).

## Developers

Install the project with its development dependencies:

```console
$ uv sync
```

(or `pip install -e . --group dev` with pip 25.1+; `dev` is a dependency group, not an extra)

Install the pre-commit hooks:

```console
$ pre-commit install
```

Run the tests:

```console
$ pytest
```
