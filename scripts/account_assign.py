"""Add account assignment tab + remove pause buttons + backend API"""
import re

# === Part 1: Frontend changes ===
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add sidebar nav button (after nav-meta button)
nav_meta_end = content.find('</button>\n      <button type="button" id="nav-users"')
if nav_meta_end >= 0:
    # Find the end of the nav-meta button
    insert_pos = nav_meta_end + len('</button>\n')
    assign_nav = '''      <button type="button" id="nav-meta-assign" class="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm text-slate-400 hover:bg-white/5 hover:text-slate-200 transition-all border-0 cursor-pointer" style="display:none">
        <svg class="sidebar-icon shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
        <span class="truncate">账户分配</span>
      </button>
'''
    content = content[:insert_pos] + assign_nav + content[insert_pos:]
    print('1. Added nav-meta-assign button')

# 2. Add tab-meta-assign HTML (before 爆款素材库 tab)
assign_tab = '''
    <!-- ====== Tab: 账户分配 ====== -->
    <div id="tab-meta-assign" class="flex-1 px-6 pb-6 overflow-y-auto" hidden>
      <div class="flex items-center justify-between mb-4 mt-2">
        <h2 class="text-lg font-bold text-slate-800">账户分配</h2>
        <span class="text-xs text-slate-400">管理员将 Meta 账户分配给用户</span>
      </div>
      <div class="grid grid-cols-3 gap-4">
        <!-- 左侧账户树 -->
        <div class="card p-4 max-h-[70vh] overflow-y-auto">
          <h3 class="text-sm font-semibold text-slate-700 mb-3">Meta 账户列表</h3>
          <div class="mb-2">
            <input id="assignSearch" type="text" placeholder="搜索账户..." class="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-xs focus:border-indigo-400 focus:outline-none">
          </div>
          <div class="flex items-center gap-2 mb-2">
            <button onclick="assignSelectAll()" class="text-xs text-indigo-600 cursor-pointer border-0 bg-transparent">全选</button>
            <button onclick="assignDeselectAll()" class="text-xs text-slate-400 cursor-pointer border-0 bg-transparent">取消</button>
          </div>
          <div id="assignAccountTree" class="text-xs space-y-0.5"></div>
        </div>
        <!-- 右侧操作区 -->
        <div class="col-span-2 card p-4">
          <h3 class="text-sm font-semibold text-slate-700 mb-3">分配操作</h3>
          <div id="assignSelectedInfo" class="text-xs text-slate-400 mb-3">未选择任何账户</div>
          <div class="flex items-center gap-3 mb-4">
            <select id="assignTargetUser" class="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white">
              <option value="">选择目标用户...</option>
            </select>
            <button onclick="doAssignAccounts()" class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 cursor-pointer">确认分配</button>
          </div>
          <div id="assignMsg" class="text-xs text-slate-500"></div>
          <hr class="my-3">
          <h3 class="text-sm font-semibold text-slate-700 mb-2">快捷操作：整个 BM 分配</h3>
          <div class="flex items-center gap-3">
            <select id="assignBmSelect" class="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white">
              <option value="">选择 BM...</option>
            </select>
            <span class="text-xs">→</span>
            <select id="assignBmTarget" class="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white">
              <option value="">分配给...</option>
            </select>
            <button onclick="doAssignBm()" class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 cursor-pointer">一键分配</button>
          </div>
          <div id="assignBmMsg" class="text-xs text-slate-500 mt-2"></div>
        </div>
      </div>
    </div>
'''
tab_insert = content.find('<!-- ====== Tab: 爆款素材库 ====== -->')
content = content[:tab_insert] + assign_tab + '\n' + content[tab_insert:]
print('2. Added tab-meta-assign HTML')

# 3. Update allTabs and titles
content = content.replace(
    "'meta', 'users'];",
    "'meta', 'meta-assign', 'users'];"
)
content = content.replace(
    "'meta': 'Meta 管理', 'users': '用户管理'",
    "'meta': 'Meta 管理', 'meta-assign': '账户分配', 'users': '用户管理'"
)

# 4. Update switchTab for new tab
content = content.replace(
    "if (tab === 'meta') { loadMetaAccountTree(); refreshMetaDashboard(); }",
    "if (tab === 'meta') { loadMetaAccountTree(); refreshMetaDashboard(); }\n"
    "      if (tab === 'meta-assign') { loadAssignPage(); }"
)
print('3. Updated JS routing')

# 5. Show nav-meta-assign for admin in hideLoginOverlay/updateSidebarUser
# Add to admin visibility logic
content = content.replace(
    "document.getElementById('nav-users').style.display = (currentUser && currentUser.role === 'admin') ? '' : 'none';",
    "document.getElementById('nav-users').style.display = (currentUser && currentUser.role === 'admin') ? '' : 'none';\n"
    "    document.getElementById('nav-meta-assign').style.display = (currentUser && currentUser.role === 'admin') ? '' : 'none';"
)

# Add event listener
content = content.replace(
    "document.getElementById('nav-meta').addEventListener('click', function() { switchTab('meta'); });",
    "document.getElementById('nav-meta').addEventListener('click', function() { switchTab('meta'); });\n"
    "    document.getElementById('nav-meta-assign').addEventListener('click', function() { switchTab('meta-assign'); });"
)
print('4. Added admin visibility + event listener')

# 6. Remove pause button from account tree rendering
# The pause button is in the admin section of renderMetaAccountTree. Remove it.
content = content.replace(
    "var toggleBtn = isAdmin ? '<button class=\"ml-1 w-5 h-5 rounded text-[10px] leading-none flex items-center justify-center border-0 cursor-pointer ' + (isActive ? 'text-slate-500 hover:text-amber-400' : 'text-slate-600 hover:text-emerald-400') + ' transition-all bg-transparent\" onclick=\"event.stopPropagation();toggleMetaAccountStatus(\\'' + actKey + '\\',\\'' + (isActive ? 'paused' : 'active') + '\\')\" title=\"' + (isActive ? '停用' : '启用') + '\">' + (isActive ? '⏸' : '▶') + '</button>' : '';\n",
    "var toggleBtn = '';\n"
)
content = content.replace("'<span class=\"flex-1 truncate\">' + escapeHtml(a.act_name || a.act_id) + '</span>' + toggleBtn",
                         "'<span class=\"flex-1 truncate\">' + escapeHtml(a.act_name || a.act_id) + '</span>'")
print('5. Removed pause button from account tree')

# ===== NEW JS functions to add at end of script =====
assign_js = '''

    // ====== 账户分配 ======
    var _assignAccounts = [];
    var _assignSelected = {};  // act_id -> true

    function loadAssignPage() {
      var targetUser = document.getElementById('assignTargetUser');
      var bmSelect = document.getElementById('assignBmSelect');
      var bmTarget = document.getElementById('assignBmTarget');
      // Load user list
      fetch('/api/users').then(function(r) { return r.json(); }).then(function(users) {
        var userOpts = '<option value="">选择目标用户...</option>';
        users.forEach(function(u) {
          userOpts += '<option value="' + u.id + '">' + escapeHtml(u.display_name || u.username) + '</option>';
        });
        targetUser.innerHTML = userOpts;
        bmTarget.innerHTML = '<option value="">分配给...</option>' + userOpts.map(function(u) { return '<option value="' + u.id + '">' + escapeHtml(u.display_name || u.username) + '</option>'; }).join('');
      });
      // Load all meta accounts
      fetch('/api/meta/accounts').then(function(r) { return r.json(); }).then(function(accounts) {
        _assignAccounts = accounts || [];
        _assignSelected = {};
        renderAssignTree();
        // Populate BM list
        var bms = {};
        _assignAccounts.forEach(function(a) { var bm = a.pingykj_account || '未归类'; if (!bms[bm]) bms[bm] = 0; bms[bm]++; });
        var bmOpts = '<option value="">选择 BM...</option>';
        Object.keys(bms).sort().forEach(function(bm) { bmOpts += '<option value="' + escapeHtml(bm) + '">' + escapeHtml(bm) + ' (' + bms[bm] + ')</option>'; });
        bmSelect.innerHTML = bmOpts;
      });
    }

    function renderAssignTree() {
      var search = (document.getElementById('assignSearch').value || '').toLowerCase();
      var accounts = _assignAccounts;
      if (search) {
        accounts = accounts.filter(function(a) {
          return (a.act_id || '').toLowerCase().indexOf(search) >= 0 ||
                 (a.act_name || '').toLowerCase().indexOf(search) >= 0 ||
                 (a.pingykj_account || '').toLowerCase().indexOf(search) >= 0;
        });
      }
      var groups = {};
      accounts.forEach(function(a) {
        var bm = a.pingykj_account || '未归类';
        if (!groups[bm]) groups[bm] = [];
        groups[bm].push(a);
      });
      var html = '';
      Object.keys(groups).sort().forEach(function(bm) {
        var bmAccounts = groups[bm];
        var allChecked = bmAccounts.every(function(a) { return _assignSelected[a.act_id]; });
        var someChecked = bmAccounts.some(function(a) { return _assignSelected[a.act_id]; });
        html += '<div class="flex items-center gap-2 py-1 px-1 hover:bg-slate-50 rounded cursor-pointer" onclick="event.stopPropagation();var cb=this.querySelector(\'input\');cb.checked=!cb.checked;assignToggleBm(\\'' + bm.replace(/'/g, '\\\\\\'') + '\\',cb.checked);">';
        html += '<input type="checkbox" ' + (allChecked ? 'checked' : '') + ' class="w-3.5 h-3.5 accent-indigo-600 cursor-pointer">';
        html += '<span class="font-medium text-slate-600">' + escapeHtml(bm) + ' (' + bmAccounts.length + ')</span></div>';
        bmAccounts.forEach(function(a) {
          html += '<div class="flex items-center gap-2 py-0.5 px-1 pl-6 hover:bg-slate-50 rounded cursor-pointer text-xs" onclick="assignToggleAccount(\\'' + a.act_id + '\\')">';
          html += '<input type="checkbox" ' + (_assignSelected[a.act_id] ? 'checked' : '') + ' class="w-3 h-3 accent-indigo-600 cursor-pointer" onclick="event.stopPropagation();_assignSelected[\\'' + a.act_id + '\\']=this.checked;updateAssignInfo();renderAssignTree();">';
          html += '<span class="text-slate-500 font-mono">' + escapeHtml(a.act_id) + '</span>';
          html += '<span class="text-slate-600">' + escapeHtml(a.act_name || '') + '</span>';
          var uname = a.user_name || ('用户' + (a.user_id || ''));
          html += '<span class="text-slate-400 text-[10px] ml-auto">👤 ' + escapeHtml(uname) + '</span>';
          html += '</div>';
        });
      });
      document.getElementById('assignAccountTree').innerHTML = html || '<div class="text-slate-400 text-center py-4">暂无账户</div>';
      updateAssignInfo();
    }

    function assignToggleAccount(actId) {
      _assignSelected[actId] = !_assignSelected[actId];
      updateAssignInfo();
      renderAssignTree();
    }

    function assignToggleBm(bm, checked) {
      _assignAccounts.forEach(function(a) {
        if ((a.pingykj_account || '未归类') === bm) _assignSelected[a.act_id] = checked;
      });
      updateAssignInfo();
      renderAssignTree();
    }

    function assignSelectAll() { _assignAccounts.forEach(function(a) { _assignSelected[a.act_id] = true; }); renderAssignTree(); }
    function assignDeselectAll() { _assignSelected = {}; renderAssignTree(); }

    function updateAssignInfo() {
      var count = Object.values(_assignSelected).filter(Boolean).length;
      document.getElementById('assignSelectedInfo').textContent = count ? '已选 ' + count + ' 个账户' : '未选择任何账户';
    }

    async function doAssignAccounts() {
      var targetUserId = document.getElementById('assignTargetUser').value;
      if (!targetUserId) { alert('请选择目标用户'); return; }
      var selected = Object.keys(_assignSelected).filter(function(k) { return _assignSelected[k]; });
      if (!selected.length) { alert('请至少选择一个账户'); return; }
      var resp = await fetch('/api/meta/assign', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({act_ids: selected, user_id: parseInt(targetUserId)})
      });
      var result = await resp.json();
      document.getElementById('assignMsg').textContent = result.message || ('已分配 ' + result.count + ' 个账户');
      _assignSelected = {};
      loadAssignPage();
    }

    async function doAssignBm() {
      var bm = document.getElementById('assignBmSelect').value;
      var targetUserId = document.getElementById('assignBmTarget').value;
      if (!bm || !targetUserId) { alert('请选择 BM 和目标用户'); return; }
      var actIds = _assignAccounts.filter(function(a) { return (a.pingykj_account || '未归类') === bm; }).map(function(a) { return a.act_id; });
      var resp = await fetch('/api/meta/assign', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({act_ids: actIds, user_id: parseInt(targetUserId)})
      });
      var result = await resp.json();
      document.getElementById('assignBmMsg').textContent = result.message || ('已分配 ' + result.count + ' 个账户');
      loadAssignPage();
    }

    document.getElementById('assignSearch').addEventListener('input', renderAssignTree);
'''

# Insert before closing </script> tag
end_script = content.rfind('</script>')
content = content[:end_script] + assign_js + '\n' + content[end_script:]
print('6. Added account assignment JS')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('\nFrontend done')
