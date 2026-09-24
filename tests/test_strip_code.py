import re
import pytest

# Mirrors ChatbotApp.strip_code (chatbot.py); kept standalone so the test
# doesn't import chatbot's heavy dependency chain.
def strip_code(message):
    return re.sub("```.*?(?:```\n*|$)", "", message, flags=re.DOTALL)

@pytest.mark.parametrize("text", [
    # Single line code block
    "```python\nprint('Hello, World!')\n```",
    # Multi-line code block
    "```python\nprint('Hello, World!')\nprint('Hello, World!')\n```",
    # Multi-line code block with leading/trailing newlines
    "\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n```\n",
    # Multi-line code block with leading/trailing newlines and leading spaces
    "\n```python\n  print('Hello, World!')\n  print('Hello, World!')\n```\n",
    # Multi-line code block with text before
    "Hello, World!\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n```",
    # Multi-line code with text before and after
    "Hello, World!\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n```\nGoodbye, World!",
    # Multi-line code with trailing backticks missing
    "Hello, World!\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n",
])
def test_strip_code(text):
    stripped = strip_code(text)
    assert "```" not in stripped
    assert "print" not in stripped
