# Markdown Tool

A versatile command-line utility for reading, writing, and manipulating markdown files.

## Features

- **Read** markdown files
- **Write** content to markdown files
- **Append** content to existing files
- **Extract** headers, links, and code blocks
- **Generate** and insert table of contents
- Support for stdin input

## Installation

The script is located at `bin/markdown-tool.py` and is ready to use.

```bash
chmod +x bin/markdown-tool.py
```

## Usage

### Read a markdown file

```bash
./bin/markdown-tool.py myfile.md --read
```

### Write content to a file

```bash
./bin/markdown-tool.py myfile.md --write "# Hello World\n\nThis is my content."
```

### Write from stdin

```bash
echo "# My Header" | ./bin/markdown-tool.py myfile.md --stdin
```

### Append content to a file

```bash
./bin/markdown-tool.py myfile.md --append "\n## New Section\n\nAdditional content here."
```

### Overwrite an existing file

```bash
./bin/markdown-tool.py myfile.md --write "# New Content" --overwrite
```

### Extract headers

```bash
./bin/markdown-tool.py myfile.md --headers
```

Output:
```
Found 3 headers in myfile.md:

# Main Title (line 1)
  ## Subsection (line 5)
  ## Another Section (line 10)
```

### Extract links

```bash
./bin/markdown-tool.py myfile.md --links
```

Output:
```
Found 2 links in myfile.md:

[GitHub](https://github.com) at line 3
[Documentation](./docs/README.md) at line 7
```

### Extract code blocks

```bash
./bin/markdown-tool.py myfile.md --code-blocks
```

Output:
```
Found 2 code blocks in myfile.md:

--- Code Block 1 (python) at line 10 ---
def hello():
    print("Hello World")
---

--- Code Block 2 (bash) at line 20 ---
echo "Hello"
---
```

### Generate table of contents

```bash
./bin/markdown-tool.py myfile.md --toc
```

Output:
```
# Table of Contents

- [Main Title](#main-title)
  - [Subsection](#subsection)
  - [Another Section](#another-section)
```

### Insert table of contents into file

```bash
./bin/markdown-tool.py myfile.md --insert-toc
```

This will add the TOC at the beginning of the file. Use `--overwrite` if a TOC already exists:

```bash
./bin/markdown-tool.py myfile.md --insert-toc --overwrite
```

## Examples

### Create a new markdown file

```bash
./bin/markdown-tool.py docs/guide.md --write "# User Guide\n\n## Introduction\n\nWelcome to the guide."
```

### Analyze an existing file

```bash
# See all headers
./bin/markdown-tool.py README.md --headers

# Check all external links
./bin/markdown-tool.py README.md --links

# Extract code examples
./bin/markdown-tool.py README.md --code-blocks
```

### Build a document programmatically

```bash
# Create base document
./bin/markdown-tool.py report.md --write "# Monthly Report\n\n## Overview"

# Append sections
./bin/markdown-tool.py report.md --append "\n\n## Statistics\n\nData here..."
./bin/markdown-tool.py report.md --append "\n\n## Conclusion\n\nSummary here..."

# Add TOC
./bin/markdown-tool.py report.md --insert-toc
```

### Use with pipes

```bash
# Generate content and write to file
echo -e "# TODO\n\n- [ ] Task 1\n- [ ] Task 2" | ./bin/markdown-tool.py todo.md --stdin

# Read and process
./bin/markdown-tool.py README.md --read | grep -i "installation"
```

## LLM/Agent Integration (Pydantic Tool Mode)

The tool supports Pydantic-based tool integration for LLM agents. This mode uses structured JSON parameters with detailed field descriptions to help LLMs make informed function calls.

### Tool Parameters

The tool accepts two JSON parameters:

1. **`--user-params`**: User configuration (currently empty `{}` for this tool)
2. **`--tool-params`**: Operation parameters with the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file_path` | string | Yes | Path to the markdown file (absolute or relative) |
| `operation` | string | Yes | Operation to perform: `read`, `write`, `append`, `extract_headers`, `extract_links`, `extract_code_blocks`, `generate_toc`, or `insert_toc` |
| `content` | string | Conditional | Content for `write` or `append` operations (required for these operations) |
| `overwrite` | boolean | No | Allow overwriting files (default: `false`) |

### LLM Usage Examples

**Write a file:**
```bash
python3 markdown-tool.py dummy.md --user-params '{}' --tool-params '{
  "file_path": "docs/readme.md",
  "operation": "write",
  "content": "# Project\\n\\n## Overview\\nDescription here.",
  "overwrite": false
}'
```

**Read a file:**
```bash
python3 markdown-tool.py dummy.md --user-params '{}' --tool-params '{
  "file_path": "docs/readme.md",
  "operation": "read"
}'
```

**Extract headers:**
```bash
python3 markdown-tool.py dummy.md --user-params '{}' --tool-params '{
  "file_path": "docs/readme.md",
  "operation": "extract_headers"
}'
```

**Append content:**
```bash
python3 markdown-tool.py dummy.md --user-params '{}' --tool-params '{
  "file_path": "docs/readme.md",
  "operation": "append",
  "content": "\\n## New Section\\n\\nAdditional content."
}'
```

**Insert table of contents:**
```bash
python3 markdown-tool.py dummy.md --user-params '{}' --tool-params '{
  "file_path": "docs/readme.md",
  "operation": "insert_toc",
  "overwrite": true
}'
```

### Output Format

When using Pydantic mode, the tool returns output after the `tool_output` marker:

```
tool_output <actual result here>
```

This allows the calling system to parse structured output while retaining the full stdout stream.

### Field Documentation for LLMs

The `ToolParameters` Pydantic model includes detailed field descriptions:

- **file_path**: "Path to the markdown file to operate on. Can be absolute or relative. Parent directories will be created automatically for write operations. Example: 'docs/readme.md' or '/tmp/test.md'"

- **operation**: "The operation to perform on the markdown file. Options: 'read' - Display entire file contents; 'write' - Create or overwrite file with content; 'append' - Add content to end of file; 'extract_headers' - List all headers with line numbers; 'extract_links' - List all [text](url) links; 'extract_code_blocks' - Extract all ```code``` blocks; 'generate_toc' - Generate table of contents (display only); 'insert_toc' - Insert table of contents into file"

- **content**: "Content to write or append to the file. Required for 'write' and 'append' operations. Should include proper markdown formatting (headers, links, code blocks, etc.). Use \\n for line breaks. For multi-line content, include full markdown structure. Example: '# Title\\n\\n## Section\\n\\nParagraph text.' Ignored for read and extract operations."

- **overwrite**: "Allow overwriting existing files for 'write' operation, or replacing existing table of contents for 'insert_toc' operation. Default is False to prevent accidental data loss. Set to True to explicitly allow overwriting. Has no effect on 'append' or extract operations."

## Python API

You can also use the `MarkdownTool` class in your Python scripts:

```python
from bin.markdown_tool import MarkdownTool

# Create instance
md = MarkdownTool('myfile.md')

# Read content
content = md.read()

# Write content
md.write("# Hello World", overwrite=True)

# Append content
md.append("\n## New Section")

# Extract information
headers = md.extract_headers()
links = md.extract_links()
code_blocks = md.extract_code_blocks()

# Generate TOC
toc = md.get_toc()

# Insert TOC into file
md.insert_toc(overwrite=True)
```

### Using the Pydantic Interface

```python
from bin.markdown_tool import UserParameters, ToolParameters, run_tool

# Configure user parameters (empty for this tool)
config = UserParameters()

# Define operation parameters
params = ToolParameters(
    file_path="docs/readme.md",
    operation="write",
    content="# My Document\\n\\n## Section 1\\n\\nContent here.",
    overwrite=False
)

# Execute the tool
result = run_tool(config, params)
print(result)
```

## Error Handling

The tool provides clear error messages:

- **File not found**: When trying to read a non-existent file
- **File exists**: When trying to write without `--overwrite`
- **TOC exists**: When trying to insert TOC without `--overwrite`

## Requirements

- Python 3.7 or higher
- **Dependencies**:
  - `pydantic` >= 2.0 (for LLM/Agent integration mode)
  - Standard library modules: `argparse`, `pathlib`, `re`, `json`

**Installation:**
```bash
pip install pydantic
```

For CLI-only usage without LLM integration, pydantic is optional (though recommended).

## License

This tool is part of the CAI Studio Agent project.
