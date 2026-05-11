#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel CRM Kommo — Servidor v4
- Login com senha por cliente
- Admin master: ve todos os paineis
- Pode bloquear/desbloquear clientes
- White label por cliente
"""
import json, os, re, ssl, sys, time, threading, urllib.request, urllib.error, hashlib, secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR    = Path(__file__).parent
DATA_DIR    = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = BASE_DIR / "config.json"
USERS_FILE  = DATA_DIR / "users.json"
CACHE_FILE  = DATA_DIR / "crm_cache.json"

# CONFIG
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
        "refresh_minutos": int(os.environ.get("REFRESH_MINUTOS") or painel.get("refresh_minutos", 3)),
        "meta_mes_padrao": int(os.environ.get("META_MES")        or painel.get("meta_mes_padrao", 50000)),
        "admin_user":      os.environ.get("ADMIN_USER", "admin"),
        "admin_pass":      os.environ.get("ADMIN_PASS", "admin123"),
    }

CFG = load_config()

# ASAAS
ASAAS_KEY      = os.environ.get("ASAAS_KEY", "")
ASAAS_VALOR    = float(os.environ.get("ASAAS_VALOR", "197"))
ASAAS_DIA_VENC = int(os.environ.get("ASAAS_DIA_VENC", "1"))
ASAAS_BASE     = "https://api.asaas.com/v3"

# ANTHROPIC (Raio-X Comercial)
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_KEY", "")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# EMAIL
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
SITE_URL   = os.environ.get("SITE_URL", "").rstrip("/")

def enviar_email_boas_vindas(nome, email, uname, senha, link_acesso, link_cartao=""):
    if not GMAIL_USER or not GMAIL_PASS or not email:
        print(f"[EMAIL] Config incompleta ou email vazio — pulando envio para {uname}")
        return
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    assunto = f"Seu acesso ao Painel CRM — {nome}"
    corpo_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#07090f;color:#eaeef7;border-radius:12px;padding:32px">
      <h2 style="color:#3b82f6;margin-bottom:8px">Painel CRM — Bem-vindo!</h2>
      <p style="color:#8590a8;margin-bottom:24px">Olá <b style="color:#eaeef7">{nome}</b>, seu acesso está pronto.</p>
      <div style="background:#0f1320;border:1px solid #1d2740;border-radius:10px;padding:20px;margin-bottom:20px">
        <p style="margin:0 0 8px"><b>🔗 Link de acesso:</b><br>
          <a href="{link_acesso}" style="color:#3b82f6">{link_acesso}</a></p>
        <p style="margin:8px 0"><b>👤 Usuário:</b> {uname}</p>
        <p style="margin:8px 0"><b>🔑 Senha:</b> {senha}</p>
      </div>
      {"<div style='background:#0f1320;border:1px solid #1d2740;border-radius:10px;padding:20px;margin-bottom:20px'><p style='margin:0'><b>💳 Link para pagamento:</b><br><a href='" + link_cartao + "' style='color:#3b82f6'>" + link_cartao + "</a></p></div>" if link_cartao else ""}
      <p style="color:#5a657d;font-size:12px;margin-top:24px">Em caso de dúvidas, entre em contato.</p>
    </div>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = GMAIL_USER
    msg["To"]      = email
    msg.attach(MIMEText(corpo_html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_USER, GMAIL_PASS)
            srv.sendmail(GMAIL_USER, email, msg.as_string())
        print(f"[EMAIL] Enviado para {email}")
    except Exception as e:
        print(f"[EMAIL] Erro ao enviar para {email}: {e}")

# SSL CONTEXT (usado por Kommo e Asaas)
CTX = ssl.create_default_context()

def asaas_req(method, endpoint, data=None):
    if not ASAAS_KEY:
        return None
    url = ASAAS_BASE + endpoint
    body = json.dumps(data).encode() if data else None
    headers = {"access_token": ASAAS_KEY, "Content-Type": "application/json", "User-Agent": "PainelCRM/1.0"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[ASAAS] HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}")
        return None
    except Exception as e:
        print(f"[ASAAS] Erro: {e}")
        return None

def asaas_proximo_vencimento(dia):
    hoje = datetime.now()
    try:
        if hoje.day < dia:
            return hoje.replace(day=dia).strftime("%Y-%m-%d")
        if hoje.month == 12:
            return hoje.replace(year=hoje.year+1, month=1, day=dia).strftime("%Y-%m-%d")
        return hoje.replace(month=hoje.month+1, day=dia).strftime("%Y-%m-%d")
    except:
        return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

def asaas_criar_cliente_assinatura(uname, nome, email=""):
    cust_data = {"name": nome or uname, "externalReference": uname}
    if email:
        cust_data["email"] = email
    cust = asaas_req("POST", "/customers", cust_data)
    if not cust or not cust.get("id"):
        print(f"[ASAAS] Falha criar cliente: {cust}"); return None, None, None
    customer_id = cust["id"]
    sub = asaas_req("POST", "/subscriptions", {
        "customer": customer_id,
        "billingType": "CREDIT_CARD",
        "value": ASAAS_VALOR,
        "nextDueDate": asaas_proximo_vencimento(ASAAS_DIA_VENC),
        "cycle": "MONTHLY",
        "description": "Painel CRM - " + (nome or uname),
        "externalReference": uname,
        "sendPaymentByPostalService": False
    })
    if not sub or not sub.get("id"):
        print(f"[ASAAS] Falha criar assinatura: {sub}"); return customer_id, None, None
    sub_id = sub["id"]
    # Busca o link de pagamento nos pagamentos da assinatura
    payment_link = sub.get("paymentLink") or ""
    if not payment_link:
        time.sleep(1)  # aguarda Asaas gerar o pagamento
        pagamentos = asaas_req("GET", f"/subscriptions/{sub_id}/payments")
        if pagamentos:
            lista = pagamentos.get("data") or []
            if lista:
                p = lista[0]
                payment_link = p.get("invoiceUrl") or p.get("bankSlipUrl") or ""
    if not payment_link:
        payment_link = f"https://www.asaas.com/c/{sub_id}"
    print(f"[ASAAS] Cliente: {customer_id} | Assinatura: {sub_id} | Link: {payment_link}")
    return customer_id, sub_id, payment_link

def asaas_cancelar_assinatura(sub_id):
    if sub_id:
        asaas_req("DELETE", "/subscriptions/" + sub_id)
        print("[ASAAS] Assinatura cancelada: " + sub_id)

def asaas_usuario_por_evento(data):
    event   = data.get("event", "")
    payment = data.get("payment") or {}
    ext_ref = payment.get("externalReference", "") or data.get("externalReference", "")
    cust_id = payment.get("customer", "")
    users   = load_users()
    if ext_ref and ext_ref in users:
        return ext_ref, event
    for uname, ud in users.items():
        if ud.get("asaas_customer_id") == cust_id:
            return uname, event
    return None, event

# USERS
_users_db   = {}
_users_lock = threading.Lock()

def load_users():
    global _users_db
    with _users_lock:
        try:
            if USERS_FILE.exists():
                _users_db = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[ERRO] Falha ao carregar users.json: {e}")
        return dict(_users_db)

def save_users(users):
    global _users_db
    with _users_lock:
        _users_db = dict(users)
        try:
            tmp = USERS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(USERS_FILE)   # atomic replace — evita corrupção
        except Exception as e:
            print(f"[ERRO] Falha ao salvar users.json: {e}")

def get_users():
    with _users_lock:
        return dict(_users_db)

def sanitize_username(u):
    return re.sub(r"[^a-z0-9_\-]", "", (u or "").lower().strip())[:40]

def hash_pass(p):
    return hashlib.sha256(p.encode()).hexdigest()

def verify_pass(plain, hashed):
    return hash_pass(plain) == hashed

# SESSOES
_sessions = {}
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

def cleanup_sessions():
    while True:
        time.sleep(300)
        now = datetime.now()
        with _sess_lock:
            expired = [t for t, s in list(_sessions.items()) if s["expires"] <= now]
            for t in expired:
                del _sessions[t]
        if expired:
            print(f"[SESSION] Removidas {len(expired)} sessoes expiradas")

def get_cookie(handler, name):
    cookie_hdr = handler.headers.get("Cookie", "")
    for part in cookie_hdr.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == name:
            return v.strip()
    return None

# CACHE POR USUARIO
_caches           = {}
_cache_locks      = {}
_caches_init_lock = threading.Lock()

def get_cache(username):
    with _caches_init_lock:
        if username not in _caches:
            _caches[username]      = {"dados": None, "atualizado_em": None, "status": "aguardando", "erro": None}
            _cache_locks[username] = threading.Lock()
        return _caches[username], _cache_locks[username]

def remove_cache(username):
    with _caches_init_lock:
        _caches.pop(username, None)
        _cache_locks.pop(username, None)

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

# Palavras-chave por canal — ordem importa (mais específico primeiro)
_CANAL_PATTERNS = [
    ("TikTok",    [r"tiktok", r"tik.?tok", r"tik_?tok"]),
    ("Instagram", [r"instagram", r"insta\b", r"ig\b", r"@instagram"]),
    ("Facebook",  [r"facebook", r"fb\b", r"messenger", r"meta\b"]),
    ("YouTube",   [r"youtube", r"yt\b", r"youtu\.be"]),
    ("Google",    [r"google", r"gads", r"g.?ads", r"adwords", r"cpc\b", r"ppc\b"]),
    ("WhatsApp",  [r"whatsapp", r"whats.?app", r"wpp\b", r"zap\b", r"wts\b",
                   r"wa\b", r"wsapp", r"whatsap"]),
    ("Email",     [r"e.?mail", r"newsletter", r"smtp"]),
    ("Organic",   [r"organic", r"seo\b", r"busca", r"search"]),
    ("Indicacao", [r"indica[cç]", r"referral", r"indica[cç]ao"]),
    ("Site",      [r"site\b", r"website", r"landing", r"form"]),
]

def detect_canal(tags_str, contact_fields, lead_name="", lead_fields=None, extra_signals=None):
    """Detecta o canal de origem do lead de forma precisa.
    Verifica: tags, campo MESSENGER do Kommo, campos UTM/source, nome do lead, funil.
    """
    signals = []
    if tags_str:
        signals.append(tags_str.lower())
    for s in (extra_signals or []):
        if s: signals.append(s.lower())

    # Verifica campos de contatos E do lead
    for cf in list(contact_fields or []) + list(lead_fields or []):
        fcode = (cf.get("field_code") or "").upper()
        fname = (cf.get("field_name") or "").lower()
        # Campo MESSENGER do Kommo: identifica de onde veio o chat
        is_source_field = (
            fcode in ("MESSENGER", "SOURCE", "CANAL", "ORIGEM", "UTM_SOURCE",
                      "MIDIA", "MEDIA", "NETWORK", "AD_SOURCE") or
            any(k in fname for k in ("source","utm","origem","canal","midia",
                                      "mídia","social","rede","messenger",
                                      "chat","integr","whence","medium"))
        )
        if is_source_field:
            for v in (cf.get("values") or []):
                val = str(v.get("value","") or v.get("enum_value","") or
                          v.get("enum_id","") or "").lower()
                if val:
                    signals.append(val)

    if lead_name:
        signals.append(lead_name.lower())

    combined = " ".join(signals)
    for canal, patterns in _CANAL_PATTERNS:
        for pat in patterns:
            if re.search(pat, combined):
                return canal
    return None  # sem sinal claro

_fetching      = set()
_fetching_lock = threading.Lock()

def buscar_kommo_user(username, subdomain, token):
    with _fetching_lock:
        if username in _fetching:
            return
        _fetching.add(username)
    try:
        _buscar_kommo_user_inner(username, subdomain, token)
    finally:
        with _fetching_lock:
            _fetching.discard(username)

def _buscar_kommo_user_inner(username, subdomain, token):
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
        leads_raw = fetch_all(base_url, token, "/leads", "leads", "&with=contacts,tags,custom_fields_values")

        contact_ids = set()
        for l in leads_raw:
            for c in (l.get("_embedded") or {}).get("contacts") or []:
                if c.get("id"): contact_ids.add(c["id"])

        ids = list(contact_ids)
        phone_map    = {}
        _contact_cache = {}   # cid -> custom_fields_values (para detect_canal)
        BATCH = 50
        for i in range(0, len(ids), BATCH):
            batch = ids[i:i+BATCH]
            query = "&".join(f"id[]={cid}" for cid in batch)
            url = f"{base_url}/contacts?{query}&with=custom_fields_values&limit=250"
            try:
                data = kommo_get(url, token)
                if data:
                    for c in (data.get("_embedded") or {}).get("contacts") or []:
                        cid = c["id"]
                        ph = get_phone(c)
                        if ph: phone_map[cid] = ph
                        _contact_cache[cid] = c.get("custom_fields_values") or []
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
            # Coleta os campos customizados do contato principal (para detect_canal)
            ct_fields = []
            for c in contacts:
                cid = c.get("id")
                if cid and cid in _contact_cache:
                    ct_fields = _contact_cache[cid]
                    break
            lead_fields = l.get("custom_fields_values") or []
            funil   = funil_map.get(l.get("pipeline_id"), f"Funil {l.get('pipeline_id')}")
            # Inclui nome do funil e etapa como sinal extra — muitos Kommos têm
            # funis chamados "Instagram", "TikTok Ads", "WhatsApp", etc.
            canal = detect_canal(tags_str, ct_fields, l.get("name",""), lead_fields,
                                 extra_signals=[funil])
            if canal is None:
                canal = "WhatsApp" if has_phone else "Outros"
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
            "canais": ["WhatsApp", "Instagram", "TikTok", "Facebook", "YouTube", "Google", "Email", "Organic", "Indicacao", "Site", "Outros"],
            "funis": sorted(funil_counts.keys()), "total": len(all_leads),
        }
        with lock:
            cache["dados"] = resultado
            cache["atualizado_em"] = datetime.now().isoformat()
            cache["status"] = "ok"
            cache["erro"]   = None
        # Log de diagnóstico de canais — visível nos logs do Render
        canal_counts = {}
        outros_sample = []
        for lead in all_leads:
            c = lead.get("canal","Outros")
            canal_counts[c] = canal_counts.get(c, 0) + 1
            if c == "Outros" and len(outros_sample) < 5:
                outros_sample.append(f"tags=[{lead.get('tags','')}] funil=[{lead.get('funil','')}]")
        print(f"  [OK] {username}: {len(all_leads)} leads | canais: {canal_counts}")
        if outros_sample:
            print(f"  [OUTROS amostras] {outros_sample}")
    except Exception as e:
        with lock:
            cache["status"] = "erro"
            cache["erro"]   = str(e)
        print(f"  [ERRO] {username}: {e}")


# ── RAIO-X COMERCIAL ──────────────────────────────────────────────────────────

def compute_raiox_stats(dados, mes, meta=50000):
    """Computa estatísticas do mês para o Raio-X Comercial."""
    leads     = dados.get("leads") or []
    status_map = dados.get("status_map") or {}

    # Filtrar por mês
    if mes:
        try:
            y, m = int(mes.split("-")[0]), int(mes.split("-")[1])
            ts_ini = datetime(y, m, 1).timestamp()
            ts_fim = datetime(y + (m // 12), (m % 12) + 1, 1).timestamp()
            mes_leads = [l for l in leads if ts_ini <= (l.get("data") or 0) < ts_fim]
        except Exception:
            mes_leads = leads
    else:
        mes_leads = leads

    agora = datetime.now().timestamp()

    def tipo_lead(l):
        return (status_map.get(l.get("etapa")) or {}).get("tipo", "andamento")

    ganhos    = [l for l in mes_leads if tipo_lead(l) == "ganho"]
    perdidos  = [l for l in mes_leads if tipo_lead(l) == "perdido"]
    andamento = [l for l in mes_leads if tipo_lead(l) == "andamento"]

    valor_ganho = sum(l.get("valor") or 0 for l in ganhos)
    total       = len(mes_leads)
    taxa_conv   = round(len(ganhos) / total * 100, 1) if total > 0 else 0

    # Leads parados há mais de 7 dias sem atualização
    parados = [l for l in andamento
               if agora - (l.get("modificado") or l.get("data") or agora) > 7 * 86400]

    # Leads sem nenhuma tag de follow-up
    sem_fup = [l for l in andamento if not (l.get("tags") or "")]

    # Stats por vendedor
    vendor_stats = {}
    for l in mes_leads:
        v = l.get("vendedor") or "Não atribuído"
        if v not in vendor_stats:
            vendor_stats[v] = {"ganho": 0, "perdido": 0, "andamento": 0,
                                "valor": 0, "parados": 0, "sem_fup": 0}
        t = tipo_lead(l)
        vendor_stats[v][t] = vendor_stats[v].get(t, 0) + 1
        vendor_stats[v]["valor"] += l.get("valor") or 0
        if l in parados:  vendor_stats[v]["parados"] += 1
        if l in sem_fup:  vendor_stats[v]["sem_fup"] += 1

    # Conversão por vendedor
    for v, s in vendor_stats.items():
        tot_v = s["ganho"] + s["perdido"] + s["andamento"]
        s["taxa_conv"] = round(s["ganho"] / tot_v * 100, 1) if tot_v > 0 else 0

    # Distribuição por canal
    canal_counts = {}
    for l in mes_leads:
        c = l.get("canal") or "Outros"
        canal_counts[c] = canal_counts.get(c, 0) + 1

    # Funil: onde perdem mais
    funil_stats = {}
    for l in mes_leads:
        f = l.get("funil") or "?"
        if f not in funil_stats:
            funil_stats[f] = {"ganho": 0, "perdido": 0, "andamento": 0}
        funil_stats[f][tipo_lead(l)] = funil_stats[f].get(tipo_lead(l), 0) + 1

    # Etapas com maior perda
    etapa_perda = {}
    for l in perdidos:
        lbl = (status_map.get(l.get("etapa")) or {}).get("label", "Desconhecida")
        etapa_perda[lbl] = etapa_perda.get(lbl, 0) + 1
    top_perdas = sorted(etapa_perda.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "mes":              mes or datetime.now().strftime("%Y-%m"),
        "meta":             meta,
        "total_leads":      total,
        "ganhos":           len(ganhos),
        "perdidos":         len(perdidos),
        "em_andamento":     len(andamento),
        "valor_ganho":      valor_ganho,
        "taxa_conversao":   taxa_conv,
        "pct_meta":         round(valor_ganho / meta * 100, 1) if meta > 0 else 0,
        "leads_parados":    len(parados),
        "leads_sem_fup":    len(sem_fup),
        "vendor_stats":     vendor_stats,
        "canal_counts":     canal_counts,
        "funil_stats":      funil_stats,
        "top_etapas_perda": top_perdas,
        "total_base":       len(leads),
    }


def claude_raiox(stats):
    """Chama Claude Haiku para gerar o Raio-X Comercial em JSON."""
    if not ANTHROPIC_KEY:
        return None

    mes_label = stats.get("mes", "?")
    vendor_txt = ""
    for v, s in list(stats.get("vendor_stats", {}).items())[:8]:
        vendor_txt += (f"  {v}: ganhou={s['ganho']}, perdeu={s['perdido']}, "
                       f"andamento={s['andamento']}, valor=R${s['valor']:,.0f}, "
                       f"conv={s['taxa_conv']}%, parados={s['parados']}\n")

    prompt = f"""Você é um consultor comercial sênior. Analise os dados do CRM abaixo e gere um relatório Raio-X Comercial executivo em JSON.

DADOS DO MÊS {mes_label}:
- Meta: R$ {stats.get('meta',0):,.0f}
- Resultado: R$ {stats.get('valor_ganho',0):,.0f} ({stats.get('pct_meta',0)}% da meta)
- Total de leads: {stats.get('total_leads',0)} | Ganhos: {stats.get('ganhos',0)} | Perdidos: {stats.get('perdidos',0)} | Em andamento: {stats.get('em_andamento',0)}
- Taxa de conversão: {stats.get('taxa_conversao',0)}%
- Leads parados >7 dias: {stats.get('leads_parados',0)}
- Leads sem follow-up: {stats.get('leads_sem_fup',0)}
- Principais etapas de perda: {stats.get('top_etapas_perda',[])}
- Canais: {json.dumps(stats.get('canal_counts',{}), ensure_ascii=False)}
- Funis: {json.dumps(stats.get('funil_stats',{}), ensure_ascii=False)}
VENDEDORES:
{vendor_txt}

Retorne APENAS um JSON válido (sem markdown, sem código) com esta estrutura:
{{
  "resultado_geral": {{
    "status": "acima_da_meta",
    "texto": "análise direta do resultado em 2-3 frases",
    "destaques": ["ponto 1", "ponto 2", "ponto 3"]
  }},
  "operacao": {{
    "texto": "análise da operação em 2-3 frases",
    "alertas": ["alerta crítico 1", "alerta 2"]
  }},
  "equipe": [
    {{
      "nome": "nome do vendedor",
      "status": "destaque",
      "pontos": ["ponto 1", "ponto 2"],
      "conclusao": "frase curta de avaliação"
    }}
  ],
  "oportunidades": ["oportunidade 1", "oportunidade 2", "oportunidade 3"],
  "recomendacoes": ["ação prática 1", "ação 2", "ação 3", "ação 4"],
  "conclusao": "parágrafo executivo de 3-4 frases resumindo o mês, principais problemas e próximos passos"
}}"""

    body = json.dumps({
        "model":      ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "messages":   [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key":         ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            resp = json.loads(r.read())
            text = resp["content"][0]["text"].strip()
            # Remove blocos de código se o modelo os inserir
            if "```" in text:
                text = re.sub(r"```[a-z]*\n?", "", text).strip()
            return json.loads(text)
    except Exception as e:
        print(f"[RAIOX] Erro Claude API: {e}")
        return None

def auto_refresh():
    while True:
        time.sleep(CFG["refresh_minutos"] * 60)
        # Atualiza painel do admin
        if CFG.get("subdomain") and CFG.get("token"):
            threading.Thread(
                target=buscar_kommo_user,
                args=("__admin__", CFG["subdomain"], CFG["token"]),
                daemon=True
            ).start()
        # Atualiza painel de cada cliente
        users = load_users()
        for uname, udata in users.items():
            if udata.get("ativo", True):
                threading.Thread(
                    target=buscar_kommo_user,
                    args=(uname, udata.get("kommo_subdomain",""), udata.get("kommo_token","")),
                    daemon=True
                ).start()

# HTML
PAINEL_HTML       = BASE_DIR / "painel_servidor_v2.html"
_painel_html_cache = None
_painel_html_lock  = threading.Lock()

def get_painel_html_base():
    """Lê o HTML uma vez e guarda em cache para evitar disco a cada request."""
    global _painel_html_cache
    with _painel_html_lock:
        if _painel_html_cache is None:
            if not PAINEL_HTML.exists():
                return None
            _painel_html_cache = PAINEL_HTML.read_text(encoding="utf-8")
        return _painel_html_cache

def get_painel_html(user_cfg, cache_data=None, is_admin_view=False, target_user=None):
    html = get_painel_html_base()
    if html is None: return None
    cor1 = user_cfg.get("cor_primaria", CFG["cor_primaria"])
    cor2 = user_cfg.get("cor_secundaria", CFG["cor_secundaria"])
    nome = user_cfg.get("nome", CFG["nome_empresa"])
    logo = user_cfg.get("logo_url", CFG["logo_url"])
    wl_css = f"\n    --bluel:{cor1};--blue2:{cor2};--blue:{cor2};\n"
    html = html.replace(":root{", f":root{{\n{wl_css}", 1)
    html = html.replace("<title>Painel CRM — Live</title>", f"<title>{nome} — Painel</title>")
    html = html.replace("<h1>Painel CRM — Live</h1>", f"<h1>{nome}</h1>")
    if logo:
        bg_css = (
            f"<style>"
            f"body{{background-image:url('{logo}');background-size:cover;background-position:center;background-attachment:fixed;background-repeat:no-repeat;}}"
            f"body::before{{content:'';position:fixed;inset:0;background:rgba(7,9,15,0.55);z-index:0;pointer-events:none;}}"
            f"*{{position:relative;z-index:1;}}"
            f"body::before{{z-index:0;}}"
            f"</style>"
        )
        html = html.replace("</head>", f"{bg_css}</head>", 1)
    if is_admin_view:
        back_btn = '<a href="/admin" style="position:fixed;top:14px;right:16px;z-index:9999;background:#1e3a8a;color:#fff;font-size:12px;font-weight:700;padding:7px 14px;border-radius:8px;text-decoration:none;border:1px solid #2c4dbd">← Voltar ao Admin</a>'
        html = html.replace("</body>", f"{back_btn}</body>", 1)

    cache_json = json.dumps(cache_data, ensure_ascii=False, separators=(",", ":")) if cache_data else "null"
    pill_txt = "Visualizacao admin" if is_admin_view else "Atualizado"
    api_url = "/api/dados?user=" + target_user if target_user else "/api/dados"

    # Script injetado no <head> — funciona independente do HTML interno
    head_script = (
        "<script>\n"
        f"window._DADOS_EMBUTIDOS={cache_json};\n"
        f"window._PILL_TXT='{pill_txt}';\n"
        f"window._API_URL='{api_url}';\n"
        "window._crm_loaded=false;\n"
        "function _crm_render(dados){\n"
        "  try{\n"
        "    window.RAW_DATA=dados.leads||[];\n"
        "    window.STATUS_MAP=dados.status_map||{};\n"
        "    window.FUNIS=dados.funis||[];\n"
        "    if(typeof preComputeLeads==='function')preComputeLeads();\n"
        "    if(typeof renderFunilBar==='function')renderFunilBar();\n"
        "    var range=window.currentRange||'30d';\n"
        "    if(typeof render==='function')render(range);\n"
        "    var ls=document.getElementById('loadingScreen');\n"
        "    if(ls)ls.style.display='none';\n"
        "    var le=document.getElementById('loadErr');\n"
        "    if(le)le.style.display='none';\n"
        "    if(typeof setPill==='function')setPill('live',window._PILL_TXT);\n"
        "    window._crm_loaded=true;\n"
        "  }catch(e){console.error('[CRM render]',e);}\n"
        "}\n"
        "function _crm_fetch(){\n"
        "  var ctrl=new AbortController();\n"
        "  var tid=setTimeout(function(){ctrl.abort();},95000);\n"
        "  fetch(window._API_URL,{signal:ctrl.signal})\n"
        "    .then(function(r){clearTimeout(tid);return r.json();})\n"
        "    .then(function(j){\n"
        "      if(j&&j.dados){_crm_render(j.dados);}\n"
        "      else if(j&&j.erro){console.error('[CRM]',j.erro);setTimeout(_crm_fetch,10000);}\n"
        "      else{setTimeout(_crm_fetch,5000);}\n"
        "    })\n"
        "    .catch(function(e){clearTimeout(tid);console.warn('[CRM]',e);setTimeout(_crm_fetch,8000);});\n"
        "}\n"
        "document.addEventListener('DOMContentLoaded',function(){\n"
        "  if(window._DADOS_EMBUTIDOS){\n"
        "    _crm_render(window._DADOS_EMBUTIDOS);\n"
        "  }else{\n"
        "    _crm_fetch();\n"
        "  }\n"
        "  setInterval(_crm_fetch,30000);\n"
        "});\n"
        "window.forcarRefresh=function(){\n"
        "  var btn=document.getElementById('btnRefresh');\n"
        "  if(btn){btn.disabled=true;btn.textContent='↻ Atualizando…';}\n"
        "  fetch('/api/refresh').then(function(){setTimeout(_crm_fetch,800);}).catch(function(e){console.warn('[refresh]',e);});\n"
        "  setTimeout(function(){if(btn){btn.disabled=false;btn.textContent='↻ Atualizar agora';}},4000);\n"
        "};\n"
        "</script>"
    )

    if "<head>" in html:
        html = html.replace("<head>", "<head>" + head_script, 1)
    elif "</head>" in html:
        html = html.replace("</head>", head_script + "</head>", 1)

    html = html.replace("let RAW_DATA=", "var RAW_DATA=", 1)
    html = html.replace("let RAW_DATA =", "var RAW_DATA =", 1)
    html = html.replace("let currentRange=", "var currentRange=", 1)
    html = html.replace("let STATUS_MAP=", "var STATUS_MAP=", 1)
    html = html.replace("let FUNIS=", "var FUNIS=", 1)
    # Desabilita funcoes originais que causam conflito
    html = html.replace("setInterval(()=>carregarDados(),60000);", "//setInterval desabilitado", 1)
    html = html.replace("setInterval(()=>carregarDados(),30000);", "//setInterval desabilitado", 1)
    html = html.replace("setInterval(() => carregarDados(), 60000);", "//setInterval desabilitado", 1)
    html = html.replace("setInterval(() => carregarDados(), 30000);", "//setInterval desabilitado", 1)
    # Neutraliza chamada inicial original se existir
    html = html.replace("carregarDados();", "//carregarDados() desabilitado - usando _crm_fetch", 1)
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
    <p class="sub">Painel CRM &middot; Acesso restrito</p>
  </div>
  <div class="err">{err_msg}</div>
  <form method="POST" action="/login">
    <label>Usuario</label>
    <input type="text" name="username" placeholder="seu usuario" autocomplete="username" value="{username_value}" required/>
    <label>Senha</label>
    <input type="password" name="password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;" autocomplete="current-password" required/>
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
<title>Admin &mdash; Painel CRM</title>
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
.msg{{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);color:#22c55e;padding:10px 14px;border-radius:8px;font-size:12.5px;margin-bottom:20px;display:{msg_display}}}
.empty{{text-align:center;padding:48px 20px;color:#5a657d;font-size:13px}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <h1>Admin &mdash; Painel CRM</h1>
    <p class="sub">Gerencie todos os clientes ativos</p>
  </div>
  <a href="/admin/meu-painel" class="logout-btn" style="background:#1e3a8a;color:#fff;border-color:#2c4dbd">Meu Painel</a>
  <a href="/logout" class="logout-btn">Sair</a>
</div>
<div class="msg">{msg}</div>
<div class="add-form">
  <h2>Adicionar novo cliente</h2>
  <form method="POST" action="/admin/add" enctype="multipart/form-data">
    <div class="form-grid">
      <div class="form-group">
        <label>Usuario (login)</label>
        <input type="text" name="username" placeholder="ex: empresa_abc" required/>
      </div>
      <div class="form-group">
        <label>Senha</label>
        <input type="password" name="password" placeholder="senha do cliente" required/>
      </div>
      <div class="form-group">
        <label>Nome da empresa</label>
        <input type="text" name="nome" placeholder="ex: Valentim Imoveis" required/>
      </div>
      <div class="form-group">
        <label>E-mail do cliente</label>
        <input type="email" name="email" placeholder="cliente@email.com" required/>
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
        <label>Cor primaria</label>
        <input type="color" name="cor" value="#3b82f6" style="width:100%;height:38px;padding:2px;border:1px solid #1d2740;border-radius:6px;background:#0f1320;cursor:pointer"/>
      </div>
      <div class="form-group">
        <label>Cor secundaria</label>
        <input type="color" name="cor2" value="#1e3a8a" style="width:100%;height:38px;padding:2px;border:1px solid #1d2740;border-radius:6px;background:#0f1320;cursor:pointer"/>
      </div>
      <div class="form-group" style="grid-column:1/-1">
        <label>Imagem de fundo do painel (opcional)</label>
        <input type="file" name="logo_file" accept="image/*" style="background:#0f1320;border:1px solid #1d2740;border-radius:6px;padding:6px;color:#eaeef7;width:100%"/>
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

# HTTP SERVER
_thread_pool = ThreadPoolExecutor(max_workers=60)  # máx 60 threads simultâneas

class ThreadedHTTPServer(HTTPServer):
    allow_reuse_address = True
    request_queue_size  = 128   # fila de conexões pendentes

    def process_request(self, request, client_address):
        """Usa pool fixo em vez de criar thread nova por requisição."""
        _thread_pool.submit(self.__process, request, client_address)

    def __process(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

class Handler(BaseHTTPRequestHandler):
    timeout = 30   # fecha conexão travada após 30s

    def log_message(self, fmt, *args):
        pass   # silencia logs de acesso no console

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
        self.send_header("Set-Cookie", f"{name}={value}; Path=/; HttpOnly; Secure; Max-Age={max_age}; SameSite=Lax")

    def clear_cookie(self, name):
        self.send_header("Set-Cookie", f"{name}=; Path=/; HttpOnly; Max-Age=0")

    MAX_BODY = 10 * 1024 * 1024  # 10 MB limite de body

    def read_body(self):
        length = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY)
        return self.rfile.read(length).decode("utf-8", errors="replace") if length else ""

    def parse_form(self):
        body = self.read_body()
        result = {}
        for pair in body.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                from urllib.parse import unquote_plus
                result[unquote_plus(k)] = unquote_plus(v)
        return result

    def parse_multipart(self):
        import re as _re
        ctype   = self.headers.get("Content-Type", "")
        clength = min(int(self.headers.get("Content-Length", 0) or 0), self.MAX_BODY)
        raw     = self.rfile.read(clength) if clength else b""
        fields, files = {}, {}
        m = _re.search(rb"boundary=([^\s;]+)", ctype.encode())
        if not m:
            # fallback: trata como form normal
            from urllib.parse import parse_qs
            decoded = raw.decode("utf-8", errors="replace")
            for k, vs in parse_qs(decoded).items():
                fields[k] = vs[0]
            return fields, files
        boundary = b"--" + m.group(1).strip(b'"')
        parts = raw.split(boundary)
        for part in parts[1:]:
            if part in (b"--\r\n", b"--", b"\r\n--"):
                continue
            if b"\r\n\r\n" not in part:
                continue
            header_raw, _, body = part.partition(b"\r\n\r\n")
            body = body.rstrip(b"\r\n")
            header_str = header_raw.decode("utf-8", errors="replace")
            nm = _re.search(r'name="([^"]+)"', header_str)
            fm = _re.search(r'filename="([^"]*)"', header_str)
            if not nm:
                continue
            name = nm.group(1)
            if fm and fm.group(1):
                class _File:
                    pass
                f = _File()
                f.filename = fm.group(1)
                f.file     = __import__("io").BytesIO(body)
                files[name] = f
            else:
                fields[name] = body.decode("utf-8", errors="replace")
        return fields, files

    def render_login(self, error="", title=None, logo="", cor="#3b82f6", prefill=""):
        t = title or CFG["nome_empresa"]
        logo_tag = f'<img src="{logo}" style="max-height:48px;border-radius:8px;display:block;margin:0 auto 12px"/>' if logo else ""
        html = LOGIN_HTML.format(
            title=t, cor=cor, logo_tag=logo_tag,
            err_display="block" if error else "none",
            err_msg=error or "",
            username_value=prefill
        )
        return html.encode("utf-8")

    def render_admin(self, msg=""):
        users = load_users()
        if not users:
            clientes_html = '<div class="empty">Nenhum cliente cadastrado ainda.</div>'
        else:
            parts = []
            for uname, ud in users.items():
                ativo = ud.get("ativo", True)
                status_cls = "ativo" if ativo else "bloqueado"
                status_txt = "Ativo" if ativo else "Bloqueado"
                card_cls   = "" if ativo else "bloqueado"
                btn_toggle = (
                    f'<form method="POST" action="/admin/block" style="display:inline">'
                    f'<input type="hidden" name="username" value="{uname}">'
                    f'<button class="btn-sm btn-block" type="submit">Bloquear</button></form>'
                ) if ativo else (
                    f'<form method="POST" action="/admin/unblock" style="display:inline">'
                    f'<input type="hidden" name="username" value="{uname}">'
                    f'<button class="btn-sm btn-unblock" type="submit">Desbloquear</button></form>'
                )
                parts.append(
                    f'<div class="client-card {card_cls}">'
                    f'<div class="client-top"><div>'
                    f'<div class="client-name">{ud.get("nome","")}</div>'
                    f'<div class="client-user">@{uname}</div></div>'
                    f'<span class="status-badge {status_cls}">{status_txt}</span></div>'
                    '<div class="client-info"><b>Kommo:</b> ' + ud.get('kommo_subdomain','') + '.kommo.com' +
                    ('<br><b>Email:</b> ' + ud.get('email','') if ud.get('email') else '') +
                    ('<br><b>Link cartao:</b> <a href="' + ud.get('asaas_payment_link','') + '" target="_blank" style="color:#3b82f6">abrir link</a>' if ud.get('asaas_payment_link') else '<br><b>Link cartao:</b> <span style="color:#6b7280">não definido</span>') +
                    (f'<br><form method="POST" action="/admin/salvar_link" style="display:flex;gap:4px;margin-top:4px;align-items:center"><input type="hidden" name="username" value="{uname}"><input type="url" name="link" placeholder="https://www.asaas.com/c/..." value="{ud.get("asaas_payment_link","")}" style="font-size:11px;padding:3px 6px;border:1px solid #374151;background:#1f2937;color:#fff;border-radius:4px;width:220px"><button type="submit" style="background:#3b82f6;border:none;color:#fff;cursor:pointer;font-size:11px;padding:3px 8px;border-radius:4px">Salvar</button></form>') +
                    f'<br><form method="POST" action="/admin/salvar_cores" style="display:flex;gap:6px;margin-top:6px;align-items:center"><input type="hidden" name="username" value="{uname}"><label style="font-size:11px;color:#8590a8">Cor 1</label><input type="color" name="cor" value="{ud.get("cor_primaria","#3b82f6")}" style="width:36px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none"><label style="font-size:11px;color:#8590a8">Cor 2</label><input type="color" name="cor2" value="{ud.get("cor_secundaria","#1e3a8a")}" style="width:36px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none"><button type="submit" style="background:#6366f1;border:none;color:#fff;cursor:pointer;font-size:11px;padding:3px 8px;border-radius:4px">Aplicar cores</button></form>' +
                    '</div>'
                    f'<div class="client-actions">'
                    f'<a href="/admin/view/{uname}" class="btn-sm btn-view">Ver painel</a>'
                    f'{btn_toggle}'
                    f'<form method="POST" action="/admin/delete" style="display:inline" onsubmit="return confirm(\'Deletar {uname}?\')">'
                    f'<input type="hidden" name="username" value="{uname}">'
                    f'<button class="btn-sm btn-del" type="submit">Excluir</button></form>'
                    f'</div></div>'
                )
            clientes_html = "\n".join(parts)
        html = ADMIN_HTML.format(
            clientes_html=clientes_html,
            msg=msg,
            msg_display="block" if msg else "none"
        )
        return html.encode("utf-8")

    def do_GET(self):
        try:
            self._handle_GET()
        except Exception as e:
            print(f"[ERROR] GET {self.path}: {e}")
            try: self.send_json(500, {"erro": "erro interno"})
            except: pass

    def _handle_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        token  = get_cookie(self, "crm_session")
        sess   = get_session(token)

        if path in ("/", "/login"):
            if sess:
                if sess["is_admin"]: return self.redirect("/admin")
                return self.redirect("/painel")
            self.send_html(200, self.render_login(logo=CFG["logo_url"], cor=CFG["cor_primaria"]))
            return

        # Link personalizado por cliente: /painel/nome-empresa
        if path.startswith("/painel/") and len(path) > 8:
            uname = path.replace("/painel/", "").strip("/").lower()
            users = load_users()
            if uname in users:
                ud = users[uname]
                if sess and sess["user"] == uname: return self.redirect("/painel")
                self.send_html(200, self.render_login(
                    title=ud.get("nome", uname),
                    logo=ud.get("logo_url",""),
                    cor=ud.get("cor_primaria", CFG["cor_primaria"]),
                    prefill=uname
                ))
            else:
                self.send_html(200, self.render_login(cor=CFG["cor_primaria"]))
            return

        if path == "/health":
            self.send_json(200, {"status": "ok", "usuarios": len(get_users()), "sessoes": len(_sessions)})
            return

        if path == "/logout":
            if token: delete_session(token)
            self.send_response(302)
            self.clear_cookie("crm_session")
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if path == "/admin":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            self.send_html(200, self.render_admin())
            return

        if path.startswith("/admin/view/"):
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            uname = path.replace("/admin/view/", "").strip("/")
            users = load_users()
            if uname not in users: return self.send_json(404, {"erro": "cliente nao encontrado"})
            ud = users[uname]
            cache, lock = get_cache(uname)
            with lock:
                c_status = cache["status"]
            if c_status not in ("atualizando", "ok"):
                threading.Thread(
                    target=buscar_kommo_user,
                    args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                    daemon=True
                ).start()
            body = get_painel_html(ud, cache_data=None, is_admin_view=True, target_user=uname)
            if body: self.send_html(200, body)
            else: self.send_json(500, {"erro": "HTML nao encontrado"})
            return

        if path == "/admin/meu-painel":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            ud = {
                "nome": CFG["nome_empresa"],
                "logo_url": CFG["logo_url"],
                "cor_primaria": CFG["cor_primaria"],
                "kommo_subdomain": CFG["subdomain"],
                "kommo_token": CFG["token"],
            }
            cache, lock = get_cache("__admin__")
            with lock:
                c_status = cache["status"]
            if c_status not in ("atualizando", "ok"):
                threading.Thread(
                    target=buscar_kommo_user,
                    args=("__admin__", CFG["subdomain"], CFG["token"]),
                    daemon=True
                ).start()
            body = get_painel_html(ud, cache_data=None, is_admin_view=True)
            if body: self.send_html(200, body)
            else: self.send_json(500, {"erro": "HTML nao encontrado"})
            return

        if path.startswith("/admin/api/"):
            if not sess or not sess["is_admin"]: return self.send_json(401, {"erro": "nao autorizado"})
            uname = path.replace("/admin/api/", "").strip("/").split("/")[0]
            cache, lock = get_cache(uname)
            with lock:
                resp = {"status": cache["status"], "atualizado_em": cache["atualizado_em"],
                        "erro": cache["erro"], "dados": cache["dados"]}
            return self.send_json(200, resp)

        if path == "/painel":
            if not sess: return self.redirect("/login")
            if sess["is_admin"]: return self.redirect("/admin")
            users = load_users()
            uname = sess["user"]
            ud = users.get(uname, {})
            if not ud.get("ativo", True):
                self.send_html(403, b"<html><body style='background:#07090f;color:#ef4444;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh'><h2>Acesso suspenso</h2></body></html>")
                return
            cache, lock = get_cache(uname)
            with lock:
                c_status = cache["status"]
            if c_status not in ("atualizando", "ok"):
                threading.Thread(
                    target=buscar_kommo_user,
                    args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                    daemon=True
                ).start()
            body = get_painel_html(ud, cache_data=None)
            if body: self.send_html(200, body)
            else: self.send_json(500, {"erro": "HTML nao encontrado"})
            return

        # API dados — long-poll: aguarda ate 90s pelos dados
        if path == "/api/dados":
            if not sess:
                return self.send_json(200, {"status": "aguardando", "dados": None, "atualizado_em": None, "erro": None})
            qs = parse_qs(parsed.query)
            target_qs = qs.get("user", [None])[0]
            if sess["is_admin"] and target_qs:
                cache_key = target_qs
            elif sess["is_admin"]:
                cache_key = "__admin__"
            else:
                cache_key = sess["user"]
            cache, lock = get_cache(cache_key)
            with lock:
                c_dados  = cache["dados"]
                c_status = cache["status"]
            if c_dados is None and c_status != "atualizando":
                users = load_users()
                if cache_key == "__admin__":
                    threading.Thread(
                        target=buscar_kommo_user,
                        args=("__admin__", CFG["subdomain"], CFG["token"]),
                        daemon=True
                    ).start()
                else:
                    ud2 = users.get(cache_key, {})
                    threading.Thread(
                        target=buscar_kommo_user,
                        args=(cache_key, ud2.get("kommo_subdomain",""), ud2.get("kommo_token","")),
                        daemon=True
                    ).start()
            # Aguarda ate 90s se ainda nao tem dados
            if c_dados is None:
                for _ in range(180):
                    time.sleep(0.5)
                    with lock:
                        c_dados  = cache["dados"]
                        c_status = cache["status"]
                    if c_dados is not None or c_status == "erro":
                        break
            with lock:
                resp = {"status": cache["status"], "atualizado_em": cache["atualizado_em"],
                        "erro": cache["erro"], "dados": cache["dados"]}
            return self.send_json(200, resp)

        # DEBUG
        if path == "/debug":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            linhas = [f"<h2>Debug {datetime.now().strftime('%H:%M:%S')}</h2><pre>"]
            linhas.append(f"subdomain: {CFG.get('subdomain','(vazio)')}")
            linhas.append(f"token: {'OK (' + str(len(CFG.get('token',''))) + 'chars)' if CFG.get('token') else '(VAZIO!)'}")
            linhas.append(f"PAINEL_HTML existe: {PAINEL_HTML.exists()}")
            linhas.append("")
            for k, v in list(_caches.items()):
                leads = len((v.get("dados") or {}).get("leads") or [])
                linhas.append(f"[{k}] status={v['status']} | leads={leads} | erro={v.get('erro')}")
            linhas.append("</pre>")
            body = ("<html><head><meta charset='utf-8'><meta http-equiv='refresh' content='5'>"
                    "<style>body{background:#07090f;color:#c8d0e0;font-family:monospace;padding:24px}"
                    "h2{color:#3b82f6}pre{background:#0f1320;padding:16px;border-radius:8px;line-height:1.8}"
                    "</style></head><body>" + "".join(linhas) + "</body></html>")
            return self.send_html(200, body.encode("utf-8"))

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

        if path == "/api/raiox":
            if not sess: return self.send_json(401, {"erro": "nao autorizado"})
            qs = parse_qs(parsed.query)
            target_qs = qs.get("user", [None])[0]
            if sess["is_admin"] and target_qs:
                cache_key = target_qs
            elif sess["is_admin"]:
                cache_key = "__admin__"
            else:
                cache_key = sess["user"]
            cache, lock = get_cache(cache_key)
            with lock:
                dados_cache = cache.get("dados") or {}
            leads_raw = dados_cache.get("leads") or []
            if not leads_raw:
                return self.send_json(200, {"erro": "sem_dados", "msg": "Ainda sem dados — aguarde o carregamento do painel."})
            meta_val = dados_cache.get("meta_mes") or CFG["meta_mes_padrao"]
            mes_atual = datetime.now().strftime("%Y-%m")
            stats = compute_raiox_stats(dados_cache, mes_atual, meta=meta_val)
            if not ANTHROPIC_KEY:
                return self.send_json(200, {"erro": "sem_chave", "msg": "Variável ANTHROPIC_KEY não configurada no servidor.", "stats": stats})
            analise = claude_raiox(stats)
            if analise is None:
                return self.send_json(200, {"erro": "api_error", "msg": "Falha ao chamar Claude API. Verifique ANTHROPIC_KEY e tente novamente.", "stats": stats})
            return self.send_json(200, {"ok": True, "stats": stats, "analise": analise})

        # Serve imagens de fundo dos clientes
        if path.startswith("/img/"):
            fname  = path[5:].replace("..", "").replace("/", "").replace("\\", "").strip()
            if not fname:
                self.send_json(400, {"erro": "arquivo invalido"}); return
            fpath  = DATA_DIR / "logos" / fname
            if fpath.exists():
                ext   = fpath.suffix.lower()
                ctype = "image/svg+xml" if ext == ".svg" else f"image/{ext[1:].replace('jpg','jpeg')}"
                data  = fpath.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type",   ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control",  "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()
            return

        if path == "/debug/canais":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            # Mostra amostra dos leads "Outros" — tags e campos customizados reais
            qs = parse_qs(parsed.query)
            target = qs.get("user", ["__admin__"])[0]
            cache, lock = get_cache(target)
            with lock:
                dados = cache.get("dados")
            if not dados:
                return self.send_json(200, {"erro": "sem dados em cache — acesse o painel primeiro"})
            outros = [l for l in (dados.get("leads") or []) if l.get("canal") == "Outros"]
            amostra = []
            for l in outros[:40]:
                amostra.append({
                    "id":     l.get("id"),
                    "nome":   l.get("cliente",""),
                    "tags":   l.get("tags",""),
                    "canal":  l.get("canal",""),
                })
            contagem = {}
            for l in (dados.get("leads") or []):
                c = l.get("canal","Outros")
                contagem[c] = contagem.get(c, 0) + 1
            return self.send_json(200, {
                "total_outros": len(outros),
                "contagem_canais": contagem,
                "amostra_outros_tags": amostra,
            })

        self.send_json(404, {"erro": "nao encontrado"})

    def do_POST(self):
        try:
            self._handle_POST()
        except Exception as e:
            print(f"[ERROR] POST {self.path}: {e}")
            try: self.send_json(500, {"erro": "erro interno"})
            except: pass

    def _handle_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/webhook/asaas":
            try:
                body = self.read_body()
                data = json.loads(body) if body else {}
            except:
                return self.send_json(400, {"erro": "json invalido"})
            uname, event = asaas_usuario_por_evento(data)
            print("[ASAAS webhook] event=" + event + " user=" + str(uname))
            if uname:
                users = load_users()
                if event == "PAYMENT_OVERDUE":
                    users[uname]["ativo"] = False
                    save_users(users)
                    print("[ASAAS] Bloqueado: " + uname)
                elif event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
                    users[uname]["ativo"] = True
                    save_users(users)
                    print("[ASAAS] Desbloqueado: " + uname)
            return self.send_json(200, {"ok": True})

        token  = get_cookie(self, "crm_session")
        sess   = get_session(token)

        if path == "/login":
            form = self.parse_form()
            uname = form.get("username", "").strip()
            passwd = form.get("password", "").strip()
            if uname == CFG["admin_user"] and passwd == CFG["admin_pass"]:
                new_token = create_session(uname, is_admin=True)
                self.send_response(302)
                self.set_cookie("crm_session", new_token)
                self.send_header("Location", "/admin")
                self.end_headers()
                return
            users = load_users()
            ud = users.get(uname)
            if ud and verify_pass(passwd, ud.get("password_hash", "")):
                if not ud.get("ativo", True):
                    self.send_html(200, self.render_login(error="Acesso suspenso.", cor=CFG["cor_primaria"]))
                    return
                new_token = create_session(uname, is_admin=False)
                self.send_response(302)
                self.set_cookie("crm_session", new_token)
                self.send_header("Location", "/painel")
                self.end_headers()
                cache, _ = get_cache(uname)
                if cache["status"] not in ("atualizando", "ok"):
                    threading.Thread(
                        target=buscar_kommo_user,
                        args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                        daemon=True
                    ).start()
                return
            self.send_html(200, self.render_login(error="Usuario ou senha incorretos.", cor=CFG["cor_primaria"]))
            return

        if path == "/admin/salvar_cores":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","").strip()
            cor   = form.get("cor","").strip()
            cor2  = form.get("cor2","").strip()
            users = load_users()
            if uname in users:
                if cor:  users[uname]["cor_primaria"]   = cor
                if cor2: users[uname]["cor_secundaria"] = cor2
                save_users(users)
            return self.redirect("/admin")

        if path == "/admin/salvar_link":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","").strip()
            link  = form.get("link","").strip()
            users = load_users()
            if uname in users and link:
                users[uname]["asaas_payment_link"] = link
                save_users(users)
            return self.redirect("/admin")

        if path == "/admin/regenerar_link":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","").strip()
            users = load_users()
            if uname in users:
                ud = users[uname]
                sub_id = ud.get("asaas_sub_id","")
                new_link = ""
                if sub_id:
                    pagamentos = asaas_req("GET", f"/subscriptions/{sub_id}/payments")
                    if pagamentos:
                        lista = pagamentos.get("data") or []
                        if lista:
                            p = lista[0]
                            new_link = p.get("invoiceUrl") or p.get("bankSlipUrl") or ""
                    if not new_link:
                        sub_info = asaas_req("GET", f"/subscriptions/{sub_id}")
                        if sub_info:
                            new_link = sub_info.get("paymentLink") or ""
                if new_link:
                    users[uname]["asaas_payment_link"] = new_link
                    save_users(users)
            return self.redirect("/admin")

        if path == "/admin/add":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form, files = self.parse_multipart()
            uname = sanitize_username(form.get("username","")).replace(" ","_")
            if not uname:
                self.send_html(200, self.render_admin("Usuario invalido."))
                return
            # Salva imagem de fundo se enviada
            logo_url = ""
            logo_file = files.get("logo_file")
            if logo_file and logo_file.filename:
                ext = Path(logo_file.filename).suffix.lower()
                if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
                    logos_dir = DATA_DIR / "logos"
                    logos_dir.mkdir(exist_ok=True)
                    fpath = logos_dir / (uname + ext)
                    fpath.write_bytes(logo_file.file.read())
                    logo_url = f"/img/{uname}{ext}"
            users = load_users()
            users[uname] = {
                "password_hash":   hash_pass(form.get("password","123456")),
                "ativo":           True,
                "nome":            form.get("nome",""),
                "email":           form.get("email",""),
                "kommo_subdomain": form.get("subdomain",""),
                "kommo_token":     form.get("token",""),
                "cor_primaria":    form.get("cor","") or CFG["cor_primaria"],
                "cor_secundaria":  form.get("cor2","") or CFG["cor_secundaria"],
                "logo_url":        logo_url,
            }
            if ASAAS_KEY:
                def _asaas_setup(u=uname, n=form.get("nome",""), e=form.get("email","")):
                    cid, sid, plink = asaas_criar_cliente_assinatura(u, n, e)
                    if cid or sid:
                        us = load_users()
                        if u in us:
                            if cid:   us[u]["asaas_customer_id"] = cid
                            if sid:   us[u]["asaas_sub_id"] = sid
                            if plink: us[u]["asaas_payment_link"] = plink
                            save_users(us)
            save_users(users)  # salva ANTES de iniciar thread Asaas — evita race condition
            if ASAAS_KEY:
                threading.Thread(target=_asaas_setup, daemon=True).start()
            ud = users[uname]
            threading.Thread(
                target=buscar_kommo_user,
                args=(uname, ud["kommo_subdomain"], ud["kommo_token"]),
                daemon=True
            ).start()
            # Envia email de boas-vindas
            if ud.get("email"):
                senha_plain = form.get("password","123456")
                host = self.headers.get("Host","")
                base = SITE_URL or (f"https://{host}" if host else "")
                link_acesso = f"{base}/painel/{uname}"
                link_cartao = ud.get("asaas_payment_link","")
                threading.Thread(
                    target=enviar_email_boas_vindas,
                    args=(ud.get("nome",uname), ud["email"], uname, senha_plain, link_acesso, link_cartao),
                    daemon=True
                ).start()
            self.send_html(200, self.render_admin(f"Cliente '{uname}' criado!"))
            return

        if path == "/admin/block":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            uname = self.parse_form().get("username","")
            users = load_users()
            if uname in users:
                users[uname]["ativo"] = False
                save_users(users)
            self.send_html(200, self.render_admin(f"'{uname}' bloqueado."))
            return

        if path == "/admin/unblock":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            uname = self.parse_form().get("username","")
            users = load_users()
            if uname in users:
                users[uname]["ativo"] = True
                save_users(users)
            self.send_html(200, self.render_admin(f"'{uname}' desbloqueado."))
            return

        if path == "/admin/delete":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            uname = self.parse_form().get("username","")
            users = load_users()
            if uname in users:
                sub_id = users[uname].get("asaas_sub_id","")
                if sub_id:
                    threading.Thread(target=asaas_cancelar_assinatura, args=(sub_id,), daemon=True).start()
                del users[uname]
                save_users(users)
            self.send_html(200, self.render_admin(f"'{uname}' removido."))
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
    print(f"  PAINEL CRM v4 — Kommo Multi-cliente")
    print("=" * 52)
    users = load_users()
    for uname, ud in list(users.items()):
        if ud.get("ativo", True) and ud.get("kommo_token"):
            threading.Thread(
                target=buscar_kommo_user,
                args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                daemon=True
            ).start()
    threading.Thread(target=auto_refresh,    daemon=True).start()
    threading.Thread(target=cleanup_sessions, daemon=True).start()
    server = ThreadedHTTPServer(("0.0.0.0", CFG["porta"]), Handler)
    print(f"  Servidor: http://localhost:{CFG['porta']}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
