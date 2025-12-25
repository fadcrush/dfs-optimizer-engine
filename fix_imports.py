"""
Fix missing typing imports in all files
"""

import re
from pathlib import Path

files_to_fix = [
    Path(r"C:\Users\David\Documents\N_B_A_and_N_F_L\analysis\nba\analytics.py"),
    Path(r"C:\Users\David\Documents\N_B_A_and_N_F_L\analysis\nba\data_aggregator.py"),
    Path(r"C:\Users\David\Documents\N_B_A_and_N_F_L\analysis\nba\pipeline.py"),
    Path(r"C:\Users\David\Documents\N_B_A_and_N_F_L\analysis\shared\api_clients.py"),
]

typing_import = "from typing import Dict, List, Tuple, Optional\n"

for file_path in files_to_fix:
    if not file_path.exists():
        print(f"✗ {file_path.name} not found")
        continue
    
    content = file_path.read_text(encoding='utf-8')
    
    # Check if typing is already imported
    if 'from typing import' in content:
        # Check if List and Dict are included
        if 'List' not in content.split('from typing import')[1].split('\n')[0]:
            # Add List and Dict to existing import
            content = re.sub(
                r'from typing import ([^\n]+)',
                lambda m: f"from typing import {m.group(1)}, Dict, List",
                content
            )
            file_path.write_text(content, encoding='utf-8')
            print(f"✓ Updated {file_path.name}")
        else:
            print(f"→ {file_path.name} already correct")
    else:
        # Add typing import at the top (after docstring)
        lines = content.split('\n')
        
        # Find where to insert (after docstring)
        insert_pos = 0
        in_docstring = False
        for i, line in enumerate(lines):
            if '"""' in line or "'''" in line:
                if not in_docstring:
                    in_docstring = True
                else:
                    insert_pos = i + 1
                    break
        
        lines.insert(insert_pos, typing_import)
        content = '\n'.join(lines)
        
        file_path.write_text(content, encoding='utf-8')
        print(f"✓ Fixed {file_path.name}")

print("\n✓ All files processed!")