#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, re, ssl, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KOMMO_SUBDOMAIN  = "souvalentimm"
KOMMO_TOKEN      = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6ImY5YWJlNjcyMjY4YjI3NjgyNGIyYjRhOTA4ZjA2YjRkYzRlMGIxZWI0NDQyN2FjMTIwMGNhNGI3ZjA0YmJmNGUyMjUzYWEyZjQ4ZWFlZjBjIn0.eyJhdWQiOiIyZWRkMTg4YS1jZDQ0LTQ3NTMtOWE0NS1hODBjNjI4MWFhYmMiLCJqdGkiOiJmOWFiZTY3MjI2OGIyNzY4MjRiMmI0YTkwOGYwNmI0ZGM0ZTBiMWViNDQ0MjdhYzEyMDBjYTRiN2YwNGJiZjRlMjI1M2FhMmY0OGVhZWYwYyIsImlhdCI6MTc3NzkwMDkwNywibmJmIjoxNzc3OTAwOTA3LCJleHAiOjE3OTg3NjE2MDAsInN1YiI6IjEwNjc5Mzc5IiwiZ3JhbnRfdHlwZSI6IiIsImFjY291bnRfaWQiOjMyMjY4ODQzLCJiYXNlX2RvbWFpbiI6ImtvbW1vLmNvbSIsInZlcnNpb24iOjIsInNjb3BlcyI6WyJjcm0iLCJmaWxlcyIsImZpbGVzX2RlbGV0ZSIsIm5vdGlmaWNhdGlvbnMiLCJwdXNoX25vdGlmaWNhdGlvbnMiXSwiaGFzaF91dWlkIjoiMjg1ZDdkNmEtZmEzZi00NmRjLTg2ZmYtMDQxNWUzNzMwMzgxIiwiYXBpX2RvbWFpbiI6ImFwaS1jLmtvbW1vLmNvbSJ9.ozGuMughXguqgy3rbwczvcc0VAbO3Bge0JwKydtgE4A9KFK3_rPCfb0q1c_Va9dF1jWjYlSvHprQ9LkGtADGyHV3tCoXXYMysiFvzM3yRyeGDDPoQ-xaDL1L2gsbh04U4jWvPeXXgT7U1QVSBuqOxHgFFKhi6Cwex9ilEGmMHpbKEdbwyYHS0lWUXitU6MAFZIKObhk8RYXBYKSq4BIFEp_Ari1b-vbPJsQobOG9XywsrEshomJARAmalz4x-4AkJe0s9FKawl4yIvwodOH0IoCkvdV35C2kgZJqAqp0R6OzqrTBwossi83pAPp73Y58SYK1YPhHZtS7WrNyYAZsyg"
PORTA            = int(os.environ.get("PORT", 8080))
REFRESH_MINUTOS  = 30
CACHE_FILE       = Path(__file__).parent / "crm_cache.json"

BASE_URL = f"https://{KOMMO_SUBDOMAIN}.kommo.com/api/v4"
HEADERS  = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
CTX      = ssl.create_default_context()

_cache = {"dados": None, "atualizado_em": None, "status": "aguardando", "erro": None}
_lock  = threading.Lock()

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
    ids = list(contact_ids)
    BATCH = 50
    batches = [ids[i:i+BATCH] for i in range(0, len(ids), BATCH)]
    phone_map = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_batch, b): b for b in batches}
        for f in as_completed(futures):
            phone_map.update(f.result())
    return phone_map

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

def buscar_kommo():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Buscando dados do Kommo...")
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

        users = fetch_all("/users", "users")
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
                "id":       str(l["id"]),
                "data":     l.get("created_at") or 0,
                "modificado": l.get("updated_at") or 0,
                "vendedor": user_map.get(l.get("responsible_user_id"), "Nao atribuido"),
                "cliente":  cliente,
                "telefone": phone,
                "valor":    l.get("price") or 0,
                "canal":    canal,
                "funil":    funil,
                "etapa":    l.get("status_id"),
                "tags":     tags_str,
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
        time.sleep(REFRESH_MINUTOS * 60)
        buscar_kommo()

PAINEL_HTML = Path(__file__).parent / "painel_servidor_v2.html"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type",  "application/json; charset=utf-8")
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
            if PAINEL_HTML.exists():
                body = PAINEL_HTML.read_bytes()
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

if __name__ == "__main__":
    print("=" * 50)
    print("  PAINEL CRM KOMMO - SERVIDOR LOCAL v2")
    print("=" * 50)
    print(f"  Conta : {KOMMO_SUBDOMAIN}.kommo.com")
    print(f"  Painel: http://localhost:{PORTA}")
    print(f"  Refresh: a cada {REFRESH_MINUTOS} minutos")
    print("=" * 50)

    disco = load_cache_disk()
    if disco:
        with _lock:
            _cache["dados"]         = disco
            _cache["atualizado_em"] = "cache"
            _cache["status"]        = "ok"
        print(f"\n  [CACHE] {len(disco.get('leads',[]))} leads carregados - painel disponivel ja!")
        print("  Atualizando em background...\n")
    else:
        print("\n  Buscando dados pela primeira vez (~10s)...\n")

    t0 = threading.Thread(target=buscar_kommo, daemon=True)
    t0.start()

    t1 = threading.Thread(target=auto_refresh, daemon=True)
    t1.start()

    server = HTTPServer(("0.0.0.0", PORTA), Handler)
    print(f"  Servidor rodando em http://localhost:{PORTA}")
    print("  NAO feche esta janela enquanto usar o painel!\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor encerrado.")
        server.server_close()