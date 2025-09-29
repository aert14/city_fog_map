#!/usr/bin/env python3
import os
import sys
from pathlib import Path

def is_binary_file(filepath):
    """Check if a file is binary by trying to read it as text"""
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(1024)
            if b'\x00' in chunk:
                return True
            # Try to decode as UTF-8
            chunk.decode('utf-8')
            return False
    except UnicodeDecodeError:
        return True
    except Exception:
        return True

def get_project_structure(root_dir=".", exclude_dirs=None, exclude_files=None):
    """
    Get project structure as string excluding junk files and directories
    """
    if exclude_dirs is None:
        exclude_dirs = {
            '.git', '__pycache__', 'venv', 'env', '.env', 'node_modules',
            '.vscode', '.idea', '.pytest_cache', 'dist', 'build', '.next',
            '.nuxt', '.cache', 'tmp', 'temp', '.DS_Store', 'Thumbs.db',
            '.mypy_cache', '.tox', '.coverage', 'htmlcov', '.pytest_cache'
        }

    if exclude_files is None:
        exclude_files = {
            '.pyc', '.pyo', '.DS_Store', 'Thumbs.db', '.gitignore',
            '.gitkeep', '.vscode', 'desktop.ini', '.env', '.env.local',
            '.env.production', '.env.development'
        }

    def should_exclude_path(path):
        """Check if path should be excluded"""
        path_str = str(path)
        name = path.name

        # Exclude hidden files/directories starting with .
        if name.startswith('.') and name not in {'.gitignore', '.dockerignore'}:
            return True

        # Exclude specific directories
        if path.is_dir() and name in exclude_dirs:
            return True

        # Exclude specific file extensions
        if path.is_file() and any(name.endswith(ext) for ext in exclude_files):
            return True

        return False

    def build_tree(current_path, prefix="", lines=None):
        """Recursively build directory tree as list of lines"""
        if lines is None:
            lines = []

        try:
            items = sorted([p for p in current_path.iterdir() if not should_exclude_path(p)],
                          key=lambda x: (x.is_file(), x.name.lower()))

            for i, item in enumerate(items):
                is_last = i == len(items) - 1
                connector = "└── " if is_last else "├── "

                if item.is_dir():
                    lines.append(f"{prefix}{connector}{item.name}/")
                    extension = "    " if is_last else "│   "
                    build_tree(item, prefix + extension, lines)
                else:
                    lines.append(f"{prefix}{connector}{item.name}")

        except PermissionError:
            lines.append(f"{prefix}└── [Permission denied]")

        return lines

    root_path = Path(root_dir)
    lines = [f"{root_path.name}/"]
    lines.extend(build_tree(root_path))
    return '\n'.join(lines)

def print_project_structure(root_dir=".", exclude_dirs=None, exclude_files=None):
    """Print project structure to console"""
    print(get_project_structure(root_dir, exclude_dirs, exclude_files))

def dump_files_to_script_dump(directories, project_structure=""):
    output_lines = []

    # Add project structure at the beginning
    if project_structure:
        output_lines.append("PROJECT STRUCTURE:")
        output_lines.append(project_structure)
        output_lines.append("")
        output_lines.append("=" * 50)
        output_lines.append("")

    for base_dir in directories:
        for root, dirs, files in os.walk(base_dir):
            for file in files:
                filepath = os.path.join(root, file)
                if is_binary_file(filepath):
                    print(f"Skipping binary file: {filepath}", file=sys.stderr)
                    continue

                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                        output_lines.append(f"{filepath}:")
                        output_lines.append(content)
                        output_lines.append("")  # Empty line between files
                except Exception as e:
                    print(f"Error reading {filepath}: {e}", file=sys.stderr)

    # Write to script_dump.txt
    with open('script_dump.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))

if __name__ == "__main__":
    directories_to_dump = ['app', 'services', 'webapp']
    files_to_dump = ['Makefile']  # Add specific files to dump

    # Get project structure
    project_structure = get_project_structure()

    # Dump directories and specific files with project structure at the beginning
    dump_files_to_script_dump(directories_to_dump, project_structure)

    # Dump specific files (append to existing file)
    output_lines = []
    for file_path in files_to_dump:
        if os.path.exists(file_path) and not is_binary_file(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    output_lines.append(f"{file_path}:")
                    output_lines.append(content)
                    output_lines.append("")  # Empty line between files
            except Exception as e:
                print(f"Error reading {file_path}: {e}", file=sys.stderr)

    # Append specific files to script_dump.txt
    if output_lines:
        with open('script_dump.txt', 'a', encoding='utf-8') as f:
            f.write('\n'.join(output_lines))

    print("\nProject structure added to beginning of script_dump.txt")
    print("Files dumped to script_dump.txt")
