import ast
import os
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".next",
    "uploads"
}

functions_defined = defaultdict(list)
functions_imported = defaultdict(list)
import_errors = []

def should_skip(path):
    return any(part in EXCLUDE_DIRS for part in path.split(os.sep))

def parse_python_file(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        try:
            tree = ast.parse(f.read(), filename=file_path)
        except SyntaxError as e:
            import_errors.append((file_path, f"SyntaxError: {e}"))
            return

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions_defined[node.name].append(file_path)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for name in node.names:
                functions_imported[name.name].append(
                    f"{file_path} (from {module})"
                )

        elif isinstance(node, ast.Import):
            for name in node.names:
                functions_imported[name.name].append(
                    f"{file_path} (import)"
                )

def walk_project():
    for root, dirs, files in os.walk(PROJECT_ROOT):
        if should_skip(root):
            continue

        for file in files:
            if file.endswith(".py"):
                parse_python_file(os.path.join(root, file))

def generate_report():
    report = []
    report.append("=== DFS EDGE PRO PROJECT AUDIT ===\n")

    report.append("\n--- FUNCTIONS DEFINED ---")
    for fn, locations in sorted(functions_defined.items()):
        report.append(f"\n{fn}:")
        for loc in locations:
            report.append(f"  - {loc}")

    report.append("\n--- FUNCTIONS IMPORTED BUT NOT DEFINED ---")
    for fn, imports in sorted(functions_imported.items()):
        if fn not in functions_defined:
            report.append(f"\n❌ {fn} (MISSING)")
            for imp in imports:
                report.append(f"  - {imp}")

    if import_errors:
        report.append("\n--- PARSE ERRORS ---")
        for file, err in import_errors:
            report.append(f"{file}: {err}")

    output_path = os.path.join(PROJECT_ROOT, "AUDIT_REPORT.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print(f"\n✅ Audit complete.")
    print(f"📄 Report written to: {output_path}")

if __name__ == "__main__":
    walk_project()
    generate_report()
