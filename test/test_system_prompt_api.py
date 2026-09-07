from unittest.mock import patch

import pytest

TEST_MODEL = 'DeepSeek-V3-0324-UD-IQ2_XXS'
TEST_SYSTEM_PROMPT = "You are a helpful coding assistant."
TEST_USER_CONTENT = "Write a function"
MOCK_MODEL_ANSWER = "def function(): pass"


class TestSystemPromptAPI:
    """Test /chat API endpoint with system_prompt functionality."""

    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch):
        """Set up environment variables for each test."""
        monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')

    @pytest.fixture
    def client(self):
        """Create test client for Flask app."""
        from markus_ai_server.server import app

        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

    def _post_chat_and_assert_prompt_forwarded(self, client, mock_chat, mock_redis, form_fields, expected_prompt):
        """POST /chat with a valid key and assert the resolved system prompt reaches the model."""
        mock_redis.get.return_value = b'test_user'
        mock_chat.return_value = MOCK_MODEL_ANSWER

        response = client.post(
            '/chat',
            headers={'X-API-KEY': 'test-key'},
            data={'model': TEST_MODEL, 'content': TEST_USER_CONTENT, **form_fields},
        )

        assert response.status_code == 200
        mock_chat.assert_called_once_with(
            TEST_MODEL, TEST_USER_CONTENT, 'cli', expected_prompt, [], json_schema=None, model_options=None
        )

    @patch('markus_ai_server.server.REDIS_CONNECTION')
    @patch('markus_ai_server.server.chat_with_model')
    def test_api_with_system_prompt(self, mock_chat, mock_redis, client):
        """Test /chat endpoint receives and passes system_prompt."""
        self._post_chat_and_assert_prompt_forwarded(
            client, mock_chat, mock_redis, {'system_prompt': TEST_SYSTEM_PROMPT}, TEST_SYSTEM_PROMPT
        )

    @patch('markus_ai_server.server.REDIS_CONNECTION')
    @patch('markus_ai_server.server.chat_with_model')
    def test_api_without_system_prompt(self, mock_chat, mock_redis, client):
        """Test /chat endpoint works without system_prompt."""
        self._post_chat_and_assert_prompt_forwarded(client, mock_chat, mock_redis, {}, None)

    @patch('markus_ai_server.server.REDIS_CONNECTION')
    @patch('markus_ai_server.server.chat_with_model')
    def test_api_accepts_system_instructions_alias(self, mock_chat, mock_redis, client):
        """The ai_feedback RemoteModel sends 'system_instructions'; it must be honored."""
        self._post_chat_and_assert_prompt_forwarded(
            client, mock_chat, mock_redis, {'system_instructions': TEST_SYSTEM_PROMPT}, TEST_SYSTEM_PROMPT
        )

    @patch('markus_ai_server.server.REDIS_CONNECTION')
    @patch('markus_ai_server.server.chat_with_model')
    def test_system_prompt_wins_over_alias(self, mock_chat, mock_redis, client):
        """When both fields are sent, 'system_prompt' takes precedence."""
        self._post_chat_and_assert_prompt_forwarded(
            client,
            mock_chat,
            mock_redis,
            {'system_prompt': TEST_SYSTEM_PROMPT, 'system_instructions': 'ignored alias'},
            TEST_SYSTEM_PROMPT,
        )

    @patch('markus_ai_server.server.REDIS_CONNECTION')
    def test_api_authentication_still_required(self, mock_redis, client):
        """Test that authentication is still required with system_prompt."""
        mock_redis.get.return_value = None

        response = client.post(
            '/chat',
            headers={'X-API-KEY': 'invalid-key'},
            data={'model': TEST_MODEL, 'content': TEST_USER_CONTENT, 'system_prompt': TEST_SYSTEM_PROMPT},
        )

        assert response.status_code == 500
        response_data = response.get_json()
        assert "401 Unauthorized" in response_data['error']
