import re

tests = [
    ## Single line code block
    "```python\nprint('Hello, World!')\n```",

    ## Multi-line code block
    "```python\nprint('Hello, World!')\nprint('Hello, World!')\n```",

    ## Multi-line code block with leading/trailing newlines
    "\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n```\n",

    ## Multi-line code block with leading/trailing newlines and leading spaces
    "\n```python\n  print('Hello, World!')\n  print('Hello, World!')\n```\n",

    ## Multi-line code block with text before
    "Hello, World!\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n```",

    ## Multi-line code with text before and after
    "Hello, World!\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n```\nGoodbye, World!",

    ## Multi-line code with traling backticks missing
    "Hello, World!\n```python\nprint('Hello, World!')\nprint('Hello, World!')\n",
]


def strip_code(message):
    return re.sub("```.*?(?:```\n*|$)", "", message, flags=re.DOTALL)

for test in tests:
    stripped = strip_code(test)
    assert stripped.find("```") == -1, f"Failed to strip code block from {test}"
    assert stripped.find("print") == -1, f"Failed to strip code block from {test}"
