from enum import Enum
from typing import Union, Optional, Any
from pydantic import BaseModel
from app.modules.agent.tools.file_entities import FileInfo, FileType

from app.core.concurrency.offload import run_blocking
from app.core.embeddings.token_counter import num_tokens_from_string, prefix_by_token
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ContentFormat(str, Enum):
    TEXT = "TEXT"
    BINARY = "BINARY"


class TextContent(BaseModel):
    content: str
    format: ContentFormat = ContentFormat.TEXT
    file_name: str


class BinaryContent(BaseModel):
    content: bytes
    format: ContentFormat = ContentFormat.BINARY
    mime_type: str
    file_name: str


MAX_TOKENS = 10000


def remove_ticks(text: str) -> str:
    """
    Get the content inside the ticks.
    """
    # based on first and last line, get the content inside the ticks.
    lines = text.split("\n")
    if lines[0].startswith("```"):
        start_index = 1
    else:
        start_index = 0
    if lines[-1].endswith("```"):
        end_index = -1
    else:
        end_index = len(lines)
    return "\n".join(lines[start_index:end_index])


class LLMFile:
    """Adapter class to prepare files for LLM consumption with appropriate formatting."""

    def __init__(self, file: FileInfo, file_manager: Any):
        self.file = file
        self.file_manager = file_manager

    async def get_content(
        self, include_line_numbers: bool = False, max_tokens: int = MAX_TOKENS
    ) -> Union[TextContent, BinaryContent]:
        """
        Get the file content in the appropriate format for LLM consumption.
        Text files are returned as TextContent, binary files as BinaryContent.

        Args:
            include_line_numbers: If True, adds line numbers to text content

        Returns:
            Formatted content with appropriate type
        """
        file_type = self.file.file_type

        # Get raw content
        raw_content = await self.file_manager.read_file(self.file.path)

        # Text-based file types should be returned as text
        text_types = [
            FileType.TEXT,
            FileType.MARKDOWN,
            FileType.PLAIN_TEXT,
            FileType.HTML,
            FileType.SVG,
            FileType.MERMAID,
            FileType.PYTHON,
            FileType.JAVASCRIPT,
            FileType.TYPESCRIPT,
            FileType.JSON,
            FileType.CSV,
        ]

        if file_type in text_types or self.file.is_text_file:
            # Ensure content is string
            if isinstance(raw_content, bytes):
                try:
                    text_content = raw_content.decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning(
                        f"Failed to decode file {self.file.path} as UTF-8, treating as binary"
                    )
                    return BinaryContent(
                        content=raw_content,
                        mime_type=self.file.mime_type,
                        file_name=self.file.path,
                    )
            else:
                text_content = raw_content

            # Format the content with filename and optional line numbers
            formatted_content = f"File: {self.file.path}\n\n"

            if include_line_numbers:
                lines = text_content.splitlines()
                numbered_lines = [f"{i + 1}: {line}" for i, line in enumerate(lines)]
                formatted_content += "\n".join(numbered_lines)
            else:
                formatted_content += text_content

            if await run_blocking(
                num_tokens_from_string, formatted_content, limiter="cpu_bound"
            ) > max_tokens:
                logger.warning(
                    f"File {self.file.path} is too long. Cutting it down to {max_tokens} tokens."
                )
                formatted_content = await run_blocking(
                    prefix_by_token, formatted_content, max_tokens, limiter="cpu_bound"
                )
            return TextContent(content=formatted_content, file_name=self.file.path)
        else:
            # Binary content
            if isinstance(raw_content, str):
                logger.warning(
                    f"Binary file {self.file.path} has string content, converting"
                )
                raw_content = raw_content.encode("utf-8")

            return BinaryContent(
                content=raw_content,
                mime_type=self.file.mime_type,
                file_name=self.file.path,
            )

    async def read_by_page(
        self,
        page_number: int,
        lines_per_page: int = 50,
        include_line_numbers: bool = False,
    ) -> Optional[TextContent]:
        """
        Read a specific page from a text file with a fixed number of lines per page.
        Returns None for binary files.

        Args:
            page_number: The page number to read (1-indexed)
            lines_per_page: Number of lines per page
            include_line_numbers: If True, adds line numbers to text content

        Returns:
            TextContent with the requested page content or None if binary
        """
        if not self.file.is_text_file:
            return None

        content = await self.file_manager.read_file(self.file.path)
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError(f"Failed to decode file {self.file.path} as UTF-8")

        lines = content.splitlines()
        start_idx = (page_number - 1) * lines_per_page
        end_idx = min(start_idx + lines_per_page, len(lines))

        if start_idx >= len(lines):
            return TextContent(
                content=f"File: {self.file.path}\n\nNo content for page {page_number}",
                file_name=self.file.path,
            )

        # Format the content with file info and optional line numbers
        formatted_content = f"File: {self.file.path} (Page {page_number}, Lines {start_idx + 1}-{end_idx})\n\n"

        if include_line_numbers:
            numbered_lines = [f"{i + 1}: {lines[i]}" for i in range(start_idx, end_idx)]
            page_content = "\n".join(numbered_lines)
        else:
            page_content = "\n".join(lines[start_idx:end_idx])

        formatted_content += page_content

        return TextContent(content=formatted_content, file_name=self.file.path)

    async def read_lines(
        self,
        start_line: int,
        end_line: int,
        include_line_numbers: bool = False,
        max_tokens: int = MAX_TOKENS,
    ) -> Optional[TextContent]:
        """
        Read specific lines from a text file (0-indexed).

        Args:
            start_line: Starting line number (inclusive)
            end_line: Ending line number (exclusive)
            include_line_numbers: If True, adds line numbers to text content

        Returns:
            TextContent with the requested lines or None if binary
        """
        if not self.file.is_text_file:
            return None

        content = await self.file_manager.read_file(self.file.path)
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError(f"Failed to decode file {self.file.path} as UTF-8")

        lines = content.splitlines()
        start_idx = max(0, start_line)
        end_idx = min(end_line, len(lines))

        if start_idx >= len(lines):
            return TextContent(
                content=f"File: {self.file.path}\n\nNo content for lines {start_line}-{end_line}",
                file_name=self.file.path,
            )

        # Format the content with file info and optional line numbers
        formatted_content = (
            f"File: {self.file.path} (Lines {start_idx + 1}-{end_idx})\n\n"
        )

        if include_line_numbers:
            numbered_lines = [f"{i + 1}: {lines[i]}" for i in range(start_idx, end_idx)]
            lines_content = "\n".join(numbered_lines)
        else:
            lines_content = "\n".join(lines[start_idx:end_idx])

        formatted_content += lines_content
        if await run_blocking(
            num_tokens_from_string, formatted_content, limiter="cpu_bound"
        ) > max_tokens:
            logger.warning(
                f"File {self.file.path} is too long. Cutting it down to {max_tokens} tokens."
            )
            formatted_content = await run_blocking(
                prefix_by_token, formatted_content, max_tokens, limiter="cpu_bound"
            )
        return TextContent(content=formatted_content, file_name=self.file.path)

    def _get_binary_file_text_content(
        self, file: FileInfo, content: bytes
    ) -> TextContent:
        size = len(content)
        return TextContent(
            file_name=file.path,
            format=ContentFormat.TEXT,
            content=f"File: {file.path}\n\n This is a binary file. Size: {size} bytes, type: {file.mime_type}",
        )

    async def get_summary(
        self, max_start_lines: int = 5, max_end_lines: int = 3, max_headings: int = 5
    ) -> Optional[TextContent]:
        """
        Generate a summary of the text file including:
        - First few lines
        - Key section headings (if detectable)
        - Last few lines
        - File metadata (name, size, total lines)

        Args:
            max_start_lines: Maximum number of lines to include from start
            max_end_lines: Maximum number of lines to include from end
            max_headings: Maximum number of headings/sections to include

        Returns:
            TextContent with summary or None if binary file
        """
        content = await self.file_manager.read_file(self.file.path)
        if not self.file.is_text_file:
            return self._get_binary_file_text_content(self.file, content)

        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                return self._get_binary_file_text_content(self.file, content)

        lines = content.splitlines()
        total_lines = len(lines)

        # Build summary
        summary = f"File Summary: {self.file.path}\n"
        summary += f"Total Lines: {total_lines}\n"
        summary += f"Size: {len(content)} bytes\n\n"

        # Include first few lines
        if total_lines > 0:
            summary += "--- Beginning of File ---\n"
            start_lines = lines[: min(max_start_lines, total_lines)]
            summary += "\n".join(start_lines)
            summary += "\n\n"

        # Try to identify and include key section headings
        if total_lines > max_start_lines:
            headings = []

            # Different patterns for different file types
            file_type = self.file.file_type

            if file_type == FileType.MARKDOWN:
                # Look for markdown headings
                for i, line in enumerate(lines):
                    if line.strip().startswith("#") and len(headings) < max_headings:
                        headings.append(f"Line {i + 1}: {line.strip()}")
            elif file_type in [
                FileType.PYTHON,
                FileType.JAVASCRIPT,
                FileType.TYPESCRIPT,
            ]:
                # Look for class and function definitions
                for i, line in enumerate(lines):
                    line_lower = line.lower().strip()
                    if len(headings) < max_headings:
                        if (
                            line_lower.startswith("def ")
                            or line_lower.startswith("class ")
                            or line_lower.startswith("function ")
                            or "export class" in line_lower
                            or "export function" in line_lower
                            or "export const" in line_lower
                        ):
                            headings.append(f"Line {i + 1}: {line.strip()}")
            elif file_type == FileType.HTML:
                # Look for HTML headings and div IDs
                for i, line in enumerate(lines):
                    line_lower = line.lower().strip()
                    if len(headings) < max_headings:
                        if (
                            "<h1" in line_lower
                            or "<h2" in line_lower
                            or "<h3" in line_lower
                            or "id=" in line_lower
                            and "<div" in line_lower
                        ):
                            headings.append(f"Line {i + 1}: {line.strip()}")

            if headings:
                summary += "--- Key Sections ---\n"
                summary += "\n".join(headings)
                summary += "\n\n"

        # Include last few lines
        if total_lines > max_start_lines:
            summary += "--- End of File ---\n"
            end_lines = lines[max(-max_end_lines, -total_lines) :]
            summary += "\n".join(end_lines)

        return TextContent(content=summary, file_name=self.file.path)

    async def get_token_optimized_content(
        self, max_tokens_estimate: int = 1000
    ) -> Optional[TextContent]:
        """
        Get a token-optimized version of the file content, useful for large files.
        This produces a useful representation that fits within token constraints.

        Args:
            max_tokens_estimate: Approximate token limit to target (rough estimate)

        Returns:
            TextContent with optimized content or None if binary
        """
        if not self.file.is_text_file:
            return None

        content = await self.file_manager.read_file(self.file.path)
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                return None

        lines = content.splitlines()
        total_lines = len(lines)

        # Extremely rough token estimation - assumes ~5 tokens per line on average
        estimated_tokens_per_line = 5
        estimated_total_tokens = total_lines * estimated_tokens_per_line

        # If content is already small enough, return it with minimal formatting
        if estimated_total_tokens <= max_tokens_estimate:
            return TextContent(
                content=f"File: {self.file.path}\n\n{content}", file_name=self.file.path
            )

        # Otherwise, create strategic representation of the content
        # Use roughly 30% for beginning, 40% for middle important parts, 30% for end
        beginning_lines = int(max_tokens_estimate * 0.3 / estimated_tokens_per_line)
        end_lines = int(max_tokens_estimate * 0.3 / estimated_tokens_per_line)

        # Try to identify important sections in the middle based on indentation changes,
        # section markers, blank lines followed by content, etc.
        middle_section_indices = []

        # Look for indentation changes or blank lines followed by content
        prev_indent = -1
        for i in range(beginning_lines, total_lines - end_lines):
            line = lines[i]
            if not line.strip():  # Skip blank lines themselves
                continue

            # Count leading spaces to determine indentation
            indent = len(line) - len(line.lstrip())

            # Major indentation change often indicates structure
            if prev_indent >= 0 and abs(indent - prev_indent) >= 4:
                middle_section_indices.append(i)

            # Line after blank line often starts a section
            if i > 0 and not lines[i - 1].strip():
                middle_section_indices.append(i)

            prev_indent = indent

        # Look for lines that might indicate sections or key parts
        file_type = self.file.file_type
        keyword_indicators = []

        if file_type in [FileType.PYTHON, FileType.JAVASCRIPT, FileType.TYPESCRIPT]:
            keyword_indicators = [
                "def ",
                "class ",
                "function",
                "import",
                "export",
                "const ",
                "let ",
                "var ",
            ]
        elif file_type == FileType.MARKDOWN:
            keyword_indicators = ["# ", "## ", "### "]
        elif file_type == FileType.HTML:
            keyword_indicators = ["<h1", "<h2", "<div", "<section", "<main"]

        for i in range(beginning_lines, total_lines - end_lines):
            line = lines[i].strip()
            if any(line.startswith(kw) for kw in keyword_indicators):
                middle_section_indices.append(i)

        # Deduplicate and sort the middle section indices
        middle_section_indices = sorted(list(set(middle_section_indices)))

        # Limit the middle sections to fit within our token budget
        middle_lines_budget = int(max_tokens_estimate * 0.4 / estimated_tokens_per_line)
        selected_middle_indices = []

        if middle_section_indices:
            # Try to select evenly distributed indices
            step = len(middle_section_indices) // min(
                middle_lines_budget, len(middle_section_indices)
            )
            step = max(1, step)  # Ensure step is at least 1
            selected_middle_indices = middle_section_indices[::step][
                :middle_lines_budget
            ]

        # Build the optimized content
        optimized_content = (
            f"File: {self.file.path} (Optimized view of {total_lines} lines)\n\n"
        )

        # Add beginning
        optimized_content += "--- Beginning of File ---\n"
        optimized_content += "\n".join(lines[:beginning_lines])
        optimized_content += "\n\n"

        # Add selected middle sections with context
        if selected_middle_indices:
            optimized_content += "--- Selected Important Sections ---\n"
            for idx in selected_middle_indices:
                # Include a small context window of lines around each selected point
                start_idx = max(beginning_lines, idx - 1)
                end_idx = min(total_lines - end_lines, idx + 2)

                optimized_content += f"\n[Lines {start_idx + 1}-{end_idx}]\n"
                optimized_content += "\n".join(lines[start_idx:end_idx])
                optimized_content += "\n"

        # Add end
        optimized_content += "\n--- End of File ---\n"
        optimized_content += "\n".join(lines[-end_lines:])

        return TextContent(content=optimized_content, file_name=self.file.path)

    @classmethod
    def from_file(cls, file: FileInfo) -> "LLMFile":
        """Factory method to create an LLMFile from a File object."""
        return cls(file)
