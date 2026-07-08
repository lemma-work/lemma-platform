from functools import lru_cache

import tiktoken


@lru_cache(maxsize=8)
def _get_encoding(encoding_name: str):
    """Cache tiktoken encodings; building one is non-trivial and they're reused.

    ``num_tokens_from_string`` / ``prefix_by_token`` are CPU-bound over the whole
    input string, so callers on the worker event loop must dispatch them via
    ``app.core.concurrency.offload.run_blocking`` to avoid stalling the loop.
    """
    return tiktoken.get_encoding(encoding_name)


def num_tokens_from_string(string: str, encoding_name: str = "cl100k_base") -> int:
    """Returns the number of tokens in a text string."""
    encoding = _get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def prefix_by_token(text: str, max_tokens: int, encoding_name: str = "cl100k_base") -> str:
    """Split a text into chunks of max_tokens."""
    tokenizer = _get_encoding(encoding_name)
    # Tokenize the input string
    tokens = tokenizer.encode(text)
    if len(tokens) <= max_tokens:
        return text
    # Select the first `num_tokens` tokens
    selected_tokens = tokens[:max_tokens]

    # Decode the selected tokens back to a string
    substring = tokenizer.decode(selected_tokens)
    substring += f"... (truncated to {max_tokens} tokens out of {len(tokens)} tokens). Please read by passing line range or use file_processor tool or python tool to extract targeted data from large file."
    return substring


