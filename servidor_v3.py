#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel CRM Kommo — Servidor v3
White Label: edite config.json para personalizar por cliente.
Deploy: Railway, Render, Heroku ou python servidor_v3.py local.
"""
import json, os, re, ssl, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

def load_config():
    """Lê config.json — prioriza variáveis de ambiente (para Railway)."""
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[!] Erro ao ler config.json: {e}")

    kommo   = cfg.get("kommo", {})
    empresa = cfg.get("empresa", {})
    painel  = cfg.get("painel", {})

    return {
        # Kommo — variáveis de ambiente têm prioridade (Railway secrets)
        "subdomain":        os.environ.get("KOMMO_SUBDOMAIN")  or kommo.get("subdomain", ""),
        "token":            os.environ.get("KOMMO_TOKEN")       or kommo.get("token", ""),
        # Empresa
        "nome_empresa":     os.environ.get("EMPRESA_NOME")      or empresa.get("nome", "Painel CRM"),
        "logo_url":         os.environ.get("EMPRESA_LOGO")      or empresa.get("logo_url", ""),
        "cor_primaria":     os.environ.get("COR_PRIMARIA")      or empresa.get("cor_primaria", "#3b82f6"),
        "cor_secundaria":   os.environ.get("COR_SECUNDARIA")    or empresa.get("cor_secundaria", "#1e3a8a"),
        # Painel
        "porta":            int(os.environ.get("PORT", 8080)),
        "refresh_minutos":  int(os.environ.get("REFRESH_MINUTOS") or painel.get("refresh_minutos", 30)),
        "meta_mes_padrao":  int(os.environ.get("META_MES")        or painel.get("meta_mes_padrao", 50000)),
    }

CFG         = load_config()
PORTA       = CFG["porta"]
CACHE_FILE  = BASE_DIR / "crm_cache.json"
BASE_URL    = f"https://{CFG['subdomain']}.kommo.com/api/v4"
HEADERS     = {"Authorization": f"Bearer {CFG['token']}"}
CTX         = ssl.create_default_context()

_cache = {"dados": None, "atualizado_em": None, "status": "aguardando", "erro": None}
_lock  = threading.Lock()

# ─── KOMMO API ────────────────────────────────────────────────────────────────
def kommo_get(url, tentativas=3):
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
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

def fetch_all(endpoint, emb_key, extra=""):
    items, page = [], 1
    sep = "&" if "?" in endpoint else "?"
    while True:
        data = kommo_get(f"{BASE_URL}{endpoint}{sep}limit=250&page={page}{extra}")
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
            if vals:
                return re.sub(r"\D", "", str(vals[0].get("value", "")))
    return ""

def _fetch_batch(batch):
    query = "&".join(f"id[]={cid}" for cid in batch)
    url = f"{BASE_URL}/contacts?{query}&with=custom_fields_values&limit=250"
    result = {}
    try:
        data = kommo_get(url)
        if data:
            for c in (data.get("_embedded") or {}).get("contacts") or []:
                ph = get_phone(c)
                if ph:
                    result[c["id"]] = ph
    except Exception as ex:
        print(f"    [!] Erro lote contatos: {ex}")
    return result

def fetch_contact_phones(contact_ids):
    ids    = list(contact_ids)
    BATCH  = 50
    batches = [ids[i:i+BATCH] for i in range(0, len(ids), BATCH)]
    phone_map = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_batch, b): b for b in batches}
        for f in as_completed(futures):
            phone_map.update(f.result())
    return phone_map

def detect_canal_com_phone(lead, has_phone, tags_str):
    text = (tags_str + " " + " ".join(
        str((cf.get("values") or [{}])[0].get("value", ""))
        for cf in (lead.get("custom_fields_values") or [])
    )).lower()
    if "tiktok"    in text: return "TikTok"
    if "instagram" in text: return "Instagram"
    if "facebook"  in text or "fb.com" in text: return "Outros"
    if has_phone:            return "WhatsApp"
    return "Outros"

# ─── CACHE ────────────────────────────────────────────────────────────────────
def save_cache_disk(resultado):
    try:
        CACHE_FILE.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  [!] Erro ao salvar cache: {e}")

def load_cache_disk():
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

# ─── FETCH PRINCIPAL ──────────────────────────────────────────────────────────
def buscar_kommo():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Buscando dados do Kommo ({CFG['subdomain']})...")
    with _lock: _cache["status"] = "atualizando"
    try:
        pd = kommo_get(f"{BASE_URL}/leads/pipelines?limit=250")
        pipelines = (pd.get("_embedded") or {}).get("pipelines") or []
        status_map, funil_map = {}, {}
        for p in pipelines:
            funil_map[p["id"]] = p["name"]
            for s in (p.get("_embedded") or {}).get("statuses") or []:
                tipo = "andamento"
                if s.get("type") == 142 or detect_tipo(s["name"]) == "ganho":   tipo = "ganho"
                if s.get("type") == 143 or detect_tipo(s["name"]) == "perdido": tipo = "perdido"
                status_map[s["id"]] = {"label": s["name"], "funil": p["name"], "tipo": tipo}

        users    = fetch_all("/users", "users")
        user_map = {u["id"]: u.get("name") or f"User {u['id']}" for u in users}

        print(f"  Baixando leads...")
        leads_raw = fetch_all("/leads", "leads", "&with=contacts,tags")
        print(f"  {len(leads_raw)} leads recebidos")

        contact_ids = set()
        for l in leads_raw:
            for c in (l.get("_embedded") or {}).get("contacts") or []:
                if c.get("id"):
                    contact_ids.add(c["id"])

        print(f"  Buscando telefones de {len(contact_ids)} contatos...")
        contact_phone_map = fetch_contact_phones(contact_ids)
        print(f"  {len(contact_phone_map)} contatos com telefone")

        all_leads = []
        for l in leads_raw:
            contacts  = (l.get("_embedded") or {}).get("contacts") or []
            tags_list = (l.get("_embedded") or {}).get("tags")     or []
            tags_str  = ",".join(t.get("name", "") for t in tags_list)
            phone = ""
            for c in contacts:
                cid = c.get("id")
                if cid and cid in contact_phone_map:
                    phone = contact_phone_map[cid]
                    break
            has_phone = bool(phone)
            canal  = detect_canal_com_phone(l, has_phone, tags_str)
            funil  = funil_map.get(l.get("pipeline_id"), f"Funil {l.get('pipeline_id')}")
            nome   = l.get("name") or ""
            cliente = "Lead sem nome" if is_phone_only(nome) or not nome else nome
            all_leads.append({
                "id":         str(l["id"]),
                "data":       l.get("created_at") or 0,
                "modificado": l.get("updated_at")  or 0,
                "vendedor":   user_map.get(l.get("responsible_user_id"), "Nao atribuido"),
                "cliente":    cliente,
                "telefone":   phone,
                "valor":      l.get("price") or 0,
                "canal":      canal,
                "funil":      funil,
                "etapa":      l.get("status_id"),
                "tags":       tags_str,
            })

        funil_counts = {}
        for l in all_leads:
            funil_counts[l["funil"]] = funil_counts.get(l["funil"], 0) + 1

        resultado = {
            "leads":      all_leads,
            "status_map": status_map,
            "canais":     ["WhatsApp", "Instagram", "TikTok", "Outros"],
            "funis":      sorted(funil_counts.keys()),
            "total":      len(all_leads),
            # white label info (enviada ao painel)
            "wl": {
                "nome":          CFG["nome_empresa"],
                "logo_url":      CFG["logo_url"],
                "cor_primaria":  CFG["cor_primaria"],
                "cor_secundaria":CFG["cor_secundaria"],
                "meta_mes":      CFG["meta_mes_padrao"],
            },
        }

        with _lock:
            _cache["dados"]         = resultado
            _cache["atualizado_em"] = datetime.now().isoformat()
            _cache["status"]        = "ok"
            _cache["erro"]          = None

        save_cache_disk(resultado)
        wa = sum(1 for l in all_leads if l["canal"] == "WhatsApp")
        print(f"  [OK] {len(all_leads)} leads | WhatsApp: {wa} | {len(funil_counts)} funis")

    except Exception as e:
        with _lock:
            _cache["status"] = "erro"
            _cache["erro"]   = str(e)
        print(f"  [ERRO] {e}")

def auto_refresh():
    while True:
        time.sleep(CFG["refresh_minutos"] * 60)
        buscar_kommo()

# ─── HTML DINÂMICO (white label) ──────────────────────────────────────────────
PAINEL_HTML = BASE_DIR / "painel_servidor_v2.html"

def get_painel_html():
    """Lê o HTML e injeta as cores/nome do cliente."""
    if not PAINEL_HTML.exists():
        return None
    html = PAINEL_HTML.read_text(encoding="utf-8")
    # Injeta CSS vars white label logo após :root{
    wl_css = f"""
    /* WHITE LABEL - gerado automaticamente */
    --bluel: {CFG['cor_primaria']};
    --blue2: {CFG['cor_secundaria']};
    --blue:  {CFG['cor_secundaria']};
    /* FIM WHITE LABEL */
"""
    html = html.replace(":root{", f":root{{\n{wl_css}", 1)

    # Troca título da página
    html = html.replace(
        "<title>Painel CRM — Live</title>",
        f"<title>{CFG['nome_empresa']} — Painel CRM</title>"
    )
    # Troca h1
    html = html.replace(
        "<h1>Painel CRM — Live</h1>",
        f"<h1>{CFG['nome_empresa']}</h1>"
    )
    # Injeta logo se configurado
    if CFG["logo_url"]:
        logo_tag = f'<img src="{CFG["logo_url"]}" style="height:32px;border-radius:6px;margin-right:8px" alt="logo">'
        html = html.replace("<h1>", f"<h1>{logo_tag}", 1)

    return html.encode("utf-8")

# ─── HTTP SERVER ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/dados":
            with _lock:
                resp = {
                    "status":        _cache["status"],
                    "atualizado_em": _cache["atualizado_em"],
                    "erro":          _cache["erro"],
                    "dados":         _cache["dados"],
                }
            self.send_json(200, resp)

        elif path == "/api/refresh":
            t = threading.Thread(target=buscar_kommo, daemon=True)
            t.start()
            self.send_json(200, {"ok": True, "msg": "Atualizacao iniciada"})

        elif path in ("/", "/index.html"):
            body = get_painel_html()
            if body:
                self.send_response(200)
                self.send_header("Content-Type",   "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json(404, {"erro": "painel_servidor_v2.html nao encontrado"})
        else:
            self.send_json(404, {"erro": "rota nao encontrada"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET,OPTIONS")
        self.end_headers()

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 52)
    print(f"  {CFG['nome_empresa']} — PAINEL CRM v3")
    print("=" * 52)
    print(f"  Conta  : {CFG['subdomain']}.kommo.com")
    print(f"  Painel : http://localhost:{PORTA}")
    print(f"  Refresh: a cada {CFG['refresh_minutos']} minutos")
    print("=" * 52)

    disco = load_cache_disk()
    if disco:
        with _lock:
            _cache["dados"]         = disco
            _cache["atualizado_em"] = "cache"
            _cache["status"]        = "ok"
        print(f"\n  [CACHE] {len(disco.get('leads',[]))} leads — painel disponivel!")
        print("  Atualizando em background...\n")
    else:
        print("\n  Buscando dados pela primeira vez (~10s)...\n")

    threading.Thread(target=buscar_kommo,  daemon=True).start()
    threading.Thread(target=auto_refresh,  daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORTA), Handler)
    print(f"  Servidor rodando em http://localhost:{PORTA}")
    print("  NAO feche esta janela enquanto usar o painel!\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor encerrado.")
        server.server_close()
