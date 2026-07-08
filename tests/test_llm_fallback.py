import pytest
from unittest.mock import MagicMock, patch
from vp.config import ModelSpec
from vp.llm import anthropic_message


@pytest.fixture
def dummy_spec():
    return ModelSpec(
        purpose="script_generation",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key="sk-dummy-key",
        params={"temperature": 0.9}
    )


@patch("anthropic.Anthropic")
def test_anthropic_succeeds_directly(mock_anthropic, dummy_spec):
    # Mock client and message completion
    mock_client = MagicMock()
    mock_anthropic.return_value = mock_client
    
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(type="text", text="Direct Anthropic Response")]
    mock_msg.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.messages.create.return_value = mock_msg

    res = anthropic_message(dummy_spec, system="sys", user="user")
    
    assert res == "Direct Anthropic Response"
    mock_client.messages.create.assert_called_once()


@patch("anthropic.Anthropic")
@patch("groq.Groq")
def test_anthropic_fails_groq_succeeds(mock_groq, mock_anthropic, dummy_spec):
    # Anthropic client fails
    mock_anthropic_client = MagicMock()
    mock_anthropic.return_value = mock_anthropic_client
    mock_anthropic_client.messages.create.side_effect = RuntimeError("Anthropic rate limit")

    # Groq client succeeds
    mock_groq_client = MagicMock()
    mock_groq.return_value = mock_groq_client
    
    mock_chunk1 = MagicMock()
    mock_chunk1.choices = [MagicMock()]
    mock_chunk1.choices[0].delta.content = "Groq "
    
    mock_chunk2 = MagicMock()
    mock_chunk2.choices = [MagicMock()]
    mock_chunk2.choices[0].delta.content = "Fallback Response"
    
    # Mock usage in last chunk
    mock_chunk3 = MagicMock()
    mock_chunk3.choices = []
    mock_chunk3.usage = MagicMock(prompt_tokens=15, completion_tokens=25)

    mock_groq_client.chat.completions.create.return_value = [mock_chunk1, mock_chunk2, mock_chunk3]

    res = anthropic_message(dummy_spec, system="sys", user="user")
    
    assert res == "Groq Fallback Response"
    mock_anthropic_client.messages.create.assert_called_once()
    mock_groq_client.chat.completions.create.assert_called_once()


@patch("groq.Groq")
def test_anthropic_missing_key_groq_succeeds(mock_groq):
    # Spec with no api key
    missing_key_spec = ModelSpec(
        purpose="script_generation",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key=None,
        params={"temperature": 0.9}
    )

    mock_groq_client = MagicMock()
    mock_groq.return_value = mock_groq_client
    
    mock_chunk1 = MagicMock()
    mock_chunk1.choices = [MagicMock()]
    mock_chunk1.choices[0].delta.content = "Groq Direct Fallback"
    mock_chunk1.usage = None

    mock_groq_client.chat.completions.create.return_value = [mock_chunk1]

    # Patch os.environ to simulate GROQ_API_KEY set
    with patch.dict("os.environ", {"GROQ_API_KEY": "gsk-dummy"}):
        res = anthropic_message(missing_key_spec, system="sys", user="user")
        
    assert res == "Groq Direct Fallback"
    mock_groq_client.chat.completions.create.assert_called_once()


@patch("anthropic.Anthropic")
@patch("groq.Groq")
@patch("vp.llm._openrouter_text")
def test_anthropic_and_groq_fail_openrouter_succeeds(mock_or, mock_groq, mock_anthropic, dummy_spec):
    # Anthropic fails
    mock_anthropic_client = MagicMock()
    mock_anthropic.return_value = mock_anthropic_client
    mock_anthropic_client.messages.create.side_effect = RuntimeError("Anthropic down")

    # Groq fails
    mock_groq_client = MagicMock()
    mock_groq.return_value = mock_groq_client
    mock_groq_client.chat.completions.create.side_effect = RuntimeError("Groq down")

    # OpenRouter succeeds
    mock_or.return_value = ("OpenRouter Fallback", "openai/gpt-oss-120b:free")

    with patch.dict("os.environ", {"GROQ_API_KEY": "gsk-dummy"}):
        res = anthropic_message(dummy_spec, system="sys", user="user")

    assert res == "OpenRouter Fallback"
    mock_or.assert_called_once_with("sys", "user")


@patch("anthropic.Anthropic")
@patch("groq.Groq")
@patch("vp.llm._openrouter_text")
def test_all_fail_raises_exception(mock_or, mock_groq, mock_anthropic, dummy_spec, capsys):
    # Anthropic fails
    mock_anthropic_client = MagicMock()
    mock_anthropic.return_value = mock_anthropic_client
    mock_anthropic_client.messages.create.side_effect = RuntimeError("Anthropic auth failed")

    # Groq fails
    mock_groq_client = MagicMock()
    mock_groq.return_value = mock_groq_client
    mock_groq_client.chat.completions.create.side_effect = RuntimeError("Groq auth failed")

    # OpenRouter fails
    mock_or.side_effect = RuntimeError("OpenRouter down")

    with patch.dict("os.environ", {"GROQ_API_KEY": "gsk-dummy"}):
        with pytest.raises(RuntimeError, match="Anthropic auth failed"):
            anthropic_message(dummy_spec, system="sys", user="user")

    # Ensure warning or API error sentinel is printed
    captured = capsys.readouterr()
    assert "[vp] [API_ERROR:LLM_ALL_FAILED]" in captured.out or "[vp] [API_ERROR:ANTHROPIC_KEY_INVALID]" in captured.out
