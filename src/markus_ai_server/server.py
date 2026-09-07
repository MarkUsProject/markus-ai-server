from __future__ import annotations

import base64
import glob
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

import ollama
import requests
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request
from werkzeug.exceptions import HTTPException

from .redis_helper import REDIS_CONNECTION

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s - %(message)s')
logger = logging.getLogger('ai-server')

# Load environment variables from .env file
load_dotenv()

app = Flask('AI server')

# Configuration from environment variables
DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'deepseek-coder-v2:latest')

# Llama.cpp configuration
LLAMA_CPP_CLI = os.getenv('LLAMA_CPP_CLI', str(Path('/data1/llama.cpp/bin/llama-cli')))
GGUF_DIR = os.getenv('GGUF_DIR', str(Path('/data1/GGUF')))

# Llama server configuration
_llama_server_url = os.getenv('LLAMA_SERVER_URL')  # e.g., http://localhost:8080 or localhost:8080
LLAMA_SERVER_URL = (
    f"http://{_llama_server_url}"
    if _llama_server_url and not _llama_server_url.startswith(('http://', 'https://'))
    else _llama_server_url
)
SCHEMA_KEY = "schema"


def _build_messages(content: str, system_prompt: Optional[str] = None, image_files: Optional[list] = None) -> list:
    """Build messages list with optional system prompt."""
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': content})

    if image_files:
        messages[-1]["images"] = [base64.b64encode(image_file.read()).decode("utf-8") for image_file in image_files]
    return messages


def chat_with_llama_server_http(
    model: str,
    content: str,
    system_prompt: Optional[str] = None,
    timeout: int = 300,
    image_files: Optional[list] = None,
    json_schema: Optional[dict] = None,
    model_options: Optional[dict] = None,
) -> str:
    """Handle chat using llama-server HTTP API."""
    if not LLAMA_SERVER_URL:
        raise Exception("LLAMA_SERVER_URL environment variable not set")

    try:
        messages = _build_messages(content, system_prompt, image_files=[])  # TODO: Pass image files

        if not model_options:
            model_options = {}

        payload = {'model': model, 'messages': messages, **model_options}
        if json_schema:
            payload['json_schema'] = json_schema[SCHEMA_KEY]
        if 'stream' not in payload:
            payload['stream'] = False

        start_log_data = {'model': model}
        logger.info(f'chat_with_llama_server_http starting: {start_log_data}')
        response = requests.post(
            f'{LLAMA_SERVER_URL}/v1/chat/completions',
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=timeout,
        )

        done_log_data = {'model': model, 'response_status_code': response.status_code}
        logger.info(f'chat_with_llama_server_http done: {start_log_data}')
        if response.status_code == 200:
            data = response.json()
            if 'choices' in data and len(data['choices']) > 0:
                return data['choices'][0]['message']['content']
            else:
                raise Exception("Invalid response format from llama-server")
        else:
            raise Exception(f"Llama-server HTTP error")

    except requests.Timeout:
        raise Exception(f"Llama-server request timed out for model {model}")
    except requests.RequestException as e:
        raise Exception(f"Llama-server request failed: {str(e)}")


def resolve_model_path(model: str) -> Optional[str]:
    """Resolve model name to full GGUF file path using glob pattern."""
    pattern = os.path.join(GGUF_DIR, model, "*.gguf")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def is_llamacpp_available(model: str) -> bool:
    """Check if model is available in llama.cpp."""
    return resolve_model_path(model) is not None


def chat_with_ollama(
    model: str,
    content: str,
    system_prompt: Optional[str] = None,
    image_files: Optional[list] = None,
    json_schema: Optional[dict] = None,
    model_options: Optional[dict] = None,
) -> str:
    """Handle chat using ollama."""
    messages = _build_messages(content, system_prompt, image_files)

    start_log_data = {'model': model}
    logger.info(f'chat_with_ollama starting: {start_log_data}')
    response = ollama.chat(
        model=model,
        messages=messages,
        stream=False,
        format=json_schema[SCHEMA_KEY] if json_schema else None,
        options=model_options,
    )
    done_log_data = {
        'model': model,
        'eval_duration': response.eval_duration,
        'prompt_eval_duration': response.prompt_eval_duration,
        'eval_count': response.eval_count,
        'prompt_eval_count': response.prompt_eval_count,
    }

    logger.info(f'chat_with_ollama done: {done_log_data}')
    return response.message.content


def chat_with_llamacpp(
    model: str,
    content: str,
    system_prompt: Optional[str] = None,
    timeout: int = 300,
    image_files: Optional[list] = None,
    model_options: Optional[dict] = None,
    json_schema: Optional[dict] = None,
) -> str:
    """Handle chat using llama.cpp CLI."""
    model_path = resolve_model_path(model)

    if not model_path:
        raise ValueError(f"Model not found: {model}")

    cmd = [LLAMA_CPP_CLI, '-m', model_path, '--n-gpu-layers', '40', '-p', content, '-n', '512', '--single-turn']
    if json_schema:
        raw_schema = json_schema[SCHEMA_KEY] if SCHEMA_KEY in json_schema else json_schema
        cmd += ["--json-schema", json.dumps(raw_schema)]

    # Add system prompt if provided
    if system_prompt:
        cmd.extend(['--system-prompt', system_prompt])

    if model_options:
        for key, value in model_options.items():
            cmd.extend(['--model-option', key, value])

    if image_files:
        pass  # TODO: pass image files

    try:
        start_log_data = {'model': model}
        logger.info(f'chat_with_llamacpp starting: {start_log_data}')
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout, check=True)
        logger.info(f'chat_with_llamacpp done: {start_log_data}')

        stdout_text = result.stdout.decode('utf-8', errors='replace')

        # Strip whitespace and return the response
        response = stdout_text.strip()
        return response if response else "No response generated."

    except subprocess.TimeoutExpired:
        raise Exception(f"Llama.cpp request timed out for model {model}")
    except subprocess.CalledProcessError as e:
        stderr_text = ""
        if e.stderr:
            stderr_text = e.stderr.decode('utf-8', errors='replace')
        raise Exception(f"Llama.cpp failed for {model}: {stderr_text.strip() if stderr_text else 'Unknown error'}")
    except FileNotFoundError:
        raise Exception("Llama.cpp CLI not found")


def chat_with_model(
    model: str,
    content: str,
    llama_mode: str = "cli",
    system_prompt: Optional[str] = None,
    image_files: Optional[list] = None,
    model_options: Optional[dict] = None,
    json_schema: Optional[dict] = None,
) -> str:
    """Route chat request based on llama_mode: server (external), cli, or ollama fallback; and with optional system prompt."""
    if is_llamacpp_available(model):
        if llama_mode == "server":
            if not LLAMA_SERVER_URL:
                raise Exception("LLAMA_SERVER_URL environment variable not set for server mode")
            return chat_with_llama_server_http(
                model,
                content,
                system_prompt=system_prompt,
                image_files=image_files,
                json_schema=json_schema,
                model_options=model_options,
            )
        elif llama_mode == "cli":
            return chat_with_llamacpp(
                model,
                content,
                system_prompt=system_prompt,
                image_files=image_files,
                json_schema=json_schema,
                model_options=model_options,
            )
        else:
            raise ValueError(f"Invalid llama_mode: '{llama_mode}'. Valid options are 'server' or 'cli'.")
    else:
        # Model not available in llama.cpp, use ollama
        return chat_with_ollama(
            model,
            content,
            system_prompt=system_prompt,
            image_files=image_files,
            json_schema=json_schema,
            model_options=model_options,
        )


def authenticate() -> bytes:
    """Authenticate the given request using an API key."""
    api_key = request.headers.get('X-API-KEY')
    client_ip = request.remote_addr
    endpoint = request.path
    if not api_key:
        logger.warning(f"Missing API key from {client_ip} at {endpoint}")
        abort(401, description="Missing API key")

    user = REDIS_CONNECTION.get(f"api-key:{api_key}")
    if not user:
        logger.warning(f"Invalid API key attempt from {client_ip} at {endpoint}")
        abort(401, description="Invalid API key")

    return user


@app.route('/chat', methods=['POST'])
def chat():
    """Handle chat request with optional llama_mode and system prompt parameters."""
    authenticate()
    model = request.form.get('model', DEFAULT_MODEL)
    content = request.form.get('content', '')
    llama_mode = request.form.get('llama_mode', 'cli')
    # Accept either field name: callers send 'system_prompt', the ai_feedback
    # RemoteModel sends 'system_instructions'. Prefer the former when both exist.
    system_prompt = request.form.get('system_prompt') or request.form.get('system_instructions')
    image_files = list(request.files.values())
    model_options = request.form.get('model_options')
    json_schema = request.form.get('json_schema')
    if json_schema:
        json_schema = json.loads(json_schema)

    if not content.strip():
        abort(400, description='Missing prompt content')

    response_content = chat_with_model(
        model, content, llama_mode, system_prompt, image_files, model_options=model_options, json_schema=json_schema
    )
    return jsonify(response_content)


@app.errorhandler(Exception)
def handle_exception(error):
    # Preserve aborts (e.g. 401 from authenticate, 400 for missing content)
    # instead of collapsing every error into a 500.
    if isinstance(error, HTTPException):
        return jsonify({"error": error.description}), error.code
    return jsonify({"error": str(error)}), 500
