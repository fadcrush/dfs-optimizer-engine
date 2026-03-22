import re
with open('workers/schedulers/daily.py', 'rb') as f:
    content = f.read()
if content.startswith(b'\xef\xbb\xbf'):
    content = content[3:]
text = content.decode('utf-8')
tqs = [(m.start(), text[:m.start()].count('\n')+1) for m in re.finditer('"""', text)]
print('Total triple-quotes:', len(tqs))
for i, (pos, lineno) in enumerate(tqs):
    col = pos - text.rfind('\n', 0, pos) - 1
    snippet = text[pos:pos+40].replace('\r\n','\\n').replace('\n','\\n')
    print(f"  #{i+1}: line {lineno}, col {col}  {snippet}")
