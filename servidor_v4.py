#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel CRM Kommo — Servidor v4
- Login com senha por cliente
- Admin master: vê todos os painéis
- Pode bloquear/desbloquear clientes
- White label por cliente
"""
import json, os, re, ssl, sys, time, threading, urllib.request, urllib.error, hashlib, secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
USERS_FILE  = BASE_DIR / "users.json"
CACHE_FILE  = BASE_DIR / "crm_cache.json"

# ─── CONFIG ───────────────────────────────────────────────────────────────────
def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        try: cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except: pass
    kommo   = cfg.get("kommo", {})
    empresa = cfg.get("empresa", {})
    painel  = cfg.get("painel", {})
    return {
        "subdomain":       os.environ.get("KOMMO_SUBDOMAIN") or kommo.get("subdomain", ""),
        "token":           os.environ.get("KOMMO_TOKEN")      or kommo.get("token", ""),
        "nome_empresa":    os.environ.get("EMPRESA_NOME")     or empresa.get("nome", "Painel CRM"),
        "logo_url":        os.environ.get("EMPRESA_LOGO")     or empresa.get("logo_url", ""),
        "cor_primaria":    os.environ.get("COR_PRIMARIA")     or empresa.get("cor_primaria", "#3b82f6"),
        "cor_secundaria":  os.environ.get("COR_SECUNDARIA")   or empresa.get("cor_secundaria", "#1e3a8a"),
        "porta":           int(os.environ.get("PORT", 8080)),
        "refresh_minutos": int(os.environ.get("REFRESH_MINUTOS") or painel.get("refresh_minutos", 30)),
        "meta_mes_padrao": int(os.environ.get("META_MES")        or painel.get("meta_mes_padrao", 50000)),
        # Admin master
        "admin_user":      os.environ.get("ADMIN_USER", "admin"),
        "admin_pass":      os.environ.get("ADMIN_PASS", "admin123"),
    }

CFG = load_config()

# ─── USERS / CLIENTES ─────────────────────────────────────────────────────────
_users_db = {}

def load_users():
    global _users_db
    if USERS_FILE.exists():
        try:
            _users_db = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return dict(_users_db)
        except: pass
    env_data = os.environ.get("USERS_DATA", "")
    if env_data:
        try: _users_db = json.loads(env_data)
        except: pass
    return dict(_users_db)

def save_users(users):
    global _users_db
    _users_db = dict(users)
    try:
        USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass

def get_users():
    return dict(_users_db)

def hash_pass(p):
    return hashlib.sha256(p.encode()).hexdigest()

def verify_pass(plain, hashed):
    return hash_pass(plain) == hashed

# ─── SESSÕES ──────────────────────────────────────────────────────────────────
_sessions = {}  # token -> {"user": "...", "expires": datetime, "is_admin": bool}
_sess_lock = threading.Lock()

def create_session(username, is_admin=False):
    token = secrets.token_hex(32)
    with _sess_lock:
        _sessions[token] = {
            "user":     username,
            "is_admin": is_admin,
            "expires":  datetime.now() + timedelta(hours=12),
        }
    return token

def get_session(token):
    if not token: return None
    with _sess_lock:
        s = _sessions.get(token)
        if s and s["expires"] > datetime.now():
            return s
        if s: del _sessions[token]
    return None

def delete_session(token):
    with _sess_lock:
        _sessions.pop(token, None)

def get_cookie(handler, name):
    cookie_hdr = handler.headers.get("Cookie", "")
    for part in cookie_hdr.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == name:
            return v.strip()
    return None

# ─── CACHE POR USUÁRIO ────────────────────────────────────────────────────────
_caches = {}   # username -> {dados, atualizado_em, status, erro}
_cache_locks = {}

def get_cache(username):
    if username not in _caches:
        _caches[username] = {"dados": None, "atualizado_em": None, "status": "aguardando", "erro": None}
        _cache_locks[username] = threading.Lock()
    return _caches[username], _cache_locks[username]

# ─── KOMMO API ────────────────────────────────────────────────────────────────
CTX = ssl.create_default_context()

def kommo_get(url, token, tentativas=3):
    headers = {"Authorization": f"Bearer {token}"}
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
                body = r.read()
                return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as e:
            if e.code == 204: return None
            if e.code in (429, 503) and t < tentativas - 1:
                time.sleep(2**t); continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:150].decode('utf-8','replace')}")
        except Exception as e:
            if t < tentativas - 1: time.sleep(1); continue
            raise

def fetch_all(base_url, token, endpoint, emb_key, extra=""):
    items, page = [], 1
    sep = "&" if "?" in endpoint else "?"
    while True:
        data = kommo_get(f"{base_url}{endpoint}{sep}limit=250&page={page}{extra}", token)
        if not data: break
        batch = (data.get("_embedded") or {}).get(emb_key) or []
        items.extend(batch)
        if len(batch) < 250: break
        page += 1
        time.sleep(0.12)
    return items

def detect_tipo(name):
    n = (name or "").lower()
    if re.search(r"ganho|won|sucesso|fechad|venda|conclu|quente|sold", n): return "ganho"
    if re.search(r"perdid|lost|cancel|rejeit|recus|fail|descart|frio",  n): return "perdido"
    return "andamento"

def is_phone_only(s):
    return bool(s and re.fullmatch(r"[\d\s+()\-\.]+", s.strip()))

def get_phone(contact):
    for cf in (contact.get("custom_fields_values") or []):
        if cf.get("field_code") == "PHONE":
            vals = cf.get("values") or []
            if vals: return re.sub(r"\D", "", str(vals[0].get("value", "")))
    return ""

def buscar_kommo_user(username, subdomain, token):
    cache, lock = get_cache(username)
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Buscando dados: {username} ({subdomain})")
    with lock: cache["status"] = "atualizando"
    base_url = f"https://{subdomain}.kommo.com/api/v4"
    try:
        pd = kommo_get(f"{base_url}/leads/pipelines?limit=250", token)
        pipelines = (pd.get("_embedded") or {}).get("pipelines") or []
        status_map, funil_map = {}, {}
        for p in pipelines:
            funil_map[p["id"]] = p["name"]
            for s in (p.get("_embedded") or {}).get("statuses") or []:
                tipo = "andamento"
                if s.get("type") == 142 or detect_tipo(s["name"]) == "ganho":   tipo = "ganho"
                if s.get("type") == 143 or detect_tipo(s["name"]) == "perdido": tipo = "perdido"
                status_map[s["id"]] = {"label": s["name"], "funil": p["name"], "tipo": tipo}

        users    = fetch_all(base_url, token, "/users", "users")
        user_map = {u["id"]: u.get("name") or f"User {u['id']}" for u in users}
        leads_raw = fetch_all(base_url, token, "/leads", "leads", "&with=contacts,tags")

        contact_ids = set()
        for l in leads_raw:
            for c in (l.get("_embedded") or {}).get("contacts") or []:
                if c.get("id"): contact_ids.add(c["id"])

        # fetch phones
        ids = list(contact_ids)
        phone_map = {}
        BATCH = 50
        for i in range(0, len(ids), BATCH):
            batch = ids[i:i+BATCH]
            query = "&".join(f"id[]={cid}" for cid in batch)
            url = f"{base_url}/contacts?{query}&with=custom_fields_values&limit=250"
            try:
                data = kommo_get(url, token)
                if data:
                    for c in (data.get("_embedded") or {}).get("contacts") or []:
                        ph = get_phone(c)
                        if ph: phone_map[c["id"]] = ph
            except: pass

        all_leads = []
        for l in leads_raw:
            contacts  = (l.get("_embedded") or {}).get("contacts") or []
            tags_list = (l.get("_embedded") or {}).get("tags")     or []
            tags_str  = ",".join(t.get("name", "") for t in tags_list)
            phone = ""
            for c in contacts:
                cid = c.get("id")
                if cid and cid in phone_map:
                    phone = phone_map[cid]; break
            has_phone = bool(phone)
            text = (tags_str).lower()
            if "tiktok" in text:      canal = "TikTok"
            elif "instagram" in text: canal = "Instagram"
            elif "facebook" in text:  canal = "Outros"
            elif has_phone:           canal = "WhatsApp"
            else:                     canal = "Outros"
            funil   = funil_map.get(l.get("pipeline_id"), f"Funil {l.get('pipeline_id')}")
            nome    = l.get("name") or ""
            cliente = "Lead sem nome" if is_phone_only(nome) or not nome else nome
            all_leads.append({
                "id": str(l["id"]), "data": l.get("created_at") or 0,
                "modificado": l.get("updated_at") or 0,
                "vendedor": user_map.get(l.get("responsible_user_id"), "Nao atribuido"),
                "cliente": cliente, "telefone": phone, "valor": l.get("price") or 0,
                "canal": canal, "funil": funil, "etapa": l.get("status_id"), "tags": tags_str,
            })

        funil_counts = {}
        for l in all_leads: funil_counts[l["funil"]] = funil_counts.get(l["funil"], 0) + 1

        resultado = {
            "leads": all_leads, "status_map": status_map,
            "canais": ["WhatsApp", "Instagram", "TikTok", "Outros"],
            "funis": sorted(funil_counts.keys()), "total": len(all_leads),
        }
        with lock:
            cache["dados"] = resultado
            cache["atualizado_em"] = datetime.now().isoformat()
            cache["status"] = "ok"
            cache["erro"]   = None
        print(f"  [OK] {username}: {len(all_leads)} leads")
    except Exception as e:
        with lock:
            cache["status"] = "erro"
            cache["erro"]   = str(e)
        print(f"  [ERRO] {username}: {e}")

def auto_refresh():
    while True:
        time.sleep(CFG["refresh_minutos"] * 60)
        users = load_users()
        for uname, udata in users.items():
            if udata.get("ativo", True):
                threading.Thread(
                    target=buscar_kommo_user,
                    args=(uname, udata.get("kommo_subdomain",""), udata.get("kommo_token","")),
                    daemon=True
                ).start()

# ─── HTML ─────────────────────────────────────────────────────────────────────
PAINEL_HTML = BASE_DIR / "painel_servidor_v2.html"

def get_painel_html(user_cfg, cache_data=None, is_admin_view=False):
    if not PAINEL_HTML.exists(): return None
    html = PAINEL_HTML.read_text(encoding="utf-8")
    cor1 = user_cfg.get("cor_primaria", CFG["cor_primaria"])
    cor2 = user_cfg.get("cor_secundaria", CFG["cor_secundaria"])
    nome = user_cfg.get("nome", CFG["nome_empresa"])
    logo = user_cfg.get("logo_url", CFG["logo_url"])
    wl_css = f"\n    --bluel:{cor1};--blue2:{cor2};--blue:{cor2};\n"
    html = html.replace(":root{", f":root{{\n{wl_css}", 1)
    html = html.replace("<title>Painel CRM — Live</title>", f"<title>{nome} — Painel</title>")
    html = html.replace("<h1>Painel CRM — Live</h1>", f"<h1>{nome}</h1>")
    if logo:
        html = html.replace("<h1>", f'<h1><img src="{logo}" style="height:28px;border-radius:4px;margin-right:8px" alt="">', 1)
    if is_admin_view:
        back_btn = '<a href="/admin" style="position:fixed;top:14px;right:16px;z-index:9999;background:#1e3a8a;color:#fff;font-size:12px;font-weight:700;padding:7px 14px;border-radius:8px;text-decoration:none;border:1px solid #2c4dbd">← Voltar ao Admin</a>'
        html = html.replace("</body>", f"{back_btn}</body>", 1)
    if cache_data:
        cache_json = json.dumps(cache_data, ensure_ascii=False, separators=(",", ":"))
        inject = f"""const _DADOS_EMBUTIDOS={cache_json};
async function carregarDados(forcar=false){{
  try{{
    const j={{status:'ok',atualizado_em:new Date().toISOString(),erro:null,dados:_DADOS_EMBUTIDOS}};
    if(j.dados){{
      if(j.atualizado_em!==_lastUpdate||forcar){{
        _lastUpdate=j.atualizado_em;
        RAW_DATA=j.dados.leads||[];
        STATUS_MAP=j.dados.status_map||{{}};
        FUNIS=j.dados.funis||[];
        preComputeLeads();
        renderFunilBar();
        render(currentRange);
        document.getElementById('loadingScreen').style.display='none';
      }}
      setPill('live','Visualizacao admin');
    }}
  }}catch(e){{console.error(e);}}
}}"""
        old_func = "async function carregarDados(forcar=false){"
        if old_func in html:
            html = html.replace(old_func, inject, 1)
        html = html.replace("setInterval(()=>carregarDados(),60000);", "//setInterval desabilitado", 1)
    return html.encode("utf-8")

LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#07090f;color:#eaeef7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}}
.box{{background:#0f1320;border:1px solid #1d2740;border-radius:16px;padding:40px 36px;width:100%;max-width:380px;box-shadow:0 20px 60px rgba(0,0,0,.5)}}
.logo-wrap{{text-align:center;margin-bottom:28px}}
.logo-wrap img{{max-height:48px;border-radius:8px;margin-bottom:12px}}
h1{{font-size:20px;font-weight:700;text-align:center;margin-bottom:4px}}
.sub{{font-size:12.5px;color:#8590a8;text-align:center;margin-bottom:28px}}
label{{font-size:11.5px;color:#8590a8;text-transform:uppercase;letter-spacing:.07em;display:block;margin-bottom:6px}}
input{{width:100%;background:#131a2b;border:1px solid #1d2740;color:#eaeef7;padding:11px 14px;border-radius:8px;font-size:14px;font-family:inherit;outline:none;margin-bottom:16px;transition:border-color .15s}}
input:focus{{border-color:{cor}}}
.btn{{width:100%;background:{cor};border:none;color:#fff;font-size:14px;font-weight:700;padding:12px;border-radius:8px;cursor:pointer;font-family:inherit;transition:opacity .15s;margin-top:4px}}
.btn:hover{{opacity:.85}}
.err{{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#ef4444;padding:10px 14px;border-radius:8px;font-size:12.5px;margin-bottom:16px;display:{err_display}}}
.footer{{text-align:center;margin-top:20px;font-size:11px;color:#5a657d}}
</style>
</head>
<body>
<div class="box">
  <div class="logo-wrap">
    {logo_tag}
    <h1>{title}</h1>
    <p class="sub">Painel CRM · Acesso restrito</p>
  </div>
  <div class="err">{err_msg}</div>
  <form method="POST" action="/login">
    <label>Usuário</label>
    <input type="text" name="username" placeholder="seu usuário" autocomplete="username" required/>
    <label>Senha</label>
    <input type="password" name="password" placeholder="••••••••" autocomplete="current-password" required/>
    <button class="btn" type="submit">Entrar</button>
  </form>
  <p class="footer">Acesso autorizado somente para clientes ativos</p>
</div>
</body>
</html>"""

ADMIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Admin — Painel CRM</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#07090f;color:#eaeef7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh;padding:32px 24px 60px}}
h1{{font-size:22px;font-weight:700;margin-bottom:4px;display:flex;align-items:center;gap:10px}}
h1::before{{content:"";display:inline-block;width:6px;height:24px;background:linear-gradient(180deg,#3b82f6,#1e3a8a);border-radius:3px}}
.sub{{color:#8590a8;font-size:12.5px;margin-bottom:28px;margin-top:2px}}
.topbar{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px;flex-wrap:wrap;gap:12px}}
.logout-btn{{background:transparent;border:1px solid #1d2740;color:#8590a8;font-size:12px;font-weight:600;padding:7px 14px;border-radius:7px;cursor:pointer;font-family:inherit;transition:all .15s;text-decoration:none;display:inline-flex;align-items:center;gap:6px}}
.logout-btn:hover{{color:#ef4444;border-color:#ef4444}}
.add-form{{background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:22px 24px;margin-bottom:28px}}
.add-form h2{{font-size:14px;font-weight:700;margin-bottom:16px;color:#eaeef7}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:600px){{.form-grid{{grid-template-columns:1fr}}}}
.form-group{{display:flex;flex-direction:column;gap:5px}}
label{{font-size:11px;color:#8590a8;text-transform:uppercase;letter-spacing:.07em;font-weight:700}}
input{{background:#131a2b;border:1px solid #1d2740;color:#eaeef7;padding:9px 12px;border-radius:7px;font-size:13px;font-family:inherit;outline:none;transition:border-color .15s}}
input:focus{{border-color:#3b82f6}}
.add-btn{{background:#1e3a8a;border:none;color:#fff;font-size:13px;font-weight:700;padding:10px 20px;border-radius:8px;cursor:pointer;font-family:inherit;margin-top:8px;transition:background .15s}}
.add-btn:hover{{background:#2c4dbd}}
.clients-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.client-card{{background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:18px 20px}}
.client-card.bloqueado{{border-color:rgba(239,68,68,.3);background:rgba(239,68,68,.04)}}
.client-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}}
.client-name{{font-size:15px;font-weight:700}}
.client-user{{font-size:11.5px;color:#8590a8;margin-top:2px}}
.status-badge{{padding:3px 10px;border-radius:20px;font-size:10.5px;font-weight:700;text-transform:uppercase}}
.status-badge.ativo{{background:rgba(34,197,94,.13);color:#22c55e;border:1px solid rgba(34,197,94,.25)}}
.status-badge.bloqueado{{background:rgba(239,68,68,.13);color:#ef4444;border:1px solid rgba(239,68,68,.25)}}
.client-info{{font-size:11.5px;color:#8590a8;margin-bottom:14px;line-height:1.7}}
.client-info b{{color:#eaeef7}}
.client-actions{{display:flex;gap:8px;flex-wrap:wrap}}
.btn-sm{{font-size:11.5px;font-weight:700;padding:6px 12px;border-radius:6px;cursor:pointer;font-family:inherit;border:none;transition:all .15s}}
.btn-block{{background:rgba(239,68,68,.12);color:#ef4444;border:1px solid rgba(239,68,68,.3)}}
.btn-block:hover{{background:rgba(239,68,68,.25)}}
.btn-unblock{{background:rgba(34,197,94,.12);color:#22c55e;border:1px solid rgba(34,197,94,.3)}}
.btn-unblock:hover{{background:rgba(34,197,94,.25)}}
.btn-view{{background:rgba(59,130,246,.12);color:#3b82f6;border:1px solid rgba(59,130,246,.3)}}
.btn-view:hover{{background:rgba(59,130,246,.25)}}
.btn-del{{background:transparent;color:#5a657d;border:1px solid #1d2740}}
.btn-del:hover{{color:#ef4444;border-color:#ef4444}}
.link-copy{{font-size:11px;color:#3b82f6;cursor:pointer;text-decoration:underline;margin-left:4px}}
.msg{{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);color:#22c55e;padding:10px 14px;border-radius:8px;font-size:12.5px;margin-bottom:20px;display:{msg_display}}}
.empty{{text-align:center;padding:48px 20px;color:#5a657d;font-size:13px}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <h1>Admin — Painel CRM</h1>
    <p class="sub">Gerencie todos os clientes ativos</p>
  </div>
  <a href="/admin/meu-painel" class="logout-btn" style="background:#1e3a8a;color:#fff;border-color:#2c4dbd">📊 Meu Painel</a>
  <a href="/logout" class="logout-btn">⎋ Sair</a>
</div>

<div class="msg">{msg}</div>

<div class="add-form">
  <h2>➕ Adicionar novo cliente</h2>
  <form method="POST" action="/admin/add">
    <div class="form-grid">
      <div class="form-group">
        <label>Usuário (login)</label>
        <input type="text" name="username" placeholder="ex: empresa_abc" required/>
      </div>
      <div class="form-group">
        <label>Senha</label>
        <input type="password" name="password" placeholder="senha do cliente" required/>
      </div>
      <div class="form-group">
        <label>Nome da empresa</label>
        <input type="text" name="nome" placeholder="ex: Valentim Imóveis" required/>
      </div>
      <div class="form-group">
        <label>Kommo Subdomain</label>
        <input type="text" name="subdomain" placeholder="ex: minhaempresa" required/>
      </div>
      <div class="form-group" style="grid-column:1/-1">
        <label>Kommo Token</label>
        <input type="text" name="token" placeholder="token de acesso do Kommo" required/>
      </div>
      <div class="form-group">
        <label>Cor primária (opcional)</label>
        <input type="text" name="cor" placeholder="#3b82f6"/>
      </div>
      <div class="form-group">
        <label>URL da logo (opcional)</label>
        <input type="text" name="logo" placeholder="https://..."/>
      </div>
    </div>
    <button class="add-btn" type="submit">Criar cliente</button>
  </form>
</div>

<div class="clients-grid">
{clientes_html}
</div>
</body>
</html>"""

# ─── HTTP SERVER ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send_html(self, code, html_bytes):
        self.send_response(code)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def set_cookie(self, name, value, max_age=43200):
        self.send_header("Set-Cookie", f"{name}={value}; Path=/; HttpOnly; Max-Age={max_age}; SameSite=Lax")

    def clear_cookie(self, name):
        self.send_header("Set-Cookie", f"{name}=; Path=/; HttpOnly; Max-Age=0")

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode("utf-8") if length else ""

    def parse_form(self):
        body = self.read_body()
        result = {}
        for pair in body.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                from urllib.parse import unquote_plus
                result[unquote_plus(k)] = unquote_plus(v)
        return result

    def render_login(self, error="", title=None, logo="", cor="#3b82f6"):
        t = title or CFG["nome_empresa"]
        logo_tag = f'<img src="{logo}" style="max-height:48px;border-radius:8px;display:block;margin:0 auto 12px"/>' if logo else ""
        html = LOGIN_HTML.format(
            title=t, cor=cor, logo_tag=logo_tag,
            err_display="block" if error else "none",
            err_msg=error or ""
        )
        return html.encode("utf-8")

    def render_admin(self, msg=""):
        users = load_users()
        if not users:
            clientes_html = '<div class="empty">Nenhum cliente cadastrado ainda. Adicione o primeiro acima.</div>'
        else:
            parts = []
            for uname, ud in users.items():
                ativo = ud.get("ativo", True)
                status_cls  = "ativo" if ativo else "bloqueado"
                status_txt  = "Ativo" if ativo else "Bloqueado"
                card_cls    = "" if ativo else "bloqueado"
                btn_toggle  = f'<form method="POST" action="/admin/block" style="display:inline"><input type="hidden" name="username" value="{uname}"><button class="btn-sm btn-block" type="submit">🔒 Bloquear</button></form>' if ativo else f'<form method="POST" action="/admin/unblock" style="display:inline"><input type="hidden" name="username" value="{uname}"><button class="btn-sm btn-unblock" type="submit">🔓 Desbloquear</button></form>'
                parts.append(f"""
                <div class="client-card {card_cls}">
                  <div class="client-top">
                    <div><div class="client-name">{ud.get('nome','—')}</div><div class="client-user">@{uname}</div></div>
                    <span class="status-badge {status_cls}">{status_txt}</span>
                  </div>
                  <div class="client-info">
                    <b>Kommo:</b> {ud.get('kommo_subdomain','—')}.kommo.com<br>
                    <b>Acesso:</b> {self.headers.get('Host','')}/login
                  </div>
                  <div class="client-actions">
                    <a href="/admin/view/{uname}" class="btn-sm btn-view">👁 Ver painel</a>
                    {btn_toggle}
                    <form method="POST" action="/admin/delete" style="display:inline" onsubmit="return confirm('Deletar {uname}?')">
                      <input type="hidden" name="username" value="{uname}">
                      <button class="btn-sm btn-del" type="submit">🗑</button>
                    </form>
                  </div>
                </div>""")
            clientes_html = "\n".join(parts)
        html = ADMIN_HTML.format(
            clientes_html=clientes_html,
            msg=msg,
            msg_display="block" if msg else "none"
        )
        return html.encode("utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        token  = get_cookie(self, "crm_session")
        sess   = get_session(token)

        # Login page
        if path in ("/", "/login"):
            if sess:
                if sess["is_admin"]: return self.redirect("/admin")
                return self.redirect("/painel")
            self.send_html(200, self.render_login(
                logo=CFG["logo_url"], cor=CFG["cor_primaria"]
            ))
            return

        # Logout
        if path == "/logout":
            if token: delete_session(token)
            self.send_response(302)
            self.clear_cookie("crm_session")
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # Admin panel
        if path == "/admin":
            if not sess or not sess["is_admin"]:
                return self.redirect("/login")
            self.send_html(200, self.render_admin())
            return

        # Admin: ver painel de um cliente específico (com dados embutidos)
        if path.startswith("/admin/view/"):
            if not sess or not sess["is_admin"]:
                return self.redirect("/login")
            uname = path.replace("/admin/view/", "").strip("/")
            users = load_users()
            if uname not in users:
                return self.send_json(404, {"erro": "cliente nao encontrado"})
            ud = users[uname]
            cache, lock = get_cache(uname)
            with lock:
                c_dados = cache["dados"]
                c_status = cache["status"]
            if c_dados is None and c_status != "atualizando":
                threading.Thread(
                    target=buscar_kommo_user,
                    args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                    daemon=True
                ).start()
                for _ in range(16):
                    time.sleep(0.5)
                    with lock:
                        c_dados = cache["dados"]
                    if c_dados: break
            body = get_painel_html(ud, cache_data=c_dados, is_admin_view=True)
            if body: self.send_html(200, body)
            else: self.send_json(500, {"erro": "HTML nao encontrado"})
            return

        # Admin: ver seu proprio painel
        if path == "/admin/meu-painel":
            if not sess or not sess["is_admin"]:
                return self.redirect("/login")
            ud = {
                "nome": CFG["nome_empresa"],
                "logo_url": CFG["logo_url"],
                "cor_primaria": CFG["cor_primaria"],
                "kommo_subdomain": CFG["subdomain"],
                "kommo_token": CFG["token"],
            }
            cache, lock = get_cache("__admin__")
            with lock:
                c_dados = cache["dados"]
                c_status = cache["status"]
            if c_dados is None and c_status != "atualizando":
                threading.Thread(
                    target=buscar_kommo_user,
                    args=("__admin__", CFG["subdomain"], CFG["token"]),
                    daemon=True
                ).start()
                for _ in range(16):
                    time.sleep(0.5)
                    with lock:
                        c_dados = cache["dados"]
                    if c_dados: break
            body = get_painel_html(ud, cache_data=c_dados, is_admin_view=True)
            if body: self.send_html(200, body)
            else: self.send_json(500, {"erro": "HTML nao encontrado"})
            return


        # Admin: dados de um cliente específico
        if path.startswith("/admin/api/"):
            if not sess or not sess["is_admin"]:
                return self.send_json(401, {"erro": "nao autorizado"})
            uname = path.replace("/admin/api/", "").strip("/").split("/")[0]
            cache, lock = get_cache(uname)
            with lock:
                resp = {"status": cache["status"], "atualizado_em": cache["atualizado_em"],
                        "erro": cache["erro"], "dados": cache["dados"]}
            return self.send_json(200, resp)

        # Painel do cliente
        if path == "/painel":
            if not sess: return self.redirect("/login")
            if sess["is_admin"]: return self.redirect("/admin")
            users = load_users()
            uname = sess["user"]
            ud = users.get(uname, {})
            if not ud.get("ativo", True):
                self.send_html(403, b"""<!DOCTYPE html><html><body style="background:#07090f;color:#ef4444;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;flex-direction:column;gap:12px">
                <h2>Acesso suspenso</h2><p style="color:#8590a8">Entre em contato com o administrador para reativar seu acesso.</p>
                <a href="/logout" style="color:#3b82f6;font-size:13px">Sair</a></body></html>""")
                return
            # Embutir dados no HTML para evitar loading infinito
            cache, lock = get_cache(uname)
            with lock:
                c_dados = cache["dados"]
                c_status = cache["status"]
            if c_dados is None and c_status != "atualizando":
                threading.Thread(
                    target=buscar_kommo_user,
                    args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                    daemon=True
                ).start()
                for _ in range(20):
                    time.sleep(0.5)
                    with lock:
                        c_dados = cache["dados"]
                    if c_dados: break
            body = get_painel_html(ud, cache_data=c_dados)
            if body: self.send_html(200, body)
            else: self.send_json(500, {"erro": "HTML nao encontrado"})
            return

        # API dados do cliente logado
        if path == "/api/dados":
            if not sess: return self.send_json(401, {"erro": "nao autorizado"})
            uname = sess["user"]
            if sess["is_admin"]:
                return self.send_json(200, {"status": "ok", "dados": None})
            cache, lock = get_cache(uname)
            with lock:
                resp = {"status": cache["status"], "atualizado_em": cache["atualizado_em"],
                        "erro": cache["erro"], "dados": cache["dados"]}
            return self.send_json(200, resp)

        # API refresh
        if path == "/api/refresh":
            if not sess: return self.send_json(401, {"erro": "nao autorizado"})
            uname = sess["user"]
            users = load_users()
            ud = users.get(uname, {})
            threading.Thread(
                target=buscar_kommo_user,
                args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                daemon=True
            ).start()
            return self.send_json(200, {"ok": True})

        self.send_json(404, {"erro": "nao encontrado"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        token  = get_cookie(self, "crm_session")
        sess   = get_session(token)

        # Login
        if path == "/login":
            form = self.parse_form()
            uname = form.get("username", "").strip()
            passwd = form.get("password", "").strip()

            # Admin master
            if uname == CFG["admin_user"] and passwd == CFG["admin_pass"]:
                new_token = create_session(uname, is_admin=True)
                self.send_response(302)
                self.set_cookie("crm_session", new_token)
                self.send_header("Location", "/admin")
                self.end_headers()
                return

            # Cliente
            users = load_users()
            ud = users.get(uname)
            if ud and verify_pass(passwd, ud.get("password_hash", "")):
                if not ud.get("ativo", True):
                    self.send_html(200, self.render_login(
                        error="Seu acesso está suspenso. Entre em contato com o administrador.",
                        cor=CFG["cor_primaria"]
                    ))
                    return
                new_token = create_session(uname, is_admin=False)
                self.send_response(302)
                self.set_cookie("crm_session", new_token)
                self.send_header("Location", "/painel")
                self.end_headers()
                # Carrega dados em background se ainda não tem
                cache, _ = get_cache(uname)
                if cache["dados"] is None:
                    threading.Thread(
                        target=buscar_kommo_user,
                        args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                        daemon=True
                    ).start()
                return

            self.send_html(200, self.render_login(
                error="Usuário ou senha incorretos.",
                cor=CFG["cor_primaria"]
            ))
            return

        # Admin: adicionar cliente
        if path == "/admin/add":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","").strip().lower().replace(" ","_")
            if not uname:
                self.send_html(200, self.render_admin("❌ Usuário inválido."))
                return
            users = load_users()
            users[uname] = {
                "password_hash":    hash_pass(form.get("password","123456")),
                "ativo":            True,
                "nome":             form.get("nome",""),
                "kommo_subdomain":  form.get("subdomain",""),
                "kommo_token":      form.get("token",""),
                "cor_primaria":     form.get("cor","") or CFG["cor_primaria"],
                "logo_url":         form.get("logo",""),
            }
            save_users(users)
            # Inicia busca em background
            ud = users[uname]
            threading.Thread(
                target=buscar_kommo_user,
                args=(uname, ud["kommo_subdomain"], ud["kommo_token"]),
                daemon=True
            ).start()
            self.send_html(200, self.render_admin(f"✅ Cliente '{uname}' criado com sucesso!"))
            return

        # Admin: bloquear
        if path == "/admin/block":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","")
            users = load_users()
            if uname in users:
                users[uname]["ativo"] = False
                save_users(users)
            self.send_html(200, self.render_admin(f"🔒 Cliente '{uname}' bloqueado."))
            return

        # Admin: desbloquear
        if path == "/admin/unblock":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","")
            users = load_users()
            if uname in users:
                users[uname]["ativo"] = True
                save_users(users)
            self.send_html(200, self.render_admin(f"🔓 Cliente '{uname}' desbloqueado."))
            return

        # Admin: deletar
        if path == "/admin/delete":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","")
            users = load_users()
            if uname in users:
                del users[uname]
                save_users(users)
            self.send_html(200, self.render_admin(f"🗑 Cliente '{uname}' removido."))
            return

        self.send_json(404, {"erro": "nao encontrado"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 52)
    print(f"  PAINEL CRM v4 — Multi-cliente + Login")
    print("=" * 52)
    print(f"  Admin : {CFG['admin_user']} / {CFG['admin_pass']}")
    print(f"  URL   : http://localhost:{CFG['porta']}")
    print(f"  Refresh: a cada {CFG['refresh_minutos']} min")
    print("=" * 52)

    # Carrega caches dos clientes existentes
    load_users()  # inicializa _users_db
    users = get_users()
    for uname, ud in users.items():
        if ud.get("ativo", True) and ud.get("kommo_token"):
            threading.Thread(
                target=buscar_kommo_user,
                args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                daemon=True
            ).start()

    threading.Thread(target=auto_refresh, daemon=True).start()

    server = HTTPServer(("0.0.0.0", CFG["porta"]), Handler)
    print(f"\n  Servidor rodando em http://localhost:{CFG['porta']}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor encerrado.")
        server.server_close()
