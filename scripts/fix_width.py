"""Remove table-layout:fixed and width percentages from Meta tables"""
import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove style="width:...%" from all th elements in meta tables
content = re.sub(r' style="width:\d+%"', '', content)
# Remove style="table-layout:fixed"
content = content.replace(' style="table-layout:fixed"', '')
print('Removed all fixed widths from Meta tables')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
