#!/usr/bin/env python3
import os
import sys

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

def dump_files_to_script_dump(directories):
    output_lines = []

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

    # Dump directories
    dump_files_to_script_dump(directories_to_dump)

    # Dump specific files
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

    print("Files dumped to script_dump.txt")
