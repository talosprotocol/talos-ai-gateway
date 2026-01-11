"""Dashboard UI - Production version using Admin API."""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from app.dependencies import get_upstream_store, get_model_group_store
from app.domain.interfaces import UpstreamStore, ModelGroupStore

router = APIRouter()

# ============ Dashboard Router ============

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    u_store: UpstreamStore = Depends(get_upstream_store),
    mg_store: ModelGroupStore = Depends(get_model_group_store)
):
    """Serve the production dashboard."""
    upstreams = u_store.list_upstreams()
    model_groups = mg_store.list_model_groups()
    
    # We will fetch templates client-side from the catalog API
    provider_options = "" 
    upstream_options = "".join([f'<option value="{u["id"]}">{u["id"]}</option>' for u in upstreams])
    model_options = "".join([f'<option value="{g["id"]}">{g.get("name", g["id"])}</option>' for g in model_groups])
    
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Talos AI Gateway</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #ffffff;
            --bg-alt: #f8fafc;
            --bg-card: #ffffff;
            --border: #e2e8f0;
            --text: #0f172a;
            --text-sec: #64748b;
            --text-muted: #94a3b8;
            --accent: #2563eb;
            --accent-hover: #1d4ed8;
            --accent-bg: #eff6ff;
            --success: #059669;
            --success-bg: #ecfdf5;
            --warning: #d97706;
            --warning-bg: #fffbeb;
            --danger: #dc2626;
            --danger-bg: #fef2f2;
            --shadow: 0 1px 3px rgba(0,0,0,0.1);
            --radius: 8px;
        }}
        [data-theme="dark"] {{
            --bg: #0f172a;
            --bg-alt: #1e293b;
            --bg-card: #1e293b;
            --border: #334155;
            --text: #f1f5f9;
            --text-sec: #94a3b8;
            --text-muted: #64748b;
            --accent: #3b82f6;
            --accent-hover: #60a5fa;
            --accent-bg: rgba(59,130,246,0.15);
            --success-bg: rgba(5,150,105,0.15);
            --warning-bg: rgba(217,119,6,0.15);
            --danger-bg: rgba(220,38,38,0.15);
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:'Inter',sans-serif; background:var(--bg-alt); color:var(--text); line-height:1.5; }}
        
        .nav {{ background:var(--bg-card); border-bottom:1px solid var(--border); padding:0.75rem 1.5rem; display:flex; justify-content:space-between; align-items:center; position:sticky; top:0; z-index:100; }}
        .logo {{ display:flex; align-items:center; gap:0.5rem; font-weight:700; font-size:1.125rem; }}
        .logo-icon {{ width:32px; height:32px; background:linear-gradient(135deg,#2563eb,#7c3aed); border-radius:8px; display:flex; align-items:center; justify-content:center; color:#fff; font-size:1rem; }}
        .nav-right {{ display:flex; align-items:center; gap:0.75rem; }}
        .principal {{ font-size:0.75rem; color:var(--text-sec); padding:0.375rem 0.75rem; background:var(--bg-alt); border-radius:6px; }}
        
        .container {{ max-width:1280px; margin:0 auto; padding:1.5rem; }}
        
        .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1rem; margin-bottom:1.5rem; }}
        @media(max-width:768px) {{ .stats {{ grid-template-columns:repeat(2,1fr); }} }}
        .stat {{ background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius); padding:1rem; }}
        .stat-label {{ font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; }}
        .stat-value {{ font-size:1.75rem; font-weight:700; margin-top:0.25rem; }}
        .stat-value.accent {{ color:var(--accent); }}
        .stat-value.success {{ color:var(--success); }}
        
        .section {{ margin-bottom:1.5rem; }}
        .section-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem; }}
        .section-title {{ font-size:1rem; font-weight:600; }}
        
        .btn {{ padding:0.5rem 1rem; border-radius:6px; font-size:0.8125rem; font-weight:500; cursor:pointer; border:none; transition:all 0.15s; display:inline-flex; align-items:center; gap:0.375rem; }}
        .btn-primary {{ background:var(--accent); color:#fff; }}
        .btn-primary:hover {{ background:var(--accent-hover); }}
        .btn-secondary {{ background:var(--bg-alt); color:var(--text); border:1px solid var(--border); }}
        .btn-secondary:hover {{ background:var(--border); }}
        .btn-danger {{ background:var(--danger-bg); color:var(--danger); }}
        .btn-sm {{ padding:0.375rem 0.625rem; font-size:0.75rem; }}
        
        .table-wrap {{ background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; }}
        table {{ width:100%; border-collapse:collapse; font-size:0.875rem; }}
        th,td {{ padding:0.75rem 1rem; text-align:left; border-bottom:1px solid var(--border); }}
        th {{ background:var(--bg-alt); font-size:0.75rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-sec); }}
        tr:last-child td {{ border-bottom:none; }}
        tr:hover {{ background:var(--accent-bg); }}
        
        .badge {{ padding:0.25rem 0.5rem; border-radius:4px; font-size:0.6875rem; font-weight:600; text-transform:uppercase; letter-spacing:0.03em; }}
        .badge-ok {{ background:var(--success-bg); color:var(--success); }}
        .badge-warn {{ background:var(--warning-bg); color:var(--warning); }}
        .badge-err {{ background:var(--danger-bg); color:var(--danger); }}
        .badge-provider {{ background:var(--accent-bg); color:var(--accent); }}
        
        .endpoint {{ max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-sec); font-size:0.8125rem; }}
        .actions {{ display:flex; gap:0.375rem; }}
        
        .modal-bg {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:200; align-items:center; justify-content:center; }}
        .modal-bg.open {{ display:flex; }}
        .modal {{ background:var(--bg-card); border-radius:12px; width:100%; max-width:480px; max-height:90vh; overflow-y:auto; }}
        .modal-head {{ padding:1rem 1.25rem; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }}
        .modal-title {{ font-weight:600; }}
        .modal-close {{ background:none; border:none; font-size:1.25rem; cursor:pointer; color:var(--text-muted); }}
        .modal-body {{ padding:1.25rem; }}
        .modal-foot {{ padding:0.75rem 1.25rem; border-top:1px solid var(--border); display:flex; justify-content:flex-end; gap:0.5rem; }}
        
        .form-group {{ margin-bottom:1rem; }}
        .form-label {{ display:block; font-size:0.8125rem; font-weight:500; margin-bottom:0.375rem; color:var(--text-sec); }}
        .form-input,.form-select {{ width:100%; padding:0.625rem 0.75rem; border:1px solid var(--border); border-radius:6px; background:var(--bg-alt); color:var(--text); font-size:0.875rem; }}
        .form-input:focus,.form-select:focus {{ outline:none; border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-bg); }}
        .form-hint {{ font-size:0.75rem; color:var(--text-muted); margin-top:0.25rem; }}
        
        .test-box {{ background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius); padding:1.25rem; }}
        .test-box h3 {{ font-size:0.9375rem; margin-bottom:0.75rem; }}
        .test-row {{ display:grid; grid-template-columns:180px 1fr auto; gap:0.75rem; align-items:end; }}
        @media(max-width:640px) {{ .test-row {{ grid-template-columns:1fr; }} }}
        .response {{ margin-top:0.75rem; padding:0.75rem; background:var(--bg-alt); border-radius:6px; font-family:monospace; font-size:0.8125rem; min-height:60px; white-space:pre-wrap; }}
        .response.ok {{ background:var(--success-bg); color:var(--success); }}
        
        .theme-btn {{ background:var(--bg-alt); border:1px solid var(--border); border-radius:6px; padding:0.5rem; cursor:pointer; }}
    </style>
</head>
<body>
    <nav class="nav">
        <div class="logo"><div class="logo-icon">⚡</div>Talos AI Gateway</div>
        <div class="nav-right">
            <span class="principal" id="principal">Loading...</span>
            <button class="theme-btn" onclick="toggleTheme()">🌓</button>
            <a href="/docs" class="btn btn-secondary">API Docs</a>
        </div>
    </nav>
    
    <div class="container">
        <div class="stats">
            <div class="stat"><div class="stat-label">Providers</div><div class="stat-value accent" id="stat-providers">{len(upstreams)}</div></div>
            <div class="stat"><div class="stat-label">Models</div><div class="stat-value accent" id="stat-models">{len(model_groups)}</div></div>
            <div class="stat"><div class="stat-label">Status</div><div class="stat-value success">Online</div></div>
            <div class="stat"><div class="stat-label">Version</div><div class="stat-value">0.1.0</div></div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">LLM Providers</h2>
                <button class="btn btn-primary" onclick="openModal('provider')">+ Add Provider</button>
            </div>
            <div class="table-wrap">
                <table>
                    <thead><tr><th>ID</th><th>Provider</th><th>Endpoint</th><th>Secret</th><th>Health</th><th>Actions</th></tr></thead>
                    <tbody id="providers-tbody"></tbody>
                </table>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">Model Groups</h2>
                <button class="btn btn-primary" onclick="openModal('model')">+ Add Model</button>
            </div>
            <div class="table-wrap">
                <table>
                    <thead><tr><th>ID</th><th>Name</th><th>Deployments</th><th>Provider</th><th>Actions</th></tr></thead>
                    <tbody id="models-tbody"></tbody>
                </table>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">MCP Servers</h2>
                <button class="btn btn-primary" onclick="openModal('mcpServer')">+ Add Server</button>
            </div>
            <div class="table-wrap">
                <table>
                    <thead><tr><th>ID</th><th>Transport</th><th>Command/URL</th><th>Status</th><th>Actions</th></tr></thead>
                    <tbody id="mcp-servers-tbody"></tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <div class="section-header">
                <h2 class="section-title">Secrets</h2>
                <button class="btn btn-primary" onclick="openModal('secret')">+ Add Secret</button>
            </div>
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Name</th><th>Value</th><th>Actions</th></tr></thead>
                    <tbody id="secrets-tbody"></tbody>
                </table>
            </div>
        </div>
        
        <div class="section">
            <div class="test-box">
                <h3>🧪 Test Chat</h3>
                <div class="test-row">
                    <select id="testModel" class="form-select">{model_options}</select>
                    <input type="text" id="testPrompt" class="form-input" value="Hello! Tell me a joke." placeholder="Message...">
                    <button class="btn btn-primary" onclick="testChat()">Send</button>
                </div>
                <div id="testResponse" class="response">Response will appear here...</div>
            </div>
        </div>
    </div>
    
    <!-- Provider Modal -->
    <div id="providerModal" class="modal-bg">
        <div class="modal">
            <div class="modal-head"><span class="modal-title">Add Provider</span><button class="modal-close" onclick="closeModal('provider')">&times;</button></div>
            <div class="modal-body">
                <div class="form-group"><label class="form-label">Provider ID *</label><input type="text" id="pId" class="form-input" placeholder="my-openai"></div>
                <div class="form-group"><label class="form-label">Provider Type *</label><select id="pType" class="form-select" onchange="autofillEndpoint()">{provider_options}</select></div>
                <div class="form-group"><label class="form-label">Endpoint *</label><input type="text" id="pEndpoint" class="form-input" placeholder="https://api.openai.com/v1"></div>
                <div class="form-group"><label class="form-label">Secret Reference</label><input type="text" id="pSecret" class="form-input" placeholder="env:OPENAI_API_KEY or secret:my-key"><div class="form-hint">Use env:VAR or secret:name (never raw keys)</div></div>
            </div>
            <div class="modal-foot"><button class="btn btn-secondary" onclick="closeModal('provider')">Cancel</button><button class="btn btn-primary" onclick="saveProvider()">Save</button></div>
        </div>
    </div>
    
    <!-- Model Modal -->
    <div id="modelModal" class="modal-bg">
        <div class="modal">
            <div class="modal-head"><span class="modal-title">Add Model Group</span><button class="modal-close" onclick="closeModal('model')">&times;</button></div>
            <div class="modal-body">
                <div class="form-group"><label class="form-label">Model Group ID *</label><input type="text" id="mId" class="form-input" placeholder="my-gpt4"></div>
                <div class="form-group"><label class="form-label">Display Name *</label><input type="text" id="mName" class="form-input" placeholder="My GPT-4"></div>
                <div class="form-group"><label class="form-label">Provider *</label><select id="mUpstream" class="form-select">{upstream_options}</select></div>
                <div class="form-group"><label class="form-label">Model Name *</label><input type="text" id="mModel" class="form-input" placeholder="gpt-4-turbo"></div>
            </div>
            <div class="modal-foot"><button class="btn btn-secondary" onclick="closeModal('model')">Cancel</button><button class="btn btn-primary" onclick="saveModel()">Save</button></div>
        </div>
    </div>
    
    <!-- MCP Server Modal -->
    <div id="mcpServerModal" class="modal-bg">
        <div class="modal">
            <div class="modal-head"><span class="modal-title">Add MCP Server</span><button class="modal-close" onclick="closeModal('mcpServer')">&times;</button></div>
            <div class="modal-body">
                <div class="form-group"><label class="form-label">Server ID *</label><input type="text" id="mcpId" class="form-input" placeholder="filesystem"></div>
                <div class="form-group"><label class="form-label">Transport *</label><select id="mcpTransport" class="form-select"><option value="stdio">Stdio</option><option value="sse">SSE</option></select></div>
                <div class="form-group"><label class="form-label">Command / URL *</label><input type="text" id="mcpCommand" class="form-input" placeholder="npx -y @modelcontextprotocol/server-filesystem"></div>
                <div class="form-group"><label class="form-label">Arguments</label><input type="text" id="mcpArgs" class="form-input" placeholder="/path/to/files"></div>
            </div>
            <div class="modal-foot"><button class="btn btn-secondary" onclick="closeModal('mcpServer')">Cancel</button><button class="btn btn-primary" onclick="saveMcpServer()">Save</button></div>
        </div>
    </div>

    <!-- Secret Modal -->
    <div id="secretModal" class="modal-bg">
        <div class="modal">
            <div class="modal-head"><span class="modal-title">Add Secret</span><button class="modal-close" onclick="closeModal('secret')">&times;</button></div>
            <div class="modal-body">
                <div class="form-group"><label class="form-label">Name *</label><input type="text" id="sName" class="form-input" placeholder="my-api-key"></div>
                <div class="form-group"><label class="form-label">Value *</label><input type="password" id="sValue" class="form-input" placeholder="sk-..."></div>
                <div class="form-hint">Stored securely. Only name works as reference: secret:name</div>
            </div>
            <div class="modal-foot"><button class="btn btn-secondary" onclick="closeModal('secret')">Cancel</button><button class="btn btn-primary" onclick="saveSecret()">Save</button></div>
        </div>
    </div>
    
    <script>
        let templates = {{}};
        
        // Theme
        document.documentElement.setAttribute('data-theme', localStorage.getItem('theme') || 'light');
        function toggleTheme() {{
            const t = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', t);
            localStorage.setItem('theme', t);
        }}
        
        // Load principal
        fetch('/admin/v1/me').then(r=>r.json()).then(d=>{{
            document.getElementById('principal').textContent = d.id + ' [' + d.scopes[0] + ']';
        }});

        // Load catalog
        fetch('/admin/v1/catalog/provider-templates').then(r=>r.json()).then(d=>{{
            templates = {{}};
            const select = document.getElementById('pType');
            select.innerHTML = '';
            
            d.templates.forEach(t => {{
                templates[t.provider_key] = t;
                const option = document.createElement('option');
                option.value = t.provider_key;
                option.textContent = t.display_name;
                select.appendChild(option);
            }});
        }});
        
        // Load data
        function loadData() {{
            fetch('/admin/v1/llm/upstreams').then(r=>r.json()).then(d=>{{
                const tbody = document.getElementById('providers-tbody');
                tbody.innerHTML = d.upstreams.map(u=>{{
                    const secret = u.credentials_ref ? (u.credentials_ref.startsWith('env:') || u.credentials_ref.startsWith('secret:') ? '✓ Configured' : '⚠ Direct') : '✗ Not set';
                    const secretClass = u.credentials_ref ? (u.credentials_ref.startsWith('env:') || u.credentials_ref.startsWith('secret:') ? 'ok' : 'warn') : 'err';
                    const enabled = u.enabled !== false;
                    return `<tr>
                        <td><strong>${{u.id}}</strong></td>
                        <td><span class="badge badge-provider">${{u.provider}}</span></td>
                        <td class="endpoint" title="${{u.endpoint}}">${{u.endpoint?.slice(0,40) || ''}}</td>
                        <td><span class="badge badge-${{secretClass}}">${{secret}}</span></td>
                        <td><span class="badge badge-${{enabled ? 'ok' : 'warn'}}">${{enabled ? 'OK' : 'Disabled'}}</span></td>
                        <td class="actions">
                            <button class="btn btn-sm btn-secondary" onclick="toggleUpstream('${{u.id}}', ${{!enabled}})"><span>${{enabled ? 'Disable' : 'Enable'}}</span></button>
                            <button class="btn btn-sm btn-danger" onclick="deleteUpstream('${{u.id}}')">Delete</button>
                        </td>
                    </tr>`;
                }}).join('');
                document.getElementById('stat-providers').textContent = d.upstreams.length;
            }});
            
            fetch('/admin/v1/llm/model-groups').then(r=>r.json()).then(d=>{{
                const tbody = document.getElementById('models-tbody');
                tbody.innerHTML = d.model_groups.map(g=>{{
                    const deps = g.deployments || [];
                    const upstream = deps[0]?.upstream_id || '';
                    const model = deps[0]?.model_name || '';
                    return `<tr>
                        <td><strong>${{g.id}}</strong></td>
                        <td>${{g.name || ''}}</td>
                        <td>${{deps.length}}</td>
                        <td><code>${{upstream}}: ${{model}}</code></td>
                        <td class="actions">
                            <button class="btn btn-sm btn-danger" onclick="deleteModel('${{g.id}}')">Delete</button>
                        </td>
                    </tr>`;
                }}).join('');
                document.getElementById('stat-models').textContent = d.model_groups.length;
            }});

            fetch('/admin/v1/mcp/servers').then(r=>r.json()).then(d=>{{
                const tbody = document.getElementById('mcp-servers-tbody');
                tbody.innerHTML = d.servers.map(s=>{{
                    const enabled = s.enabled !== false;
                    return `<tr>
                        <td><strong>${{s.id}}</strong></td>
                        <td>${{s.transport}}</td>
                        <td class="endpoint" title="${{s.command}}">${{s.command?.slice(0,40) || ''}}</td>
                        <td><span class="badge badge-${{enabled ? 'ok' : 'warn'}}">${{enabled ? 'OK' : 'Disabled'}}</span></td>
                        <td class="actions">
                            <button class="btn btn-sm btn-secondary" onclick="toggleMcpServer('${{s.id}}', ${{!enabled}})"><span>${{enabled ? 'Disable' : 'Enable'}}</span></button>
                            <button class="btn btn-sm btn-danger" onclick="deleteMcpServer('${{s.id}}')">Delete</button>
                        </td>
                    </tr>`;
                }}).join('');
            }});

            fetch('/admin/v1/secrets').then(r=>r.json()).then(d=>{{
                const tbody = document.getElementById('secrets-tbody');
                tbody.innerHTML = d.secrets.map(s=>{{
                    return `<tr>
                        <td><strong>${{s.name}}</strong></td>
                        <td><code>******</code></td>
                        <td class="actions">
                            <button class="btn btn-sm btn-danger" onclick="deleteSecret('${{s.name}}')">Delete</button>
                        </td>
                    </tr>`;
                }}).join('');
            }});
        }}
        loadData();
        
        // Modals
        function openModal(type) {{ document.getElementById(type+'Modal').classList.add('open'); }}
        function closeModal(type) {{ document.getElementById(type+'Modal').classList.remove('open'); }}
        
        function autofillEndpoint() {{
            const type = document.getElementById('pType').value;
            if (templates[type]) {{
                document.getElementById('pEndpoint').value = templates[type].default_base_url || '';
                // Also show auth hint if available (to be implemented more fully later)
                 const secretInput = document.getElementById('pSecret');
                 if (templates[type].secret_ref_hints && templates[type].secret_ref_hints.length > 0) {{
                     secretInput.placeholder = templates[type].secret_ref_hints[0];
                 }}
            }}
        }}
        
        async function saveProvider() {{
            const data = {{
                id: document.getElementById('pId').value,
                provider: document.getElementById('pType').value,
                endpoint: document.getElementById('pEndpoint').value,
                credentials_ref: document.getElementById('pSecret').value,
                enabled: true
            }};
            const r = await fetch('/admin/v1/llm/upstreams', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(data)}});
            if (r.ok) {{ closeModal('provider'); loadData(); }} else {{ alert('Error: ' + (await r.json()).detail?.error?.message); }}
        }}
        
        async function saveModel() {{
            const data = {{
                id: document.getElementById('mId').value,
                name: document.getElementById('mName').value,
                deployments: [{{upstream_id: document.getElementById('mUpstream').value, model_name: document.getElementById('mModel').value, weight: 100}}],
                fallback_groups: []
            }};
            const r = await fetch('/admin/v1/llm/model-groups', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(data)}});
            if (r.ok) {{ closeModal('model'); loadData(); }} else {{ alert('Error: ' + (await r.json()).detail?.error?.message); }}
        }}

        async function saveMcpServer() {{
            const data = {{
                id: document.getElementById('mcpId').value,
                transport: document.getElementById('mcpTransport').value,
                command: document.getElementById('mcpCommand').value,
                args: document.getElementById('mcpArgs').value.split(' '),
                env: {{}},
                enabled: true
            }};
            const r = await fetch('/admin/v1/mcp/servers', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(data)}});
            if (r.ok) {{ closeModal('mcpServer'); loadData(); }} else {{ alert('Error: ' + (await r.json()).detail?.error?.message); }}
        }}

        async function saveSecret() {{
            const data = {{
                name: document.getElementById('sName').value,
                value: document.getElementById('sValue').value
            }};
            const r = await fetch('/admin/v1/secrets', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(data)}});
            if (r.ok) {{ closeModal('secret'); loadData(); }} else {{ alert('Error: ' + (await r.json()).detail?.error?.message); }}
        }}
        
        async function toggleUpstream(id, enable) {{
            await fetch(`/admin/v1/llm/upstreams/${{id}}:${{enable ? 'enable' : 'disable'}}`, {{method:'POST'}});
            loadData();
        }}

        async function toggleMcpServer(id, enable) {{
            await fetch(`/admin/v1/mcp/servers/${{id}}:${{enable ? 'enable' : 'disable'}}`, {{method:'POST'}});
            loadData();
        }}
        
        async function deleteUpstream(id) {{
            if (!confirm('Delete provider ' + id + '?')) return;
            const r = await fetch(`/admin/v1/llm/upstreams/${{id}}`, {{method:'DELETE'}});
            if (r.ok) loadData();
            else {{
                const d = await r.json();
                if (d.detail?.error?.code === 'DEPENDENCY_EXISTS') {{
                    alert('Cannot delete: has ' + d.detail.error.dependents.length + ' dependent model groups');
                }} else alert('Error: ' + d.detail?.error?.message);
            }}
        }}
        
        async function deleteModel(id) {{
            if (!confirm('Delete model group ' + id + '?')) return;
            const r = await fetch(`/admin/v1/llm/model-groups/${{id}}`, {{method:'DELETE'}});
            if (r.ok) loadData();
        }}

        async function deleteMcpServer(id) {{
            if (!confirm('Delete MCP server ' + id + '?')) return;
            const r = await fetch(`/admin/v1/mcp/servers/${{id}}`, {{method:'DELETE'}});
            if (r.ok) loadData();
        }}

        async function deleteSecret(name) {{
            if (!confirm('Delete secret ' + name + '?')) return;
            const r = await fetch(`/admin/v1/secrets/${{name}}`, {{method:'DELETE'}});
            if (r.ok) loadData();
        }}
        
        async function testChat() {{
            const box = document.getElementById('testResponse');
            box.className = 'response';
            box.textContent = 'Loading...';
            try {{
                const r = await fetch('/v1/chat/completions', {{
                    method:'POST',
                    headers:{{'Content-Type':'application/json', 'Authorization':'Bearer sk-test-key-1'}},
                    body:JSON.stringify({{model: document.getElementById('testModel').value, messages:[{{role:'user', content: document.getElementById('testPrompt').value}}]}})
                }});
                const d = await r.json();
                if (d.choices?.[0]) {{
                    box.className = 'response ok';
                    box.textContent = '✓ ' + d.model + '\\n\\n' + d.choices[0].message.content;
                }} else box.textContent = JSON.stringify(d, null, 2);
            }} catch(e) {{ box.textContent = 'Error: ' + e.message; }}
        }}
    </script>
</body>
</html>
"""
    return html


@router.get("/api/upstreams")
async def get_upstreams(store: UpstreamStore = Depends(get_upstream_store)):
    return {"upstreams": store.list_upstreams()}


@router.get("/api/model-groups")
async def get_model_groups(store: ModelGroupStore = Depends(get_model_group_store)):
    return {"model_groups": store.list_model_groups()}
