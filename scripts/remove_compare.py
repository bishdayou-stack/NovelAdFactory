"""Remove comparison dropdown and related logic from Meta page"""
import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Delete getCompareDateRange function
old_fn = """    function getCompareDateRange() {
      var baseline = document.getElementById('metaCompareBaseline').value;
      var start, end;
      if (baseline === 'yesterday') {
        var d = new Date(); d.setDate(d.getDate()-2);
        start = end = d.toISOString().slice(0,10);
      } else if (baseline === 'daybefore') {
        var d = new Date(); d.setDate(d.getDate()-3);
        start = end = d.toISOString().slice(0,10);
      } else if (baseline === 'lastweek') {
        var d = new Date(); d.setDate(d.getDate()-7);
        start = end = d.toISOString().slice(0,10);
      } else if (baseline === 'avg7') {
        var d = new Date(); d.setDate(d.getDate()-1);
        end = d.toISOString().slice(0,10);
        d.setDate(d.getDate()-7);
        start = d.toISOString().slice(0,10);
      } else if (baseline === 'avg30') {
        var d = new Date(); d.setDate(d.getDate()-1);
        end = d.toISOString().slice(0,10);
        d.setDate(d.getDate()-30);
        start = d.toISOString().slice(0,10);
      }
      return { start: start, end: end };
    }

"""
content = content.replace(old_fn, '\n')
print('Removed getCompareDateRange')

# 2. Simplify renderMetaKpis
content = content.replace(
    'function renderMetaKpis(summary, compareSummary)',
    'function renderMetaKpis(summary)'
)
# Replace compareSummary references
content = re.sub(r'prev: compareSummary\.\w+ \|\| 0', 'prev: 0', content)
print('Simplified renderMetaKpis')

# 3. Remove compareParams from refreshMetaDashboard
content = content.replace(
    '      var compareRange = getCompareDateRange();\n'
    "      var params = '?start=' + range.start + '&end=' + range.end;\n"
    "      var compareParams = '?start=' + compareRange.start + '&end=' + compareRange.end;",
    "      var params = '?start=' + range.start + '&end=' + range.end;"
)
# Remove compareParams lines
for line_to_remove in [
    "          compareParams += '&user_id=' + uid;",
    "          compareParams += '&keyword=' + encodeURIComponent(bmName);",
    "          compareParams += '&account=' + _metaSelectedAccount;",
]:
    content = content.replace(line_to_remove + '\n', '')
print('Removed compareParams logic')

# 4. Remove compare fetch
content = content.replace(
    "          fetch('/api/meta/summary' + compareParams).then(function(r) { return r.json(); }),\n",
    ''
)
print('Removed compare fetch')

# 5. Update renderMetaKpis call
content = content.replace('renderMetaKpis(summaryRes, compareRes);', 'renderMetaKpis(summaryRes);')
print('Updated renderMetaKpis call')

# 6. Remove event listener
content = content.replace(
    "document.getElementById('metaCompareBaseline').addEventListener('change', function() { refreshMetaDashboard(); });\n",
    ''
)
print('Removed event listener')

# Clean up double blank lines
content = re.sub(r'\n{4,}', '\n\n\n', content)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('\nDone')
