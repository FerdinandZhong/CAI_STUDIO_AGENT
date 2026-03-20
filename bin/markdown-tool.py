#!/usr/bin/env python3
"""
Markdown File Tool - LLM-Friendly Markdown File Operations

A comprehensive utility script for reading, writing, and manipulating markdown files.
Designed to be called by LLMs and automation tools via command-line interface or as a Pydantic-based tool.

CAPABILITIES:
-------------
1. READ: Display entire markdown file contents
2. WRITE: Create new markdown files with content (with overwrite protection)
3. APPEND: Add content to existing markdown files
4. EXTRACT HEADERS: Parse and list all headers (#, ##, ###, etc.) with line numbers
5. EXTRACT LINKS: Find all [text](url) links with locations
6. EXTRACT CODE BLOCKS: Extract all ```language code``` blocks
7. GENERATE TOC: Create table of contents from headers (display or insert)

OPERATIONS:
-----------
- All operations require a file path as the first argument
- Operations are mutually exclusive (use one per invocation)
- File paths can be absolute or relative
- Parent directories are created automatically for write operations
- All output is UTF-8 encoded

USAGE PATTERNS FOR LLM FUNCTION CALLS:
--------------------------------------
1. To read a markdown file:
   markdown-tool.py <file_path> --read

2. To create/write a new markdown file:
   markdown-tool.py <file_path> --write "<content>"
   (Fails if file exists, use --overwrite to replace)

3. To append content to existing file:
   markdown-tool.py <file_path> --append "\\n## New Section\\n"

4. To analyze document structure:
   markdown-tool.py <file_path> --headers
   markdown-tool.py <file_path> --links
   markdown-tool.py <file_path> --code-blocks

5. To generate/insert table of contents:
   markdown-tool.py <file_path> --toc          # Display only
   markdown-tool.py <file_path> --insert-toc   # Modify file

6. To write content from stdin (useful for large content):
   echo "content" | markdown-tool.py <file_path> --stdin --write

ERROR HANDLING:
--------------
- Returns exit code 0 on success
- Returns exit code 1 on error with descriptive message to stderr
- Common errors: FileNotFoundError, FileExistsError, ValueError

SECURITY NOTES:
--------------
- No arbitrary code execution
- File operations are limited to specified path
- No network operations
- Safe for automated/LLM usage
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Literal, Any
from pydantic import BaseModel, Field
import json


class MarkdownTool:
    """Utility class for markdown file operations."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def read(self) -> str:
        """Read the entire markdown file."""
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")

        with open(self.file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def write(self, content: str, overwrite: bool = False) -> None:
        """Write content to the markdown file."""
        if self.file_path.exists() and not overwrite:
            raise FileExistsError(
                f"File already exists: {self.file_path}. Use --overwrite to replace it."
            )

        # Create parent directories if they don't exist
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"✓ Written to {self.file_path}")

    def append(self, content: str) -> None:
        """Append content to the markdown file."""
        with open(self.file_path, 'a', encoding='utf-8') as f:
            f.write(content)

        print(f"✓ Appended to {self.file_path}")

    def extract_headers(self) -> List[Dict[str, any]]:
        """Extract all headers from the markdown file."""
        content = self.read()
        headers = []

        # Match markdown headers (# Header)
        pattern = r'^(#{1,6})\s+(.+)$'

        for match in re.finditer(pattern, content, re.MULTILINE):
            level = len(match.group(1))
            text = match.group(2).strip()
            headers.append({
                'level': level,
                'text': text,
                'line': content[:match.start()].count('\n') + 1
            })

        return headers

    def extract_links(self) -> List[Dict[str, str]]:
        """Extract all links from the markdown file."""
        content = self.read()
        links = []

        # Match markdown links [text](url)
        pattern = r'\[([^\]]+)\]\(([^\)]+)\)'

        for match in re.finditer(pattern, content):
            links.append({
                'text': match.group(1),
                'url': match.group(2),
                'line': content[:match.start()].count('\n') + 1
            })

        return links

    def extract_code_blocks(self) -> List[Dict[str, str]]:
        """Extract all code blocks from the markdown file."""
        content = self.read()
        code_blocks = []

        # Match code blocks ```lang\ncode\n```
        pattern = r'```(\w*)\n(.*?)```'

        for match in re.finditer(pattern, content, re.DOTALL):
            language = match.group(1) or 'text'
            code = match.group(2).strip()
            code_blocks.append({
                'language': language,
                'code': code,
                'line': content[:match.start()].count('\n') + 1
            })

        return code_blocks

    def get_toc(self) -> str:
        """Generate a table of contents from headers."""
        headers = self.extract_headers()
        toc_lines = ["# Table of Contents\n"]

        for header in headers:
            indent = "  " * (header['level'] - 1)
            # Create anchor link (GitHub style)
            anchor = header['text'].lower()
            anchor = re.sub(r'[^\w\s-]', '', anchor)
            anchor = re.sub(r'[-\s]+', '-', anchor)

            toc_lines.append(f"{indent}- [{header['text']}](#{anchor})")

        return "\n".join(toc_lines)

    def insert_toc(self, overwrite: bool = False) -> None:
        """Insert a table of contents at the beginning of the file."""
        content = self.read()
        toc = self.get_toc()

        # Check if TOC already exists
        if "# Table of Contents" in content and not overwrite:
            raise ValueError("TOC already exists. Use --overwrite to replace it.")

        # Remove existing TOC if overwrite is True
        if overwrite:
            content = re.sub(
                r'# Table of Contents\n.*?(?=\n#{1,6}\s|\Z)',
                '',
                content,
                flags=re.DOTALL
            ).lstrip()

        new_content = f"{toc}\n\n{content}"
        self.write(new_content, overwrite=True)
        print(f"✓ TOC inserted into {self.file_path}")


# ============================================================================
# Pydantic Models for LLM Tool Integration
# ============================================================================

class UserParameters(BaseModel):
    """
    User configuration parameters for the markdown tool.
    These parameters are typically configured once per tool instance.
    """
    pass  # No user-level configuration needed for this tool


class ToolParameters(BaseModel):
    """
    Arguments passed to the markdown tool for each operation.
    These arguments are provided by the LLM/agent when calling the tool.
    The descriptions below help LLMs understand how to use each parameter.
    """

    file_path: str = Field(
        description="Path to the markdown file to operate on. Can be absolute or relative. "
                    "Parent directories will be created automatically for write operations. "
                    "Example: 'docs/readme.md' or '/tmp/test.md'"
    )

    operation: Literal["read", "write", "append", "extract_headers", "extract_links",
                      "extract_code_blocks", "generate_toc", "insert_toc"] = Field(
        description="The operation to perform on the markdown file. Options: "
                    "'read' - Display entire file contents; "
                    "'write' - Create or overwrite file with content; "
                    "'append' - Add content to end of file; "
                    "'extract_headers' - List all headers with line numbers; "
                    "'extract_links' - List all [text](url) links; "
                    "'extract_code_blocks' - Extract all ```code``` blocks; "
                    "'generate_toc' - Generate table of contents (display only); "
                    "'insert_toc' - Insert table of contents into file"
    )

    content: Optional[str] = Field(
        default=None,
        description="Content to write or append to the file. Required for 'write' and 'append' operations. "
                    "Should include proper markdown formatting (headers, links, code blocks, etc.). "
                    "Use \\n for line breaks. For multi-line content, include full markdown structure. "
                    "Example: '# Title\\n\\n## Section\\n\\nParagraph text.' "
                    "Ignored for read and extract operations."
    )

    overwrite: bool = Field(
        default=False,
        description="Allow overwriting existing files for 'write' operation, or replacing existing "
                    "table of contents for 'insert_toc' operation. Default is False to prevent "
                    "accidental data loss. Set to True to explicitly allow overwriting. "
                    "Has no effect on 'append' or extract operations."
    )


def run_tool(config: UserParameters, args: ToolParameters) -> str:
    """
    Main tool execution logic. Processes the requested markdown operation.

    Args:
        config: User configuration (currently unused but required for tool interface)
        args: Tool parameters containing file path, operation, and optional content

    Returns:
        String result of the operation (file contents, extracted data, or success message)

    Raises:
        FileNotFoundError: If file doesn't exist for read operations
        FileExistsError: If file exists for write without overwrite flag
        ValueError: If invalid operation or missing required parameters
    """
    md = MarkdownTool(args.file_path)

    try:
        if args.operation == "read":
            return md.read()

        elif args.operation == "write":
            if args.content is None:
                raise ValueError("Content is required for write operation")
            md.write(args.content, overwrite=args.overwrite)
            return f"✓ Written to {args.file_path}"

        elif args.operation == "append":
            if args.content is None:
                raise ValueError("Content is required for append operation")
            md.append(args.content)
            return f"✓ Appended to {args.file_path}"

        elif args.operation == "extract_headers":
            headers = md.extract_headers()
            result = [f"\nFound {len(headers)} headers in {args.file_path}:\n"]
            for h in headers:
                indent = "  " * (h['level'] - 1)
                result.append(f"{indent}{'#' * h['level']} {h['text']} (line {h['line']})")
            return "\n".join(result)

        elif args.operation == "extract_links":
            links = md.extract_links()
            result = [f"\nFound {len(links)} links in {args.file_path}:\n"]
            for link in links:
                result.append(f"[{link['text']}]({link['url']}) at line {link['line']}")
            return "\n".join(result)

        elif args.operation == "extract_code_blocks":
            blocks = md.extract_code_blocks()
            result = [f"\nFound {len(blocks)} code blocks in {args.file_path}:\n"]
            for i, block in enumerate(blocks, 1):
                result.append(f"\n--- Code Block {i} ({block['language']}) at line {block['line']} ---")
                result.append(block['code'])
                result.append("---")
            return "\n".join(result)

        elif args.operation == "generate_toc":
            return md.get_toc()

        elif args.operation == "insert_toc":
            md.insert_toc(overwrite=args.overwrite)
            return f"✓ TOC inserted into {args.file_path}"

        else:
            raise ValueError(f"Unknown operation: {args.operation}")

    except Exception as e:
        return f"Error: {e}"


OUTPUT_KEY = "tool_output"
"""
When an agent calls this tool, only stdout content after this key is passed back to the agent.
This allows structured output while retaining the full stdout stream.
"""


def main():
    parser = argparse.ArgumentParser(
        description="Markdown file tool for reading, writing, and manipulating markdown files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s file.md --read                    # Read and display file contents
  %(prog)s file.md --write "# Title"         # Write content to file (fails if exists)
  %(prog)s file.md --write "# Title" --overwrite  # Overwrite existing file
  %(prog)s file.md --append "\\n## Section"   # Append content to existing file
  %(prog)s file.md --headers                 # List all headers with line numbers
  %(prog)s file.md --links                   # Extract all markdown links
  %(prog)s file.md --code-blocks             # Extract all code blocks
  %(prog)s file.md --toc                     # Generate table of contents (display only)
  %(prog)s file.md --insert-toc              # Insert TOC at beginning of file
  cat content.txt | %(prog)s file.md --stdin --write  # Write from stdin
        """
    )

    parser.add_argument(
        'file',
        metavar='FILE',
        help='Path to the markdown file to operate on. Can be absolute or relative path. '
             'Parent directories will be created automatically for write operations.'
    )

    # Read operations
    parser.add_argument(
        '--read',
        action='store_true',
        help='Read and display the entire contents of the markdown file to stdout. '
             'Exits with error if file does not exist.'
    )

    # Write operations
    parser.add_argument(
        '--write',
        type=str,
        metavar='CONTENT',
        help='Write CONTENT to the markdown file. By default, fails if file already exists '
             'unless --overwrite is specified. Creates parent directories if needed. '
             'Use with --stdin to read content from stdin instead of command line argument.'
    )

    parser.add_argument(
        '--append',
        type=str,
        metavar='CONTENT',
        help='Append CONTENT to the end of the markdown file. Creates file if it does not exist. '
             'Does not add newlines automatically - include \\n in CONTENT if needed.'
    )

    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='Allow overwriting existing files when used with --write. '
             'Also allows replacing existing table of contents with --insert-toc. '
             'Has no effect with other operations.'
    )

    # Extraction operations
    parser.add_argument(
        '--headers',
        action='store_true',
        help='Extract and display all markdown headers (# through ######) from the file. '
             'Output includes header level, text content, and line number. '
             'Returns count and hierarchical view of document structure.'
    )

    parser.add_argument(
        '--links',
        action='store_true',
        help='Extract and display all markdown links from the file. '
             'Matches [text](url) format. Output includes link text, URL, and line number. '
             'Useful for finding broken links or external references.'
    )

    parser.add_argument(
        '--code-blocks',
        action='store_true',
        help='Extract and display all fenced code blocks from the file. '
             'Matches ```language\\ncode\\n``` format. '
             'Output includes language identifier, code content, and line number. '
             'Useful for extracting examples or analyzing code documentation.'
    )

    # Table of contents operations
    parser.add_argument(
        '--toc',
        action='store_true',
        help='Generate and display a table of contents based on headers in the file. '
             'Creates GitHub-style anchor links. Indentation reflects header hierarchy. '
             'Only displays to stdout - does not modify the file (use --insert-toc for that).'
    )

    parser.add_argument(
        '--insert-toc',
        action='store_true',
        help='Generate a table of contents and insert it at the beginning of the file. '
             'Fails if TOC already exists unless --overwrite is specified. '
             'With --overwrite, removes existing TOC and inserts new one. '
             'Modifies the file in place.'
    )

    # Input options
    parser.add_argument(
        '--stdin',
        action='store_true',
        help='Read content from stdin instead of command line argument. '
             'Use with --write to pipe content from another command or file. '
             'Example: cat input.md | markdown-tool.py output.md --stdin --write'
    )

    # Pydantic tool mode arguments (for LLM integration)
    parser.add_argument(
        '--user-params',
        type=str,
        help='JSON string for user configuration parameters (Pydantic tool mode). '
             'Used when calling this tool via the Pydantic tool interface.'
    )

    parser.add_argument(
        '--tool-params',
        type=str,
        help='JSON string for tool operation parameters (Pydantic tool mode). '
             'Contains file_path, operation, content, and overwrite flags. '
             'Used when calling this tool via the Pydantic tool interface.'
    )

    args = parser.parse_args()

    # Check if we're in Pydantic tool mode
    if args.user_params and args.tool_params:
        # Parse JSON into dictionaries
        user_dict = json.loads(args.user_params)
        tool_dict = json.loads(args.tool_params)

        # Validate dictionaries against Pydantic models
        config = UserParameters(**user_dict)
        params = ToolParameters(**tool_dict)

        # Run the tool and print output
        output = run_tool(config, params)
        print(OUTPUT_KEY, output)
        return

    if not args.file:
        parser.print_help()
        sys.exit(1)

    md = MarkdownTool(args.file)

    try:
        # Read operation
        if args.read:
            content = md.read()
            print(content)

        # Write operation
        elif args.write or args.stdin:
            if args.stdin:
                content = sys.stdin.read()
            else:
                content = args.write

            md.write(content, overwrite=args.overwrite)

        # Append operation
        elif args.append:
            md.append(args.append)

        # Extract headers
        elif args.headers:
            headers = md.extract_headers()
            print(f"\nFound {len(headers)} headers in {args.file}:\n")
            for h in headers:
                indent = "  " * (h['level'] - 1)
                print(f"{indent}{'#' * h['level']} {h['text']} (line {h['line']})")

        # Extract links
        elif args.links:
            links = md.extract_links()
            print(f"\nFound {len(links)} links in {args.file}:\n")
            for link in links:
                print(f"[{link['text']}]({link['url']}) at line {link['line']}")

        # Extract code blocks
        elif args.code_blocks:
            blocks = md.extract_code_blocks()
            print(f"\nFound {len(blocks)} code blocks in {args.file}:\n")
            for i, block in enumerate(blocks, 1):
                print(f"\n--- Code Block {i} ({block['language']}) at line {block['line']} ---")
                print(block['code'])
                print("---")

        # Generate TOC
        elif args.toc:
            toc = md.get_toc()
            print(toc)

        # Insert TOC
        elif args.insert_toc:
            md.insert_toc(overwrite=args.overwrite)

        else:
            parser.print_help()

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
