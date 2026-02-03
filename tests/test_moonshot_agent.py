"""
Tests unitaires pour NEW_MOONSHOT_AIAgent (Kimi K2.5)
"""
import pytest
import json
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import asyncio


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_openai_client():
    """Mock du client OpenAI."""
    client = Mock()
    client.chat = Mock()
    client.chat.completions = Mock()
    return client


@pytest.fixture
def mock_async_openai_client():
    """Mock du client AsyncOpenAI."""
    client = AsyncMock()
    client.chat = AsyncMock()
    client.chat.completions = AsyncMock()
    return client


@pytest.fixture
def moonshot_agent(mock_openai_client, mock_async_openai_client):
    """Fixture pour NEW_MOONSHOT_AIAgent avec clients mockés."""
    with patch('app.llm.klk_agents.get_secret', return_value='test_api_key'):
        with patch('app.llm.klk_agents.OpenAI', return_value=mock_openai_client):
            with patch('app.llm.klk_agents.AsyncOpenAI', return_value=mock_async_openai_client):
                from app.llm.klk_agents import NEW_MOONSHOT_AIAgent
                agent = NEW_MOONSHOT_AIAgent()
                agent.client = mock_openai_client
                agent.client_stream = mock_async_openai_client
                yield agent
                agent.reset_token_counters()


@pytest.fixture
def base_agent_with_moonshot(mock_openai_client, mock_async_openai_client):
    """Fixture pour BaseAIAgent avec Moonshot enregistré."""
    with patch('app.llm.klk_agents.get_secret', return_value='test_api_key'):
        with patch('app.llm.klk_agents.OpenAI', return_value=mock_openai_client):
            with patch('app.llm.klk_agents.AsyncOpenAI', return_value=mock_async_openai_client):
                from app.llm.klk_agents import BaseAIAgent, NEW_MOONSHOT_AIAgent, ModelProvider, ModelSize

                base = BaseAIAgent()
                moonshot = NEW_MOONSHOT_AIAgent()
                moonshot.client = mock_openai_client
                moonshot.client_stream = mock_async_openai_client

                base.register_provider(ModelProvider.MOONSHOT_AI, moonshot, ModelSize.MEDIUM)
                base.default_provider = ModelProvider.MOONSHOT_AI

                yield base, moonshot


@pytest.fixture
def sample_tools():
    """Outils de test au format Anthropic."""
    return [
        {
            "name": "get_weather",
            "description": "Obtient la météo pour une ville donnée",
            "input_schema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "La ville"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                },
                "required": ["city"]
            }
        }
    ]


@pytest.fixture
def sample_tools_openai_format():
    """Outils de test au format OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Obtient la météo pour une ville donnée",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "La ville"},
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                    },
                    "required": ["city"],
                    "additionalProperties": False
                }
            }
        }
    ]


@pytest.fixture
def mock_response_text():
    """Mock d'une réponse texte simple."""
    response = Mock()
    response.model = "kimi-k2.5"
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = "Voici ma réponse"
    response.choices[0].message.reasoning_content = None
    response.choices[0].message.tool_calls = None

    response.usage = Mock()
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 50
    response.usage.prompt_tokens_details = None
    response.usage.completion_tokens_details = None

    return response


@pytest.fixture
def mock_response_with_thinking():
    """Mock d'une réponse avec thinking."""
    response = Mock()
    response.model = "kimi-k2.5"
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = "Voici ma réponse finale"
    response.choices[0].message.reasoning_content = "Analysons le problème..."
    response.choices[0].message.tool_calls = None

    response.usage = Mock()
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 150

    # Tokens détaillés avec reasoning_tokens
    response.usage.prompt_tokens_details = Mock()
    response.usage.prompt_tokens_details.cached_tokens = 20

    response.usage.completion_tokens_details = Mock()
    response.usage.completion_tokens_details.reasoning_tokens = 80

    return response


@pytest.fixture
def mock_response_with_tool_call():
    """Mock d'une réponse avec appel d'outil."""
    response = Mock()
    response.model = "kimi-k2.5"
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = None
    response.choices[0].message.reasoning_content = ""

    # Configuration du tool call
    tool_call = Mock()
    tool_call.function = Mock()
    tool_call.function.name = "get_weather"
    tool_call.function.arguments = '{"city": "Paris", "unit": "celsius"}'
    response.choices[0].message.tool_calls = [tool_call]

    response.usage = Mock()
    response.usage.prompt_tokens = 120
    response.usage.completion_tokens = 30
    response.usage.prompt_tokens_details = None
    response.usage.completion_tokens_details = None

    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - process_text
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoonshotProcessText:
    """Tests pour la méthode process_text."""

    def test_process_text_without_thinking(self, moonshot_agent, mock_response_text):
        """Test process_text sans thinking activé."""
        moonshot_agent.client.chat.completions.create.return_value = mock_response_text

        result = moonshot_agent.process_text(
            content="Bonjour, comment vas-tu?",
            model_name="kimi-k2.5",
            thinking=False
        )

        assert result == "Voici ma réponse"
        moonshot_agent.client.chat.completions.create.assert_called_once()

        # Vérifier les paramètres d'appel
        call_kwargs = moonshot_agent.client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "kimi-k2.5"
        assert call_kwargs["temperature"] == 0.6
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["thinking"]["type"] == "disabled"

    def test_process_text_with_thinking(self, moonshot_agent, mock_response_with_thinking):
        """Test process_text avec thinking activé."""
        moonshot_agent.client.chat.completions.create.return_value = mock_response_with_thinking

        result = moonshot_agent.process_text(
            content="Explique-moi la relativité",
            model_name="kimi-k2.5",
            thinking=True
        )

        # Doit retourner le texte final (pas le thinking)
        assert result == "Voici ma réponse finale"

        # Vérifier les paramètres pour thinking
        call_kwargs = moonshot_agent.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 1.0
        assert call_kwargs["top_p"] == 0.95
        assert "extra_body" not in call_kwargs

    def test_process_text_updates_chat_history(self, moonshot_agent, mock_response_text):
        """Test que process_text met à jour l'historique."""
        moonshot_agent.client.chat.completions.create.return_value = mock_response_text

        moonshot_agent.process_text(
            content="Test message",
            thinking=False
        )

        # Doit avoir 2 messages: user + assistant
        assert len(moonshot_agent.chat_history) == 2
        assert moonshot_agent.chat_history[0]["role"] == "user"
        assert moonshot_agent.chat_history[1]["role"] == "assistant"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - process_tool_use
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoonshotProcessToolUse:
    """Tests pour la méthode process_tool_use."""

    def test_process_tool_use_format(self, moonshot_agent, mock_response_with_tool_call, sample_tools_openai_format):
        """Test process_tool_use avec format d'outils correct."""
        moonshot_agent.client.chat.completions.create.return_value = mock_response_with_tool_call

        result = moonshot_agent.process_tool_use(
            content="Quelle est la météo à Paris?",
            tools=sample_tools_openai_format,
            model_name="kimi-k2.5",
            tool_mapping={"get_weather": None},
            thinking=False
        )

        # Doit retourner les arguments de l'outil
        assert "tool_output" in str(result) or isinstance(result, dict)

    def test_process_tool_use_executes_function(self, moonshot_agent, mock_response_with_tool_call, sample_tools_openai_format):
        """Test que process_tool_use exécute la fonction mappée."""
        moonshot_agent.client.chat.completions.create.return_value = mock_response_with_tool_call

        # Mock de la fonction
        mock_weather_func = Mock(return_value={"temperature": 22, "condition": "sunny"})
        tool_mapping = {"get_weather": mock_weather_func}

        result = moonshot_agent.process_tool_use(
            content="Météo à Paris",
            tools=sample_tools_openai_format,
            tool_mapping=tool_mapping,
            thinking=False
        )

        # La fonction doit avoir été appelée
        mock_weather_func.assert_called_once_with(city="Paris", unit="celsius")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - process_vision
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoonshotProcessVision:
    """Tests pour la méthode process_vision."""

    def test_process_vision_builds_content_correctly(self, moonshot_agent, mock_response_text, tmp_path):
        """Test que process_vision construit le contenu multimodal."""
        moonshot_agent.client.chat.completions.create.return_value = mock_response_text

        # Créer un fichier image temporaire
        test_image = tmp_path / "test.jpg"
        test_image.write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100)  # JPEG header

        result = moonshot_agent.process_vision(
            text="Décris cette image",
            local_files=[str(test_image)],
            thinking=False
        )

        assert result == "Voici ma réponse"

        # Vérifier que les messages contiennent le bon format
        call_kwargs = moonshot_agent.client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        assert len(messages) > 0

        # Le dernier message user doit contenir une liste avec image_url et text
        last_user_msg = [m for m in messages if m["role"] == "user"][-1]
        assert isinstance(last_user_msg["content"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - Token Capture
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoonshotTokenCapture:
    """Tests pour la capture des tokens."""

    def test_token_capture_basic(self, moonshot_agent, mock_response_text):
        """Test capture des tokens basique."""
        moonshot_agent.update_token_usage(mock_response_text)

        tokens = moonshot_agent.get_total_tokens()

        assert "kimi-k2.5" in tokens
        assert tokens["kimi-k2.5"]["total_input_tokens"] == 100
        assert tokens["kimi-k2.5"]["total_output_tokens"] == 50

    def test_token_capture_with_thinking(self, moonshot_agent, mock_response_with_thinking):
        """Test capture des tokens avec thinking (reasoning_tokens)."""
        moonshot_agent.update_token_usage(mock_response_with_thinking)

        tokens = moonshot_agent.get_total_tokens()

        assert "kimi-k2.5" in tokens
        # Input tokens = prompt_tokens - cached_tokens = 100 - 20 = 80
        assert tokens["kimi-k2.5"]["total_input_tokens"] == 80
        assert tokens["kimi-k2.5"]["total_output_tokens"] == 150
        assert tokens["kimi-k2.5"]["total_cached_tokens"] == 20
        assert tokens["kimi-k2.5"]["total_thought_tokens"] == 80

    def test_reset_token_counters(self, moonshot_agent, mock_response_text):
        """Test réinitialisation des compteurs."""
        moonshot_agent.update_token_usage(mock_response_text)
        assert len(moonshot_agent.token_usage) > 0

        moonshot_agent.reset_token_counters()

        assert len(moonshot_agent.token_usage) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - Streaming
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoonshotStreaming:
    """Tests pour les méthodes streaming."""

    @pytest.mark.asyncio
    async def test_streaming_text(self, moonshot_agent):
        """Test streaming texte simple."""
        # Créer des chunks mockés
        chunk1 = Mock()
        chunk1.choices = [Mock()]
        chunk1.choices[0].delta = Mock()
        chunk1.choices[0].delta.content = "Bonjour"
        chunk1.choices[0].delta.reasoning_content = None

        chunk2 = Mock()
        chunk2.choices = [Mock()]
        chunk2.choices[0].delta = Mock()
        chunk2.choices[0].delta.content = " monde!"
        chunk2.choices[0].delta.reasoning_content = None

        # Mock du stream async
        async def mock_stream():
            yield chunk1
            yield chunk2

        moonshot_agent.client_stream.chat.completions.create = AsyncMock(
            return_value=mock_stream()
        )

        chunks = []
        async for chunk in moonshot_agent.moonshot_send_message_streaming(
            content="Test",
            model_name="kimi-k2.5",
            thinking=False
        ):
            chunks.append(chunk)

        # Vérifier les chunks
        text_chunks = [c for c in chunks if c.get("type") == "text_chunk"]
        assert len(text_chunks) == 2
        assert text_chunks[0]["chunk"] == "Bonjour"
        assert text_chunks[1]["chunk"] == " monde!"

    @pytest.mark.asyncio
    async def test_streaming_with_thinking(self, moonshot_agent):
        """Test streaming avec thinking."""
        # Chunk de thinking
        chunk_thinking = Mock()
        chunk_thinking.choices = [Mock()]
        chunk_thinking.choices[0].delta = Mock()
        chunk_thinking.choices[0].delta.content = None
        chunk_thinking.choices[0].delta.reasoning_content = "Analysons..."

        # Chunk de texte
        chunk_text = Mock()
        chunk_text.choices = [Mock()]
        chunk_text.choices[0].delta = Mock()
        chunk_text.choices[0].delta.content = "Réponse finale"
        chunk_text.choices[0].delta.reasoning_content = None

        async def mock_stream():
            yield chunk_thinking
            yield chunk_text

        moonshot_agent.client_stream.chat.completions.create = AsyncMock(
            return_value=mock_stream()
        )

        chunks = []
        async for chunk in moonshot_agent.moonshot_send_message_streaming(
            content="Test",
            model_name="kimi-k2.5",
            thinking=True
        ):
            chunks.append(chunk)

        # Vérifier qu'on a à la fois thinking et text
        thinking_chunks = [c for c in chunks if c.get("type") == "thinking_chunk"]
        text_chunks = [c for c in chunks if c.get("type") == "text_chunk"]

        assert len(thinking_chunks) == 1
        assert thinking_chunks[0]["chunk"] == "Analysons..."
        assert len(text_chunks) == 1
        assert text_chunks[0]["chunk"] == "Réponse finale"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - BaseAIAgent Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaseAIAgentIntegration:
    """Tests d'intégration avec BaseAIAgent."""

    def test_provider_registered(self, base_agent_with_moonshot):
        """Test que le provider est bien enregistré."""
        from app.llm.klk_agents import ModelProvider

        base, moonshot = base_agent_with_moonshot

        assert ModelProvider.MOONSHOT_AI in base.provider_instances
        assert base.default_provider == ModelProvider.MOONSHOT_AI

    def test_model_available(self, base_agent_with_moonshot):
        """Test que les modèles sont disponibles."""
        from app.llm.klk_agents import ModelProvider, ModelSize

        base, moonshot = base_agent_with_moonshot

        # Vérifier que kimi-k2.5 est dans provider_models
        assert ModelProvider.MOONSHOT_AI in base.provider_models
        assert ModelSize.MEDIUM in base.provider_models[ModelProvider.MOONSHOT_AI]
        assert "kimi-k2.5" in base.provider_models[ModelProvider.MOONSHOT_AI][ModelSize.MEDIUM]

    def test_transform_tools_for_moonshot(self, base_agent_with_moonshot, sample_tools):
        """Test transformation des outils pour Moonshot."""
        from app.llm.klk_agents import ModelProvider

        base, moonshot = base_agent_with_moonshot

        transformed = base._transform_tools_for_provider(sample_tools, ModelProvider.MOONSHOT_AI)

        # Doit être au format OpenAI
        assert len(transformed) == 1
        assert transformed[0]["type"] == "function"
        assert transformed[0]["function"]["name"] == "get_weather"
        assert "parameters" in transformed[0]["function"]


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - Chat History
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoonshotChatHistory:
    """Tests pour la gestion de l'historique."""

    def test_add_user_message(self, moonshot_agent):
        """Test ajout message utilisateur."""
        moonshot_agent.add_user_message("Hello")

        assert len(moonshot_agent.chat_history) == 1
        assert moonshot_agent.chat_history[0]["role"] == "user"
        assert moonshot_agent.chat_history[0]["content"] == "Hello"

    def test_add_ai_message(self, moonshot_agent):
        """Test ajout message assistant."""
        moonshot_agent.add_ai_message("Hi there!")

        assert len(moonshot_agent.chat_history) == 1
        assert moonshot_agent.chat_history[0]["role"] == "assistant"

    def test_update_system_prompt(self, moonshot_agent):
        """Test mise à jour du system prompt."""
        moonshot_agent.update_system_prompt("Tu es un assistant.")

        assert len(moonshot_agent.chat_history) == 1
        assert moonshot_agent.chat_history[0]["role"] == "system"

        # Mettre à jour devrait remplacer
        moonshot_agent.update_system_prompt("Nouveau prompt")
        assert len(moonshot_agent.chat_history) == 1
        assert moonshot_agent.chat_history[0]["content"] == "Nouveau prompt"

    def test_flush_chat_history_keeps_system(self, moonshot_agent):
        """Test flush conserve le system prompt."""
        moonshot_agent.update_system_prompt("System")
        moonshot_agent.add_user_message("User")
        moonshot_agent.add_ai_message("AI")

        assert len(moonshot_agent.chat_history) == 3

        moonshot_agent.flush_chat_history()

        assert len(moonshot_agent.chat_history) == 1
        assert moonshot_agent.chat_history[0]["role"] == "system"

    def test_flush_all_chat_history(self, moonshot_agent):
        """Test flush complet."""
        moonshot_agent.update_system_prompt("System")
        moonshot_agent.add_user_message("User")

        moonshot_agent.flush_all_chat_history()

        assert len(moonshot_agent.chat_history) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - Error Handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestMoonshotErrorHandling:
    """Tests pour la gestion des erreurs."""

    def test_process_text_handles_exception(self, moonshot_agent):
        """Test que process_text gère les exceptions."""
        moonshot_agent.client.chat.completions.create.side_effect = Exception("API Error")

        result = moonshot_agent.process_text(
            content="Test",
            thinking=False
        )

        assert result is None

    def test_process_vision_handles_missing_file(self, moonshot_agent, mock_response_text):
        """Test process_vision avec fichier manquant."""
        moonshot_agent.client.chat.completions.create.return_value = mock_response_text

        # Le fichier n'existe pas, mais la méthode doit continuer
        result = moonshot_agent.process_vision(
            text="Test",
            local_files=["/nonexistent/file.jpg"],
            thinking=False
        )

        # Doit quand même retourner quelque chose (juste le texte)
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
