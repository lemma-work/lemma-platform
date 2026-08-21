import re
import os
from typing import List, Tuple


def apply_edits(content: str, edit_string: str) -> str:
    """
    Apply search/replace edits to a file using git merge conflict syntax.

    Args:
        file_path: Path to the file to edit
        edit_string: String containing one or more edit blocks in format:
                    <<<<<<< SEARCH
                    [text to find]
                    =======
                    [text to replace with]
                    >>>>>>> REPLACE

    Returns:
        Updated file content as string

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If search text not found or edit format is invalid
    """
    # Read the original file
    # Parse edit blocks using regex
    # Allow for empty replacement content (no newline required before >>>>>>> REPLACE)
    edit_pattern = r"<<<<<<< SEARCH\s*\n(.*?)\n=======\s*\n(.*?)>>>>>>> REPLACE"
    edits = re.findall(edit_pattern, edit_string, re.DOTALL)

    if not edits:
        raise ValueError("No valid edit blocks found in edit_string")

    # Apply each edit sequentially
    for search_text, replace_text in edits:
        # Strip trailing newlines/whitespace from replace_text for consistent handling
        replace_text = replace_text.rstrip("\n\r\t ")

        # Handle special case for end-of-file operations
        if search_text.strip() == "<<<END_OF_FILE>>>":
            # Add newline before appended content if original file doesn't end with newline
            if content and not content.endswith("\n"):
                content = content + "\n" + replace_text
            else:
                content = content + replace_text
            continue

        # Handle special case for start-of-file operations
        if search_text.strip() == "<<<START_OF_FILE>>>":
            # Add newline after prepended content if it doesn't end with one
            if replace_text and not replace_text.endswith("\n"):
                replace_text += "\n"

            content = replace_text + content
            continue

        # Regular search and replace
        if search_text not in content:
            raise ValueError(
                f"Search text not found in file: {repr(search_text[:100])}"
            )

        # Count occurrences to ensure uniqueness
        occurrences = content.count(search_text)
        if occurrences > 1:
            raise ValueError(
                f"Search text appears {occurrences} times in file. Must be unique: {repr(search_text[:100])}"
            )

        # Perform the replacement
        content = content.replace(search_text, replace_text, 1)

    return content


def apply_edits_to_file(file_path: str, edit_string: str) -> None:
    """
    Apply edits and write back to the file.

    Args:
        file_path: Path to the file to edit
        edit_string: Edit string in search/replace format
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    updated_content = apply_edits(content, edit_string)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(updated_content)
    return updated_content


# Example usage and helper for parsing multiple edit blocks
def parse_edit_blocks(edit_string: str) -> List[Tuple[str, str]]:
    """
    Parse edit string and return list of (search, replace) tuples.
    Useful for debugging and validation.
    """
    edit_pattern = r"<<<<<<< SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>> REPLACE"
    return re.findall(edit_pattern, edit_string, re.DOTALL)


if __name__ == "__main__":
    # Example usage
    sample_edit = """
<<<<<<< SEARCH
def old_function():
    return "old"
=======
def new_function():
    return "new"
>>>>>>> REPLACE

<<<<<<< SEARCH
<<<END_OF_FILE>>>
=======

# Added at end of file
def bonus_function():
    pass
>>>>>>> REPLACE
"""

    print("Parsed edit blocks:")
    for i, (search, replace) in enumerate(parse_edit_blocks(sample_edit)):
        print(f"Edit {i + 1}:")
        print(f"  Search: {repr(search[:50])}")
        print(f"  Replace: {repr(replace[:50])}")
