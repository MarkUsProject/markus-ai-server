# markus-ai-server

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
$ uv run pytest
```

### Running with Docker

Copy `.env.example` to `.env` and adjust `AI_SERVER_PORT` / `REDIS_PORT` if the
defaults clash with something else on your machine:

```console
$ cp .env.example .env
```

Build the images and start the stack:

```console
$ docker compose build
$ docker compose up -d
```

The server is then reachable at `http://localhost:5001` (or whichever
`AI_SERVER_PORT` you set in `.env`). Ollama runs inside the `ai-server`
container itself, with the `smollm2:135m-instruct-q2_K` model baked in at
build time, so no separate pull step is needed.

Requests must include a valid `X-API-KEY` header; generate one for a
client against the running container:

```console
$ docker compose exec ai-server uv run python -c "from markus_ai_server import generate_api_key; print(generate_api_key('dev'))"
```

Keys are stored in Redis as `api-key:<key>` -> `<client name>` and aren't
printed anywhere else, so list the existing keys straight from Redis if you
need to retrieve one you already generated:

```console
$ docker compose exec redis redis-cli -p 6380 --scan --pattern 'api-key:*'
```

(replace `6380` with whichever `REDIS_PORT` you set in `.env`)

Each result is `api-key:<key>` — the part after the prefix is the value to
send in the `X-API-KEY` header. To see which client a given key belongs to:

```console
$ docker compose exec redis redis-cli -p 6380 GET 'api-key:<key>'
```

Send a test chat request (using the baked-in default model):

```console
$ curl http://localhost:5001/chat -H "X-API-KEY: <key>" -F "content=Say hi in one word."
```

Check container status and logs:

```console
$ docker compose ps
$ docker compose logs -f ai-server
```

Stop the stack:

```console
$ docker compose down
```
