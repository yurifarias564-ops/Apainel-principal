#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel CRM Kommo — Servidor v4

ESTRUTURA (onde editar cada coisa):
  painel_servidor_v2.html  → painel NEGÓCIOS LOCAIS
  painel_corretor.html     → painel CORRETOR
  servidor_v4.py           → lógica do servidor (este arquivo)
- Login com senha por cliente
- Admin master: ve todos os paineis
- Pode bloquear/desbloquear clientes
- White label por cliente
"""
import json, os, re, ssl, sys, time, threading, urllib.request, urllib.error, hashlib, secrets, calendar
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
# ══════════════════════════════  CONFIGURAÇÃO  ══════════════════════════════

DATA_DIR    = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE     = BASE_DIR / "config.json"
USERS_FILE      = DATA_DIR / "users.json"
CORRETORES_FILE = DATA_DIR / "corretores.json"
CACHE_FILE      = DATA_DIR / "crm_cache.json"
EMAIL_LOG_FILE  = DATA_DIR / "email_log.json"
ADMIN_CREDS_FILE = DATA_DIR / "admin_creds.json"

def get_admin_pass():
    """Retorna a senha atual do admin — prioriza o arquivo local (trocada via painel)."""
    try:
        if ADMIN_CREDS_FILE.exists():
            d = json.loads(ADMIN_CREDS_FILE.read_text(encoding="utf-8"))
            if d.get("password_hash"):
                return None, d["password_hash"]   # (plain=None, hash)
    except Exception:
        pass
    return CFG["admin_pass"], None   # (plain, hash=None)

def set_admin_pass(nova_senha):
    """Salva nova senha do admin no arquivo local (persiste reinicializações)."""
    ADMIN_CREDS_FILE.write_text(
        json.dumps({"password_hash": hash_pass(nova_senha)}, ensure_ascii=False),
        encoding="utf-8"
    )

def check_admin_pass(passwd):
    """Verifica se a senha bate com a do admin (plain ou hash)."""
    plain, hashed = get_admin_pass()
    if hashed:
        return verify_pass(passwd, hashed)
    return passwd == plain

# ── Fuso horário Brasil (UTC-3) ──────────────────────────────────────────────
def br_now():
    """Retorna datetime atual no fuso de Brasília (UTC-3)."""
    return datetime.utcnow() - timedelta(hours=3)

# ── Trial: 14 dias gratuitos ─────────────────────────────────────────────────
TRIAL_DIAS = 14

def trial_info(ud):
    """Retorna (dentro_do_trial, dias_restantes, expirado).
    Usuários sem trial_inicio ou com ativado=True são sempre liberados."""
    if ud.get("ativado", True):          # já pago / ativado manualmente
        return True, None, False
    inicio_str = ud.get("trial_inicio")
    if not inicio_str:                   # usuário antigo sem campo → libera
        return True, None, False
    try:
        inicio = datetime.fromisoformat(inicio_str)
    except Exception:
        return True, None, False
    dias_passados = (br_now() - inicio).days
    dias_rest     = max(0, TRIAL_DIAS - dias_passados)
    expirado      = dias_passados >= TRIAL_DIAS
    return not expirado, dias_rest, expirado

def _html_trial_bloqueado(titulo, mensagem):
    empresa = CFG.get("nome_empresa", "Painel CRM")
    cor     = CFG.get("cor_primaria", "#3b82f6")
    wa      = SUPORTE_WHATSAPP.strip().lstrip("+").replace(" ", "")
    btn_suporte = (
        f"<a class='btn' href='https://wa.me/{wa}?text=Quero+ativar+meu+acesso' target='_blank'>💬 Falar com suporte</a>"
        if wa else
        "<p style='color:#6b7280;font-size:13px'>Entre em contato com o suporte para ativar seu acesso.</p>"
    )
    html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{titulo} — {empresa}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#07090f;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.card{{background:#0f1320;border:1px solid #1d2740;border-radius:16px;padding:40px 36px;
  max-width:440px;width:100%;text-align:center;box-shadow:0 8px 40px rgba(0,0,0,.5)}}
.icon{{font-size:52px;margin-bottom:16px}}
h1{{font-size:22px;font-weight:700;color:#f1f5f9;margin-bottom:12px}}
p{{font-size:15px;color:#8590a8;line-height:1.6;margin-bottom:28px}}
.empresa{{font-size:12px;color:#4b5563;margin-top:24px}}
.btn{{display:inline-block;background:linear-gradient(135deg,{cor},{cor}cc);
  color:#fff;font-weight:700;font-size:15px;padding:13px 32px;border-radius:10px;
  text-decoration:none;letter-spacing:.3px}}
.btn:hover{{opacity:.9}}
</style></head>
<body><div class='card'>
  <div class='icon'>🔒</div>
  <h1>{titulo}</h1>
  <p>{mensagem}</p>
  {btn_suporte}
  <div class='empresa'>{empresa}</div>
</div></body></html>"""
    return html.encode("utf-8")

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
GMAIL_USER        = os.environ.get("GMAIL_USER", "")
GMAIL_PASS        = os.environ.get("GMAIL_PASS", "")
SITE_URL          = os.environ.get("SITE_URL", "").rstrip("/")
ALERT_EMAIL       = os.environ.get("ALERT_EMAIL", "")
SUPORTE_WHATSAPP  = os.environ.get("SUPORTE_WHATSAPP", "")  # ex: 5511999999999  # email que recebe os alertas (pode ser o mesmo do Gmail)

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
_corretores_db   = {}
_corretores_lock = threading.Lock()

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

def load_corretores():
    global _corretores_db
    with _corretores_lock:
        try:
            if CORRETORES_FILE.exists():
                _corretores_db = json.loads(CORRETORES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[ERRO] Falha ao carregar corretores.json: {e}")
        return dict(_corretores_db)

def save_corretores(data):
    global _corretores_db
    with _corretores_lock:
        _corretores_db = dict(data)
        try:
            tmp = CORRETORES_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(CORRETORES_FILE)
        except Exception as e:
            print(f"[ERRO] Falha ao salvar corretores.json: {e}")

def get_corretores():
    with _corretores_lock:
        return dict(_corretores_db)

# ═══════════════════════  UTILITÁRIOS COMPARTILHADOS  ═══════════════════════

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
# Inclui: tags do Kommo, valores do campo MESSENGER, rastreamento via QR [SRC:xxx]
# ══════════════════════  DETECÇÃO DE CANAL (ambos)  ════════════════════════

_CANAL_PATTERNS = [
    ("TikTok",    [r"tiktok", r"tik.?tok", r"tik_?tok",
                   r"\[src:tiktok\]", r"#src_tiktok", r"#tiktok"]),
    ("Instagram", [r"instagram", r"insta\b", r"ig\b", r"@instagram",
                   r"instagram_dm", r"instagram_direct",
                   r"\[src:ig\]", r"\[src:instagram\]", r"#src_ig", r"#instagram"]),
    ("Facebook",  [r"facebook", r"fb\b", r"meta\b",
                   r"fbmessenger", r"fb_messenger", r"facebook_messenger",
                   r"\[src:fb\]", r"\[src:facebook\]", r"#src_fb"]),
    ("YouTube",   [r"youtube", r"yt\b", r"youtu\.be",
                   r"\[src:yt\]", r"\[src:youtube\]"]),
    ("Google",    [r"google", r"gads", r"g.?ads", r"adwords", r"cpc\b", r"ppc\b",
                   r"\[src:google\]", r"\[src:gads\]", r"#src_google"]),
    ("WhatsApp",  [r"whatsapp", r"whats.?app", r"wpp\b", r"zap\b", r"wts\b",
                   r"wsapp", r"whatsap",
                   # Valores do campo MESSENGER do Kommo (WhatsApp Business API)
                   r"\bwaba\b", r"amocrm_waba", r"kommo_waba",
                   r"\[src:wpp\]", r"\[src:whatsapp\]", r"#src_wpp"]),
    ("Email",     [r"e.?mail", r"newsletter", r"smtp"]),
    ("Organic",   [r"organic", r"seo\b", r"busca", r"search"]),
    ("Indicacao", [r"indica[cç]", r"referral", r"indica[cç]ao",
                   r"\[src:indicacao\]", r"#src_indicacao"]),
    ("Site",      [r"site\b", r"website", r"landing", r"form",
                   r"\[src:site\]", r"#src_site"]),
]

# Mapa de valores exatos do campo MESSENGER do Kommo → canal
# (verificação antes dos padrões regex para máxima precisão)
_KOMMO_MESSENGER_MAP = {
    # WhatsApp
    "waba":            "WhatsApp",
    "amocrm_waba":     "WhatsApp",
    "whatsapp":        "WhatsApp",
    "whatsapp_biz":    "WhatsApp",
    "waba_v2":         "WhatsApp",
    # Instagram
    "instagram":       "Instagram",
    "instagram_dm":    "Instagram",
    "instagram_direct":"Instagram",
    # Facebook
    "fbmessenger":     "Facebook",
    "fb_messenger":    "Facebook",
    "facebook":        "Facebook",
    "facebook_messenger": "Facebook",
    # Telegram
    "telegram":        "Outros",
    "tg":              "Outros",
    # Outros chats
    "vk":              "Outros",
    "viber":           "Outros",
    "skype":           "Outros",
    "sms":             "Outros",
}

def detect_canal(tags_str, contact_fields, lead_name="", lead_fields=None, extra_signals=None):
    """Detecta o canal de origem do lead de forma precisa.
    Verifica (por prioridade):
    1. Campo MESSENGER do Kommo (valor exato → canal)
    2. Tags, campos UTM/source, nome do lead, nome do funil (regex)
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

        # ── Prioridade máxima: campo MESSENGER do Kommo ──────────────
        # Kommo preenche este campo automaticamente com o canal de chat integrado
        if fcode == "MESSENGER":
            for v in (cf.get("values") or []):
                raw = (v.get("value") or v.get("enum_value") or
                       v.get("enum_id") or "")
                val = str(raw).lower().strip()
                if val in _KOMMO_MESSENGER_MAP:
                    return _KOMMO_MESSENGER_MAP[val]
                # fallback regex no valor do MESSENGER
                for canal, patterns in _CANAL_PATTERNS:
                    for pat in patterns:
                        if re.search(pat, val):
                            return canal

        # ── Campos UTM / source / origem ─────────────────────────────
        is_source_field = (
            fcode in ("SOURCE", "CANAL", "ORIGEM", "UTM_SOURCE",
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

# ══════════════════════  INTEGRAÇÃO KOMMO CRM  ════════════════════════════

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

        # ── Sync incremental: só busca leads modificados desde a última sync ──
        # Na 1ª carga (ou forçado) busca tudo; nas seguintes só o que mudou.
        cached_leads_map = {}
        last_sync_ts     = None
        with lock:
            prev = cache.get("dados")
        if prev and prev.get("leads"):
            cached_leads_map = {l["id"]: l for l in prev["leads"]}
            last_sync_ts = prev.get("_last_kommo_ts")  # unix do lead mais recente

        LEAD_EXTRA = "&with=contacts,tags,custom_fields_values"
        if last_sync_ts and cached_leads_map:
            # Busca apenas leads modificados depois do último timestamp (com 5min de margem)
            margin = last_sync_ts - 300
            leads_raw = fetch_all(base_url, token, "/leads", "leads",
                                  f"{LEAD_EXTRA}&filter[updated_at][from]={margin}")
            print(f"  [sync incremental] {len(leads_raw)} leads modificados (de {len(cached_leads_map)} em cache)")
        else:
            leads_raw = fetch_all(base_url, token, "/leads", "leads", LEAD_EXTRA)
            print(f"  [sync full] {len(leads_raw)} leads")

        # ── Contatos em paralelo (8 workers) ─────────────────────────────────
        contact_ids = set()
        for l in leads_raw:
            for c in (l.get("_embedded") or {}).get("contacts") or []:
                if c.get("id"): contact_ids.add(c["id"])

        phone_map           = {}
        _contact_cache      = {}   # cid -> custom_fields_values
        _contact_name_cache = {}   # cid -> nome

        def _fetch_contact_batch(batch):
            query = "&".join(f"id[]={cid}" for cid in batch)
            url   = f"{base_url}/contacts?{query}&with=custom_fields_values&limit=250"
            try:
                data = kommo_get(url, token)
                if not data: return {}
                out = {}
                for c in (data.get("_embedded") or {}).get("contacts") or []:
                    out[c["id"]] = {
                        "phone":  get_phone(c) or "",
                        "fields": c.get("custom_fields_values") or [],
                        "name":   c.get("name") or "",
                    }
                return out
            except:
                return {}

        BATCH    = 100   # 100 ids por requisição (era 50) — reduz nº de chamadas pela metade
        WORKERS  = 8     # 8 requisições em paralelo
        ids      = list(contact_ids)
        batches  = [ids[i:i+BATCH] for i in range(0, len(ids), BATCH)]
        print(f"  [contatos] {len(ids)} únicos → {len(batches)} lotes × {WORKERS} workers")
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(_fetch_contact_batch, batches):
                for cid, d in result.items():
                    if d["phone"]:  phone_map[cid]           = d["phone"]
                    _contact_cache[cid]      = d["fields"]
                    _contact_name_cache[cid] = d["name"]

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
            nome_contato = ""
            for c in contacts:
                cid = c.get("id")
                if cid and cid in _contact_cache:
                    ct_fields = _contact_cache[cid]
                    nome_contato = _contact_name_cache.get(cid, "")
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
                "fechado_em": l.get("closed_at") or 0,  # data real de fechamento (ganho/perdido)
                "vendedor": user_map.get(l.get("responsible_user_id"), "Nao atribuido"),
                "cliente": cliente, "nome_contato": nome_contato, "telefone": phone, "valor": l.get("price") or 0,
                "canal": canal, "funil": funil, "etapa": l.get("status_id"), "tags": tags_str,
            })

        # ── Merge incremental: combina novos/atualizados com cache existente ──
        if cached_leads_map:
            for l in all_leads:
                cached_leads_map[l["id"]] = l   # sobrescreve ou insere
            all_leads = list(cached_leads_map.values())
            print(f"  [merge] total após merge: {len(all_leads)} leads")

        # Timestamp do lead mais recente (para próxima sync incremental)
        new_last_ts = max((l.get("modificado") or 0 for l in all_leads), default=0)

        funil_counts = {}
        for l in all_leads: funil_counts[l["funil"]] = funil_counts.get(l["funil"], 0) + 1

        resultado = {
            "leads": all_leads, "status_map": status_map,
            "canais": ["WhatsApp", "Instagram", "TikTok", "Facebook", "YouTube", "Google", "Email", "Organic", "Indicacao", "Site", "Outros"],
            "funis": sorted(funil_counts.keys()), "total": len(all_leads),
            "_last_kommo_ts": new_last_ts,   # interno — usado pelo sync incremental
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

def compute_raiox_stats(dados, mes, meta=50000, vendor_tags=None, filtro_vendedor=''):
    """Computa estatísticas do mês selecionado para o Raio-X Comercial.
    Todos os dados (resultado + operacional) são filtrados pelo mês informado.
    """
    leads      = dados.get("leads") or []
    status_map = dados.get("status_map") or {}
    agora      = datetime.now().timestamp()

    def tipo_lead(l):
        return (status_map.get(l.get("etapa")) or {}).get("tipo", "andamento")

    # ── Detecta vendedor via tag (igual ao painel) ───────────────────────────
    def _tag_vend(l):
        tags_str = l.get("tags") or ""
        if not tags_str:
            return filtro_vendedor or None
        parts = [t.strip().upper() for t in str(tags_str).split(",") if t.strip()]
        if vendor_tags:
            for t in parts:
                if t in vendor_tags:
                    return t
            return filtro_vendedor or None
        import re as _re
        for t in parts:
            if len(t) < 2: continue
            if _re.match(r"^FOLLOWUP:\d+$", t): continue
            if _re.match(r"^[A-ZÁÉÍÓÚÃÕÂÊÎÔÛÀÈÌÒÙÇ\s]+$", t):
                return t
        return filtro_vendedor or None

    # ── FILTRO DE MÊS ────────────────────────────────────────────────────────
    # Ganhos/perdidos → filtro por data de FECHAMENTO (closed_at / fechado_em)
    # Leads recebidos → filtro por data de CRIAÇÃO (created_at / data)
    # Andamento / sem_fup / parados → TODOS os ativos (pipeline real, sem filtro de mês)
    if mes:
        try:
            y, m = int(mes.split("-")[0]), int(mes.split("-")[1])
            ts_ini = datetime(y, m, 1).timestamp()
            ts_fim = datetime(y + (m // 12), (m % 12) + 1, 1).timestamp()

            # Leads CRIADOS no mês (recebidos)
            recebidos_mes = [l for l in leads if ts_ini <= (l.get("data") or 0) < ts_fim]

            # Leads FECHADOS no mês — usa closed_at se disponível, senão modificado
            def _data_fechamento(l):
                return l.get("fechado_em") or l.get("modificado") or l.get("data") or 0

            ganhos   = [l for l in leads
                        if tipo_lead(l) == "ganho"
                        and ts_ini <= _data_fechamento(l) < ts_fim]
            perdidos = [l for l in leads
                        if tipo_lead(l) == "perdido"
                        and ts_ini <= _data_fechamento(l) < ts_fim]
        except Exception:
            recebidos_mes = leads
            ganhos   = [l for l in leads if tipo_lead(l) == "ganho"]
            perdidos = [l for l in leads if tipo_lead(l) == "perdido"]
    else:
        recebidos_mes = leads
        ganhos   = [l for l in leads if tipo_lead(l) == "ganho"]
        perdidos = [l for l in leads if tipo_lead(l) == "perdido"]

    # Pipeline atual — todos os leads EM ANDAMENTO (independente do mês)
    # Reflete a realidade operacional da equipe agora
    andamento = [l for l in leads if tipo_lead(l) == "andamento"]

    valor_ganho = sum(l.get("valor") or 0 for l in ganhos)
    total_recebidos = len(recebidos_mes)
    total_fechados  = len(ganhos) + len(perdidos)
    taxa_conv = round(len(ganhos) / total_fechados * 100, 1) if total_fechados > 0 else 0

    # Métricas operacionais — pipeline real (sem filtro de mês)
    parados = [l for l in andamento
               if agora - (l.get("modificado") or l.get("data") or agora) > 7 * 86400]
    sem_fup = [l for l in andamento if not (l.get("tags") or "")]

    # Sets de IDs para lookup O(1) no loop de vendedores
    parados_ids = {l.get("id") for l in parados}
    sem_fup_ids = {l.get("id") for l in sem_fup}

    # ── STATS POR VENDEDOR ───────────────────────────────────────────────────
    vendor_stats = {}

    def _ensure_vendor(v):
        if v not in vendor_stats:
            vendor_stats[v] = {"ganho": 0, "perdido": 0, "andamento": 0,
                                "valor": 0, "parados": 0, "sem_fup": 0, "taxa_conv": 0}

    for l in ganhos:
        v = _tag_vend(l) or "Não identificado"
        _ensure_vendor(v)
        vendor_stats[v]["ganho"] += 1
        vendor_stats[v]["valor"] += l.get("valor") or 0

    for l in perdidos:
        v = _tag_vend(l) or "Não identificado"
        _ensure_vendor(v)
        vendor_stats[v]["perdido"] += 1

    for l in andamento:
        v = _tag_vend(l) or "Não identificado"
        _ensure_vendor(v)
        vendor_stats[v]["andamento"] += 1
        if l.get("id") in parados_ids: vendor_stats[v]["parados"] += 1
        if l.get("id") in sem_fup_ids: vendor_stats[v]["sem_fup"] += 1

    for v, s in vendor_stats.items():
        tot_v = s["ganho"] + s["perdido"]
        s["taxa_conv"] = round(s["ganho"] / tot_v * 100, 1) if tot_v > 0 else 0

    # ── CANAL / FUNIL / ETAPA PERDA (base: fechados no mês) ─────────────────
    canal_counts = {}
    for l in ganhos + perdidos:
        c = l.get("canal") or "Outros"
        canal_counts[c] = canal_counts.get(c, 0) + 1

    funil_stats = {}
    for l in ganhos + perdidos:
        f = l.get("funil") or "?"
        if f not in funil_stats:
            funil_stats[f] = {"ganho": 0, "perdido": 0, "andamento": 0}
        funil_stats[f][tipo_lead(l)] = funil_stats[f].get(tipo_lead(l), 0) + 1

    etapa_perda = {}
    for l in perdidos:
        lbl = (status_map.get(l.get("etapa")) or {}).get("label", "Desconhecida")
        etapa_perda[lbl] = etapa_perda.get(lbl, 0) + 1
    top_perdas = sorted(etapa_perda.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "mes":              mes or datetime.now().strftime("%Y-%m"),
        "meta":             meta,
        "filtro_vendedor":  filtro_vendedor,
        "total_leads":      total_recebidos,   # leads criados no mês
        "total_fechados":   total_fechados,    # ganhos + perdidos no mês
        "ganhos":           len(ganhos),       # fechados como ganho no mês
        "perdidos":         len(perdidos),     # fechados como perdido no mês
        "em_andamento":     len(andamento),    # pipeline ativo real (sem filtro de mês)
        "valor_ganho":      valor_ganho,
        "taxa_conversao":   taxa_conv,
        "pct_meta":         round(valor_ganho / meta * 100, 1) if meta > 0 else 0,
        "leads_parados":    len(parados),      # ativos sem atualização há 7+ dias
        "leads_sem_fup":    len(sem_fup),      # ativos sem nenhuma tag de follow-up
        "vendor_stats":     vendor_stats,
        "canal_counts":     canal_counts,
        "funil_stats":      funil_stats,
        "top_etapas_perda": top_perdas,
        "total_base":       len(leads),
    }


def claude_raiox(stats):
    """Chama Claude para gerar o Raio-X Comercial com 7 seções."""
    if not ANTHROPIC_KEY:
        return None

    mes_label  = stats.get("mes", "?")
    meta_total = stats.get("meta", 0)
    n_vendors  = max(len(stats.get("vendor_stats", {})), 1)
    meta_vend  = round(meta_total / n_vendors) if meta_total else 0
    filtro     = stats.get("filtro_vendedor", "")

    vendor_txt = ""
    for v, s in list(stats.get("vendor_stats", {}).items())[:10]:
        bateu = "BATEU META" if s["valor"] >= meta_vend and meta_vend > 0 else "NAO BATEU META"
        uso_crm = "MAU USO DO CRM" if (s["sem_fup"] + s["parados"]) > s["andamento"] * 0.5 else "uso adequado"
        vendor_txt += (
            f"  {v}: faturou=R${s['valor']:,.0f} | meta_individual=R${meta_vend:,.0f} | {bateu} | "
            f"ganhou={s['ganho']} | perdeu={s['perdido']} | em_andamento={s['andamento']} | "
            f"conv={s['taxa_conv']}% | parados={s['parados']} | sem_fup={s['sem_fup']} | {uso_crm}\n"
        )

    canal_counts = stats.get("canal_counts", {})
    if canal_counts:
        top = sorted(canal_counts.items(), key=lambda x: x[1], reverse=True)
        canal_txt = ", ".join(f"{c}={n}" for c,n in top[:5])
    else:
        canal_txt = "sem dados"

    meta_empresa_status = "BATEU META DA EMPRESA" if stats.get("pct_meta",0) >= 100 else f"NAO BATEU META DA EMPRESA — ficou em {stats.get('pct_meta',0)}% (R${stats.get('valor_ganho',0):,.0f} de R${meta_total:,.0f})"
    filtro_linha = f"FILTRO: análise somente do vendedor/tag [{filtro}]\n" if filtro else ""

    prompt = f"""Você é um gestor comercial sênior rigoroso e direto. Analise os dados do CRM abaixo e gere um relatório executivo honesto em JSON com 7 seções.

{filtro_linha}MÊS: {mes_label}
META DA EMPRESA: R${meta_total:,.0f} | META INDIVIDUAL POR VENDEDOR: R${meta_vend:,.0f}
RESULTADO GERAL: R${stats.get('valor_ganho',0):,.0f} | {meta_empresa_status}
VENDAS FECHADAS NO MÊS: {stats.get('ganhos',0)} | PERDAS NO MÊS: {stats.get('perdidos',0)} | CONVERSÃO: {stats.get('taxa_conversao',0)}%
LEADS RECEBIDOS NO MÊS: {stats.get('total_leads',0)} | TOTAL FECHADOS (ganho+perda): {stats.get('total_fechados',0)}
PIPELINE ATIVO ATUAL: {stats.get('em_andamento',0)} leads em negociação (todos os meses)
  └ SEM FOLLOW-UP: {stats.get('leads_sem_fup',0)} leads sem nenhuma tag/contato registrado — indica abandono
  └ PARADOS +7 DIAS: {stats.get('leads_parados',0)} leads sem atualização há mais de 7 dias — indica negligência
CANAIS: {canal_txt}
ETAPAS COM MAIS PERDA: {stats.get('top_etapas_perda',[])}

VENDEDORES:
{vendor_txt}

Retorne APENAS JSON válido (sem markdown), com exatamente esta estrutura:
{{
  "resultado_geral": {{
    "meta": "R$ {meta_total:,.0f}",
    "resultado": "R$ 0.000",
    "pct_meta": 0,
    "status": "acima_da_meta",
    "vendas": 0,
    "perdas": 0,
    "resumo": "1 frase direta sobre o resultado do mês"
  }},
  "operacao": {{
    "leads_recebidos": 0,
    "sem_follow_up": 0,
    "negociacoes_paradas": 0,
    "observacoes": ["observação concreta 1", "observação 2"]
  }},
  "produto": {{
    "nome": "canal/produto mais vendido",
    "quantidade": 0,
    "taxa_conversao": "0%",
    "observacao": "1 frase sobre o canal mais forte"
  }},
  "equipe": [
    {{
      "nome": "Nome do Vendedor",
      "destaques": ["ponto positivo com dados reais"],
      "problemas": ["problema específico com número", "problema 2"],
      "impacto": "impacto identificado — deixar vazio se não há problemas"
    }}
  ],
  "oportunidades": [
    "oportunidade concreta 1 com dado real",
    "oportunidade 2"
  ],
  "recomendacoes": [
    "ação prática 1 com responsável e prazo",
    "ação 2",
    "ação 3"
  ],
  "conclusao": "Resumo executivo de 2-3 frases: o que aconteceu, principal problema/risco, próximo passo urgente."
}}

REGRAS CRÍTICAS:
- Use APENAS os dados reais fornecidos — proibido inventar ou arredondar números
- Se a empresa NAO BATEU a meta, o status deve ser "abaixo_da_meta" — nunca marcar como acima se resultado < meta
- resultado_geral.resumo: 1 frase direta e honesta — se não bateu meta, dizer quanto faltou
- equipe.destaques: só se houver pontos positivos REAIS com números concretos
- equipe.problemas: obrigatório quando tem sem_fup alto, parados alto, ou não bateu meta individual — use os números exatos
- equipe.impacto: obrigatório quando há problemas — descrever consequência prática
- Se sem_fup > 30% do andamento: problema obrigatório "X leads abandonados sem follow-up"
- Se parados > 20% do andamento: problema obrigatório "X leads parados sem contato há 7+ dias"
- Se nenhum vendedor atualizou o CRM (sem_fup alto + parados alto): explicitar "baixo uso do CRM"
- oportunidades: use números reais de leads parados, abandonados, etapas com perda
- recomendacoes: SEMPRE 3-4 ações práticas com responsável e prazo
- conclusao: SEMPRE — resumo executivo 2-3 frases: resultado vs meta, principal problema, próxima ação urgente
- Português apenas — sem markdown, sem texto fora do JSON"""

    body = json.dumps({
        "model":      ANTHROPIC_MODEL,
        "max_tokens": 3000,
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
            if "```" in text:
                text = re.sub(r"```[a-z]*\n?", "", text).strip()
                text = text.replace("```", "").strip()
            return json.loads(text)
    except urllib.error.HTTPError as e:
        body_err = ""
        try: body_err = e.read().decode("utf-8","replace")[:300]
        except: pass
        print(f"[RAIOX] HTTP {e.code}: {body_err}")
        raise RuntimeError(f"HTTP {e.code}: {body_err}")
    except urllib.error.URLError as e:
        print(f"[RAIOX] URLError: {e.reason}")
        raise RuntimeError(f"URLError: {e.reason}")
    except json.JSONDecodeError as e:
        print(f"[RAIOX] JSON inválido: {e}")
        raise RuntimeError(f"Resposta inválida da API: {e}")
    except Exception as e:
        print(f"[RAIOX] Erro inesperado: {e}")
        raise

def _html_email_raiox(stats, analise, nome_empresa, mes_label):
    """Gera o corpo HTML do e-mail de Raio-X Comercial (7 seções)."""
    rg  = analise.get("resultado_geral", {})
    op  = analise.get("operacao", {})
    pr  = analise.get("produto", {})
    eq  = analise.get("equipe", [])
    ops = analise.get("oportunidades", [])
    rec = analise.get("recomendacoes", [])
    con = analise.get("conclusao", "")
    filtro = stats.get("filtro_vendedor", "")

    status_cor = {"acima_da_meta":"#22c55e","abaixo_da_meta":"#ef4444","na_meta":"#f59e0b"}.get(rg.get("status",""),"#3b82f6")
    status_lbl = {"acima_da_meta":"✅ Acima da Meta","abaixo_da_meta":"❌ Abaixo da Meta","na_meta":"⚡ Na Meta"}.get(rg.get("status",""),"")

    def card(titulo, corpo):
        return (f"<div style='background:#0f1320;border:1px solid #1d2740;border-radius:12px;"
                f"padding:20px 24px;margin-bottom:16px'>"
                f"<div style='font-size:11px;font-weight:700;text-transform:uppercase;"
                f"letter-spacing:.06em;color:#8590a8;margin-bottom:12px'>{titulo}</div>"
                f"{corpo}</div>")

    # 1. Resultado Geral
    c1 = (f"<span style='display:inline-block;padding:4px 14px;border-radius:20px;font-size:12px;"
          f"font-weight:700;background:{status_cor}22;color:{status_cor};border:1px solid {status_cor}55'>{status_lbl}</span>"
          f"<table style='width:100%;margin-top:12px;border-collapse:collapse'><tr>"
          f"<td style='padding:6px 0;color:#8590a8;font-size:12px'>Meta</td>"
          f"<td style='padding:6px 0;color:#eaeef7;font-weight:700;font-size:12px'>{rg.get('meta','—')}</td>"
          f"<td style='padding:6px 0;color:#8590a8;font-size:12px'>Resultado</td>"
          f"<td style='padding:6px 0;color:{status_cor};font-weight:700;font-size:12px'>{rg.get('resultado','—')} ({rg.get('pct_meta',0)}%)</td>"
          f"</tr><tr>"
          f"<td style='padding:6px 0;color:#8590a8;font-size:12px'>Vendas</td>"
          f"<td style='padding:6px 0;color:#4ade80;font-weight:700;font-size:12px'>{rg.get('vendas',0)}</td>"
          f"<td style='padding:6px 0;color:#8590a8;font-size:12px'>Perdas</td>"
          f"<td style='padding:6px 0;color:#f87171;font-weight:700;font-size:12px'>{rg.get('perdas',0)}</td>"
          f"</tr></table>"
          + (f"<p style='color:#c8d0e0;font-size:13px;margin:12px 0 0'>{rg.get('resumo','')}</p>" if rg.get('resumo') else ""))

    # 2. Operação
    obs_html = "".join(f"<li style='margin-bottom:6px;color:#c8d0e0'>{o}</li>" for o in (op.get("observacoes") or []))
    c2 = (f"<table style='width:100%;border-collapse:collapse;margin-bottom:12px'>"
          + "".join(
              f"<tr><td style='padding:5px 0;color:#8590a8;font-size:12px'>{lbl}</td>"
              f"<td style='padding:5px 0;color:#eaeef7;font-weight:700;font-size:12px'>{val}</td></tr>"
              for lbl,val in [
                  ("Leads recebidos", op.get("leads_recebidos","—")),
                  ("Sem follow-up", op.get("sem_follow_up","—")),
                  ("Negociações paradas", op.get("negociacoes_paradas","—")),
              ])
          + f"</table>"
          + (f"<ul style='margin:0;padding-left:16px'>{obs_html}</ul>" if obs_html else ""))

    # 3. Produto
    c3 = (f"<b style='color:#eaeef7;font-size:14px'>{pr.get('nome','—')}</b>"
          f"<span style='background:#3b82f622;color:#93c5fd;border:1px solid #3b82f655;padding:2px 10px;"
          f"border-radius:12px;font-size:11px;margin-left:10px'>{pr.get('quantidade',0)} vendas</span>"
          f"<br><span style='color:#8590a8;font-size:12px'>Conversão: {pr.get('taxa_conversao','—')}</span>"
          + (f"<p style='color:#c8d0e0;font-size:12px;margin:8px 0 0'>{pr.get('observacao','')}</p>" if pr.get('observacao') else ""))

    # 4. Equipe
    eq_html = ""
    for v in eq:
        dest_html = "".join(f"<li style='color:#4ade80;margin-bottom:4px'>✅ {d}</li>" for d in (v.get("destaques") or []))
        prob_html = "".join(f"<li style='color:#fca5a5;margin-bottom:4px'>⚠ {p}</li>" for p in (v.get("problemas") or []))
        eq_html += (f"<div style='border-bottom:1px solid #1d2740;padding-bottom:14px;margin-bottom:14px'>"
                    f"<b style='color:#eaeef7;font-size:13px'>{v.get('nome','')}</b>"
                    + (f"<ul style='margin:8px 0 0;padding-left:16px'>{dest_html}</ul>" if dest_html else "")
                    + (f"<ul style='margin:8px 0 0;padding-left:16px'>{prob_html}</ul>" if prob_html else "")
                    + (("<p style='color:#fbbf24;font-size:12px;margin:8px 0 0;font-style:italic'>Impacto: " + v.get("impacto","") + "</p>") if v.get("impacto") else "")
                    + "</div>")
    c4 = eq_html or "<p style='color:#8590a8;font-size:12px'>Sem dados de equipe.</p>"

    # 5. Oportunidades
    c5 = ("".join(f"<li style='color:#c8d0e0;margin-bottom:6px'>{o}</li>" for o in ops)
          if ops else "<p style='color:#8590a8;font-size:12px'>Sem oportunidades identificadas.</p>")
    c5_wrap = f"<ul style='margin:0;padding-left:16px'>{c5}</ul>" if ops else c5

    # 6. Recomendações
    c6 = ("".join(f"<li style='color:#93c5fd;margin-bottom:6px'><b>{i+1}.</b> {r}</li>" for i,r in enumerate(rec))
          if rec else "<p style='color:#8590a8;font-size:12px'>Sem recomendações.</p>")
    c6_wrap = f"<ol style='margin:0;padding-left:16px'>{c6}</ol>" if rec else c6

    # 7. Conclusão
    c7 = (f"<p style='color:#c8d0e0;font-size:13px;margin:0;line-height:1.7'>{con}</p>"
          if con else "<p style='color:#8590a8;font-size:12px'>Sem conclusão.</p>")

    filtro_badge = (f"<span style='background:#3b82f622;color:#93c5fd;border:1px solid #3b82f655;"
                    f"padding:2px 10px;border-radius:12px;font-size:11px;margin-left:8px'>Vendedor: {filtro}</span>"
                    if filtro else "")

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#07090f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:640px;margin:0 auto;padding:32px 16px">
  <div style="text-align:center;margin-bottom:28px">
    <div style="font-size:22px;font-weight:800;color:#eaeef7">🔍 Raio-X Comercial {filtro_badge}</div>
    <div style="font-size:13px;color:#8590a8;margin-top:4px">{nome_empresa} · {mes_label}</div>
  </div>
  {card("1. Resultado Geral", c1)}
  {card("2. Visão Geral da Operação", c2)}
  {card("3. Produto / Canal Mais Vendido", c3)}
  {card("4. Análise da Equipe", c4)}
  {card("5. Oportunidades Encontradas", c5_wrap)}
  {card("6. Recomendações", c6_wrap)}
  <div style="background:linear-gradient(135deg,rgba(59,130,246,.1),rgba(30,58,138,.15));border:1px solid rgba(59,130,246,.3);border-radius:12px;padding:20px 24px;margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#93c5fd;margin-bottom:10px">7. Conclusão Automática</div>
    {c7}
  </div>
  <div style="text-align:center;margin-top:24px;font-size:11px;color:#5a657d">Raio-X gerado automaticamente pelo Painel CRM · {mes_label}</div>
</div></body></html>"""

def enviar_raiox_email(dest_email, stats, analise, nome_empresa="Painel CRM"):
    """Envia o relatório Raio-X por e-mail."""
    if not GMAIL_USER or not GMAIL_PASS:
        print("[RAIOX EMAIL] GMAIL_USER/GMAIL_PASS não configurados")
        return False
    if not dest_email:
        print("[RAIOX EMAIL] E-mail de destino vazio")
        return False
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    mes_label = stats.get("mes", datetime.now().strftime("%Y-%m"))
    assunto   = f"🔍 Raio-X Comercial — {mes_label} | {nome_empresa}"
    corpo     = _html_email_raiox(stats, analise, nome_empresa, mes_label)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = GMAIL_USER
    msg["To"]      = dest_email
    msg.attach(MIMEText(corpo, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_USER, GMAIL_PASS)
            srv.sendmail(GMAIL_USER, dest_email, msg.as_string())
        print(f"[RAIOX EMAIL] Enviado para {dest_email}")
        return True
    except Exception as e:
        print(f"[RAIOX EMAIL] Erro: {e}")
        return False

def auto_raiox_mensal():
    """Thread que envia o Raio-X todo dia 31 às 08:00h.
    Em meses sem dia 31, envia no dia 28 (ex: fevereiro, abril, junho...).
    """
    _enviado_raiox = None  # controle: (ano, mes) do último envio automático
    while True:
        agora = datetime.now()
        # Calcula próxima janela de verificação: 08:00 do próximo dia
        proximo = agora.replace(hour=8, minute=0, second=0, microsecond=0)
        if agora.hour >= 8:
            proximo += timedelta(days=1)
        time.sleep(max(0, (proximo - datetime.now()).total_seconds()))

        agora = datetime.now()
        # Dia de disparo: 31 se o mês tiver, senão 28
        dias_no_mes  = calendar.monthrange(agora.year, agora.month)[1]
        dia_disparo  = 31 if dias_no_mes >= 31 else 28
        if agora.day != dia_disparo:
            continue
        chave_mes = (agora.year, agora.month)
        if _enviado_raiox == chave_mes:
            continue  # Já enviou neste mês
        _enviado_raiox = chave_mes

        mes_chave = agora.strftime("%Y-%m")
        print(f"[RAIOX MENSAL] Último dia do mês {mes_chave} — gerando relatórios…")

        users = load_users()
        for uname, ud in list(users.items()):
            if not ud.get("ativo", True):
                continue
            # Verifica se já enviou este mês
            if ud.get("raiox_ultimo_envio") == mes_chave:
                continue
            email_dest = ud.get("raiox_email") or ud.get("email", "")
            if not email_dest:
                continue
            # Obtém dados do cache
            cache, lock = get_cache(uname)
            with lock:
                dados_cache = cache.get("dados") or {}
            if not dados_cache.get("leads"):
                continue
            try:
                meta_val  = dados_cache.get("meta_mes") or CFG["meta_mes_padrao"]
                stats     = compute_raiox_stats(dados_cache, mes_chave, meta=meta_val)
                analise   = claude_raiox(stats)
                if not analise:
                    continue
                nome_emp  = ud.get("nome", CFG["nome_empresa"])
                ok        = enviar_raiox_email(email_dest, stats, analise, nome_emp)
                if ok:
                    users[uname]["raiox_ultimo_envio"] = mes_chave
                    save_users(users)
                    print(f"[RAIOX MENSAL] Enviado para {uname} ({email_dest})")
            except Exception as e:
                print(f"[RAIOX MENSAL] Erro para {uname}: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  SISTEMA DE ALERTAS POR EMAIL
# ═══════════════════════════════════════════════════════════════════════
_alertas_enviados = {}
_alertas_lock     = threading.Lock()
INTERVALO_ALERTA_H = 6   # nao reenvia o mesmo alerta por 6 horas

# Log de emails enviados (últimos 300) — persistido em arquivo
_email_log      = []
_email_log_lock = threading.Lock()
MAX_EMAIL_LOG   = 300

def _load_email_log():
    """Carrega histórico de emails do disco ao iniciar."""
    global _email_log
    try:
        if EMAIL_LOG_FILE.exists():
            data = json.loads(EMAIL_LOG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _email_log = data[:MAX_EMAIL_LOG]
    except Exception as e:
        print(f"[EMAIL_LOG] Erro ao carregar: {e}")

def _save_email_log():
    """Salva log de emails no disco (chamado após cada registro)."""
    try:
        EMAIL_LOG_FILE.write_text(
            json.dumps(_email_log[:MAX_EMAIL_LOG], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[EMAIL_LOG] Erro ao salvar: {e}")

def _registrar_email_log(tipo, destinatario, assunto, preview_html, status, erro=""):
    entrada = {
        "ts":           br_now().strftime("%d/%m/%Y %H:%M"),
        "tipo":         tipo,
        "destinatario": destinatario,
        "assunto":      assunto,
        "preview":      preview_html,
        "status":       status,   # "ok" | "erro" | "sem_config"
        "erro":         erro,
    }
    with _email_log_lock:
        _email_log.insert(0, entrada)
        if len(_email_log) > MAX_EMAIL_LOG:
            _email_log.pop()
        _save_email_log()

def _pode_alertar(chave):
    agora = time.time()
    with _alertas_lock:
        ultimo = _alertas_enviados.get(chave, 0)
        if agora - ultimo >= INTERVALO_ALERTA_H * 3600:
            _alertas_enviados[chave] = agora
            return True
    return False

def _enviar_alerta_email(assunto, corpo_html, tipo="alerta", dest_email=None):
    """Envia alerta/relatório por email.
    dest_email: endereço do cliente específico; se None usa ALERT_EMAIL ou GMAIL_USER (admin).
    """
    dest = dest_email or ALERT_EMAIL or GMAIL_USER
    if not GMAIL_USER or not GMAIL_PASS or not dest:
        print(f"[ALERTA] Config de email incompleta — pulando envio")
        _registrar_email_log(tipo, dest or "(não configurado)", assunto, corpo_html, "sem_config")
        return False
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = GMAIL_USER
    msg["To"]      = dest
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_USER, GMAIL_PASS)
            srv.sendmail(GMAIL_USER, dest, msg.as_string())
        print(f"[ALERTA] Enviado para {dest}: {assunto}")
        _registrar_email_log(tipo, dest, assunto, corpo_html, "ok")
        return True
    except Exception as e:
        print(f"[ALERTA] Erro ao enviar: {e}")
        _registrar_email_log(tipo, dest, assunto, corpo_html, "erro", str(e))
        return False

def _html_alerta(titulo, itens, cor="#f59e0b"):
    linhas = "".join(f"<li style='padding:4px 0;font-size:14px'>{i}</li>" for i in itens)
    empresa = CFG.get("nome_empresa", "Painel CRM")
    hora    = br_now().strftime("%d/%m/%Y %H:%M")
    return (
        "<div style='font-family:Arial,sans-serif;max-width:560px;margin:0 auto;"
        "background:#0f172a;color:#e2e8f0;border-radius:12px;overflow:hidden'>"
        f"<div style='background:{cor};padding:16px 24px'>"
        f"<h2 style='margin:0;font-size:18px;color:#fff'>{titulo}</h2>"
        f"<p style='margin:4px 0 0;font-size:12px;color:rgba(255,255,255,.8)'>{empresa} &mdash; {hora}</p>"
        "</div>"
        "<div style='padding:20px 24px'>"
        f"<ul style='margin:0;padding-left:18px;color:#cbd5e1'>{linhas}</ul>"
        "<p style='margin:16px 0 0;font-size:11px;color:#64748b'>Acesse o painel para ver os detalhes.</p>"
        "</div></div>"
    )

def checar_alertas_email():  # utilitário — não chamado automaticamente, pode ser chamado manualmente
    agora = time.time()
    try:
        with _caches_init_lock:
            usuarios = list(_caches.keys())
        users_data = load_users()
        for uname in usuarios:
            cache, lock = get_cache(uname)
            with lock:
                dados  = cache.get("dados")
                status = cache.get("status")
            if not dados or status != "ok":
                continue
            # Email do cliente para receber os alertas
            ud         = users_data.get(uname, {})
            dest_email = ud.get("raiox_email") or ud.get("email") or ALERT_EMAIL or GMAIL_USER
            leads      = dados.get("leads", [])
            status_map = dados.get("status_map", {})
            prefixo    = f"{uname}_" if uname != "__admin__" else ""
            label      = f" [{uname}]" if uname != "__admin__" else ""
            alertas    = []

            # 1. Leads parados ha +24h
            parados = [l for l in leads
                       if status_map.get(l.get("etapa"), {}).get("tipo") == "andamento"
                       and (agora - (l.get("modificado") or l.get("data") or 0)) > 86400]
            if parados:
                por_vendedor = {}
                for l in parados:
                    v = l.get("vendedor", "Sem vendedor")
                    por_vendedor[v] = por_vendedor.get(v, 0) + 1
                chave = f"{prefixo}parados"
                if _pode_alertar(chave):
                    itens = [f"<b>{tot} lead(s)</b> parado(s) com <b>{v}</b>"
                             for v, tot in sorted(por_vendedor.items(), key=lambda x: -x[1])]
                    alertas.append((f"⏸ {len(parados)} Lead(s) Parado(s) ha +24h{label}", itens, "#f59e0b"))

            # 2. Vendedores sem atividade hoje
            hoje_ini = br_now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            ativos   = {l.get("vendedor") for l in leads if (l.get("modificado") or 0) >= hoje_ini}
            todos    = {l.get("vendedor") for l in leads if l.get("vendedor") and l.get("vendedor") != "Nao atribuido"}
            inativos = sorted(todos - ativos)
            if inativos:
                chave = f"{prefixo}inativos_{br_now().strftime('%Y%m%d')}"
                if _pode_alertar(chave):
                    itens = [f"<b>{v}</b> sem atividade hoje" for v in inativos]
                    alertas.append((f"\U0001f464 Vendedor(es) sem atividade hoje{label}", itens, "#3b82f6"))

            # 3. Meta em risco (>40% do mes, <30% da meta)
            meta = CFG.get("meta_mes_padrao", 0)
            if meta > 0:
                hoje    = br_now()
                try:
                    prox    = datetime(hoje.year + (1 if hoje.month == 12 else 0), hoje.month % 12 + 1, 1)
                    dias_mes = (prox - timedelta(days=1)).day
                except Exception:
                    dias_mes = 30
                pct_mes  = hoje.day / dias_mes
                ganhos   = sum(1 for l in leads
                               if status_map.get(l.get("etapa"), {}).get("tipo") == "ganho"
                               and datetime.fromtimestamp(l.get("fechado_em") or l.get("data") or 0).month == hoje.month
                               and datetime.fromtimestamp(l.get("fechado_em") or l.get("data") or 0).year  == hoje.year)
                pct_meta = ganhos / meta
                if pct_mes > 0.4 and pct_meta < 0.3:
                    chave = f"{prefixo}meta_{hoje.year}_{hoje.month}"
                    if _pode_alertar(chave):
                        itens = [
                            f"<b>{ganhos}</b> vendas de <b>{meta}</b> ({round(pct_meta*100)}% da meta)",
                            f"Estamos em <b>{round(pct_mes*100)}%</b> do mes",
                            "Ritmo atual insuficiente para bater a meta"
                        ]
                        alertas.append((f"\U0001f3af Meta em Risco{label}", itens, "#ef4444"))

            for assunto, itens, cor in alertas:
                _enviar_alerta_email(assunto, _html_alerta(assunto, itens, cor), dest_email=dest_email)

    except Exception as e:
        print(f"[ALERTA] Erro em checar_alertas_email: {e}")


def _html_relatorio(titulo, subtitulo, secoes, cor_topo="#1e3a8a"):
    """Gera HTML bonito para os relatórios diários."""
    empresa = CFG.get("nome_empresa", "Painel CRM")
    hora    = br_now().strftime("%d/%m/%Y %H:%M")
    html    = (
        "<div style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;"
        "background:#0f172a;color:#e2e8f0;border-radius:12px;overflow:hidden'>"
        f"<div style='background:{cor_topo};padding:20px 24px'>"
        f"<h1 style='margin:0;font-size:20px;color:#fff'>{titulo}</h1>"
        f"<p style='margin:6px 0 0;font-size:13px;color:rgba(255,255,255,.75)'>{subtitulo}</p>"
        f"<p style='margin:4px 0 0;font-size:11px;color:rgba(255,255,255,.5)'>{empresa} &mdash; {hora}</p>"
        "</div>"
    )
    for sec_titulo, linhas in secoes:
        html += (
            f"<div style='padding:16px 24px;border-bottom:1px solid #1e293b'>"
            f"<h3 style='margin:0 0 10px;font-size:13px;text-transform:uppercase;"
            f"letter-spacing:.5px;color:#94a3b8'>{sec_titulo}</h3>"
            "<table style='width:100%;border-collapse:collapse'>"
        )
        for linha in linhas:
            if isinstance(linha, tuple) and len(linha) == 2:
                label, valor = linha
                html += (
                    f"<tr><td style='padding:5px 0;font-size:13px;color:#cbd5e1'>{label}</td>"
                    f"<td style='padding:5px 0;font-size:13px;font-weight:700;color:#f1f5f9;"
                    f"text-align:right'>{valor}</td></tr>"
                )
            else:
                html += f"<tr><td colspan='2' style='padding:4px 0;font-size:13px;color:#94a3b8'>{linha}</td></tr>"
        html += "</table></div>"
    html += "<div style='padding:14px 24px;font-size:11px;color:#475569'>Acesse o painel para mais detalhes.</div></div>"
    return html

def _stats_periodo(leads, status_map, inicio_ts, fim_ts):
    """Calcula estatísticas de leads para um período."""
    do_periodo = [l for l in leads if inicio_ts <= (l.get("data") or 0) < fim_ts]
    fechados   = [l for l in leads
                  if status_map.get(l.get("etapa"), {}).get("tipo") == "ganho"
                  and inicio_ts <= (l.get("fechado_em") or l.get("data") or 0) < fim_ts]
    perdidos   = [l for l in leads
                  if status_map.get(l.get("etapa"), {}).get("tipo") == "perdido"
                  and inicio_ts <= (l.get("modificado") or l.get("data") or 0) < fim_ts]
    movidos    = [l for l in leads
                  if inicio_ts <= (l.get("modificado") or l.get("data") or 0) < fim_ts]

    # Por vendedor (vendas fechadas)
    por_vendedor = {}
    for l in fechados:
        v = l.get("vendedor", "Sem vendedor")
        por_vendedor[v] = por_vendedor.get(v, 0) + 1

    # Por canal (novos leads)
    por_canal = {}
    for l in do_periodo:
        c = l.get("canal", "Outros")
        por_canal[c] = por_canal.get(c, 0) + 1

    # Atividade por vendedor — quantos leads cada um tocou no período
    atividade = {}
    todos_vendedores = set()
    for l in leads:
        v = l.get("vendedor")
        if v and v != "Nao atribuido":
            todos_vendedores.add(v)
    for l in movidos:
        v = l.get("vendedor")
        if v and v != "Nao atribuido":
            atividade[v] = atividade.get(v, 0) + 1

    # Classifica cada vendedor
    sem_atividade   = sorted(v for v in todos_vendedores if v not in atividade)
    pouca_atividade = sorted(v for v in atividade if atividade[v] <= 3)   # 1-3 leads tocados = pouco
    boa_atividade   = sorted(v for v in atividade if atividade[v] >  3)   # 4+ = ok

    return {
        "novos":            len(do_periodo),
        "vendas":           len(fechados),
        "perdidos":         len(perdidos),
        "movidos":          len(movidos),
        "por_vendedor":     por_vendedor,
        "por_canal":        por_canal,
        "atividade":        atividade,          # {vendedor: qtd leads tocados}
        "sem_atividade":    sem_atividade,       # não usaram o CRM
        "pouca_atividade":  pouca_atividade,     # tocaram em 1-3 leads apenas
        "boa_atividade":    boa_atividade,        # 4+ leads
        "todos_vendedores": sorted(todos_vendedores),
    }

def enviar_relatorio_manha(only_user=None):
    """Relatório das 6h — o que aconteceu ontem.
    only_user: se informado, envia apenas para aquele usuário específico.
    """
    try:
        with _caches_init_lock:
            usuarios = list(_caches.keys())
        if only_user:
            usuarios = [u for u in usuarios if u == only_user]

        users_data = load_users()

        for uname in usuarios:
            cache, lock = get_cache(uname)
            with lock:
                dados  = cache.get("dados")
                status = cache.get("status")
            if not dados or status != "ok":
                continue

            # Determina email do cliente; admin recebe no ALERT_EMAIL
            ud         = users_data.get(uname, {})
            dest_email = ud.get("raiox_email") or ud.get("email") or ALERT_EMAIL or GMAIL_USER

            leads      = dados.get("leads", [])
            status_map = dados.get("status_map", {})
            label      = f" [{uname}]" if uname != "__admin__" else ""

            ontem       = br_now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            inicio_ts   = ontem.timestamp()
            fim_ts      = inicio_ts + 86400
            s           = _stats_periodo(leads, status_map, inicio_ts, fim_ts)
            data_label  = ontem.strftime("%A, %d/%m").capitalize()

            secoes = []

            # Resumo geral
            secoes.append(("📊 Resumo de Ontem", [
                ("Novos leads recebidos", str(s["novos"])),
                ("Vendas fechadas",       f"✅ {s['vendas']}"),
                ("Perdidos",              f"❌ {s['perdidos']}"),
                ("Leads movimentados",    str(s["movidos"])),
            ]))

            # Por vendedor
            if s["por_vendedor"]:
                linhas_v = sorted(s["por_vendedor"].items(), key=lambda x: -x[1])
                secoes.append(("🏆 Vendas por Vendedor", [(v, f"{n} venda(s)") for v, n in linhas_v]))
            else:
                secoes.append(("🏆 Vendas por Vendedor", [("Nenhuma venda registrada ontem", "")]))

            # Por canal
            if s["por_canal"]:
                linhas_c = sorted(s["por_canal"].items(), key=lambda x: -x[1])
                secoes.append(("📡 Novos Leads por Canal", [(c, f"{n} lead(s)") for c, n in linhas_c]))

            # Leads parados (acumulado)
            agora   = time.time()
            parados = [l for l in leads
                       if status_map.get(l.get("etapa"), {}).get("tipo") == "andamento"
                       and (agora - (l.get("modificado") or l.get("data") or 0)) > 86400]
            secoes.append(("⚠️ Atenção Hoje", [
                (f"Leads parados há +24h", f"⏸ {len(parados)}"),
            ]))

            # Atividade dos vendedores ontem
            linhas_at = []
            for v in s["sem_atividade"]:
                linhas_at.append((v, "🔴 Não usou o CRM"))
            for v in s["pouca_atividade"]:
                n = s["atividade"].get(v, 0)
                linhas_at.append((v, f"🟡 Só {n} lead(s) tocado(s)"))
            for v in s["boa_atividade"]:
                n = s["atividade"].get(v, 0)
                linhas_at.append((v, f"✅ {n} leads movimentados"))
            if linhas_at:
                secoes.append(("👥 Atividade dos Vendedores Ontem", linhas_at))
            elif s["todos_vendedores"]:
                secoes.append(("👥 Atividade dos Vendedores Ontem", [("Nenhum vendedor ativo ontem", "—")]))

            html    = _html_relatorio(
                f"☀️ Bom dia! Resumo de {data_label}{label}",
                "Veja o que aconteceu ontem no seu CRM.",
                secoes, cor_topo="#1e40af"
            )
            assunto = f"☀️ Resumo de ontem — {data_label}{label}"
            _enviar_alerta_email(assunto, html, tipo="manha", dest_email=dest_email)

    except Exception as e:
        print(f"[RELATORIO MANHA] Erro: {e}")

def enviar_relatorio_noite(only_user=None):
    """Relatório das 19h — resumo da semana atual (seg a hoje).
    only_user: se informado, envia apenas para aquele usuário específico.
    """
    try:
        with _caches_init_lock:
            usuarios = list(_caches.keys())
        if only_user:
            usuarios = [u for u in usuarios if u == only_user]

        users_data = load_users()

        for uname in usuarios:
            cache, lock = get_cache(uname)
            with lock:
                dados  = cache.get("dados")
                status = cache.get("status")
            if not dados or status != "ok":
                continue

            # Determina email do cliente; admin recebe no ALERT_EMAIL
            ud         = users_data.get(uname, {})
            dest_email = ud.get("raiox_email") or ud.get("email") or ALERT_EMAIL or GMAIL_USER

            leads      = dados.get("leads", [])
            status_map = dados.get("status_map", {})
            label      = f" [{uname}]" if uname != "__admin__" else ""

            hoje       = br_now().replace(hour=0, minute=0, second=0, microsecond=0)
            # Segunda-feira da semana atual
            seg        = hoje - timedelta(days=hoje.weekday())
            inicio_ts  = seg.timestamp()
            fim_ts     = time.time()
            s          = _stats_periodo(leads, status_map, inicio_ts, fim_ts)

            data_seg   = seg.strftime("%d/%m")
            data_hoje  = hoje.strftime("%d/%m")
            periodo    = f"{data_seg} a {data_hoje}" if seg != hoje else data_hoje

            # Meta do mês
            meta       = CFG.get("meta_mes_padrao", 0)
            try:
                prox     = datetime(hoje.year + (1 if hoje.month == 12 else 0), hoje.month % 12 + 1, 1)
                dias_mes = (prox - timedelta(days=1)).day
            except Exception:
                dias_mes = 30
            pct_mes    = hoje.day / dias_mes
            ganhos_mes = sum(1 for l in leads
                             if status_map.get(l.get("etapa"), {}).get("tipo") == "ganho"
                             and datetime.fromtimestamp(l.get("fechado_em") or l.get("data") or 0).month == hoje.month
                             and datetime.fromtimestamp(l.get("fechado_em") or l.get("data") or 0).year  == hoje.year)
            pct_meta   = round(ganhos_mes / meta * 100) if meta > 0 else 0

            # Dias úteis da semana trabalhados até agora
            dias_semana = hoje.weekday() + 1  # 1=seg, 2=ter ... 5=sex+

            secoes = []

            # Resumo da semana
            secoes.append((f"📊 Semana ({periodo})", [
                ("Novos leads recebidos",  str(s["novos"])),
                ("Vendas fechadas",        f"✅ {s['vendas']}"),
                ("Perdidos",               f"❌ {s['perdidos']}"),
                ("Leads movimentados",     str(s["movidos"])),
                ("Dias trabalhados",       f"{min(dias_semana, 5)} dia(s)"),
                ("Média vendas/dia",       f"{round(s['vendas']/max(dias_semana,1), 1)}"),
            ]))

            # Ranking de vendas da semana por vendedor
            if s["por_vendedor"]:
                linhas_v = sorted(s["por_vendedor"].items(), key=lambda x: -x[1])
                secoes.append(("🏆 Ranking da Semana", [(v, f"{n} venda(s)") for v, n in linhas_v]))
            else:
                secoes.append(("🏆 Ranking da Semana", [("Nenhuma venda esta semana", "—")]))

            # Meta do mês
            if meta > 0:
                emoji_meta = "✅" if pct_meta >= 100 else ("⚡" if pct_meta >= 70 else ("⚠️" if pct_meta >= 40 else "🔴"))
                faltam     = max(0, meta - ganhos_mes)
                dias_rest  = dias_mes - hoje.day
                ritmo      = round(faltam / max(dias_rest, 1), 1)
                secoes.append(("🎯 Meta do Mês", [
                    ("Vendas no mês",         f"{ganhos_mes} de {meta}"),
                    ("% atingida",            f"{emoji_meta} {pct_meta}%"),
                    ("Faltam",                f"{faltam} vendas em {dias_rest} dias"),
                    ("Ritmo necessário",      f"{ritmo} vendas/dia para bater"),
                ]))

            # Canais da semana
            if s["por_canal"]:
                linhas_c = sorted(s["por_canal"].items(), key=lambda x: -x[1])
                secoes.append(("📡 Leads por Canal (semana)", [(c, f"{n}") for c, n in linhas_c]))

            # Atividade dos vendedores na semana
            linhas_at = []
            for v in s["sem_atividade"]:
                linhas_at.append((v, "🔴 Não usou o CRM esta semana"))
            for v in s["pouca_atividade"]:
                n = s["atividade"].get(v, 0)
                linhas_at.append((v, f"🟡 Só {n} lead(s) — pouco follow-up"))
            for v in s["boa_atividade"]:
                n = s["atividade"].get(v, 0)
                linhas_at.append((v, f"✅ {n} leads na semana"))
            if linhas_at:
                secoes.append(("👥 Atividade da Equipe na Semana", linhas_at))

            # Leads parados acumulados
            agora_ts = time.time()
            parados  = [l for l in leads
                        if status_map.get(l.get("etapa"), {}).get("tipo") == "andamento"
                        and (agora_ts - (l.get("modificado") or l.get("data") or 0)) > 86400]
            if parados:
                por_v = {}
                for l in parados:
                    v = l.get("vendedor", "Sem vendedor")
                    por_v[v] = por_v.get(v, 0) + 1
                linhas_p = [(v, f"⏸ {n} lead(s)") for v, n in sorted(por_v.items(), key=lambda x: -x[1])]
                secoes.append((f"⏸ Leads Parados há +24h ({len(parados)} total)", linhas_p))

            html    = _html_relatorio(
                f"📅 Relatório Semanal{label}",
                f"Resumo da semana: {periodo}.",
                secoes, cor_topo="#0f172a"
            )
            assunto = f"📅 Relatório da semana {periodo}{label}"
            _enviar_alerta_email(assunto, html, tipo="semana", dest_email=dest_email)

    except Exception as e:
        print(f"[RELATORIO NOITE] Erro: {e}")

def auto_alertas_email():
    """Thread de emails automáticos: apenas 06:00h (relatório de ontem) e 19:00h (semana)."""
    time.sleep(120)  # aguarda 2 min no boot
    _enviado_manha = None
    _enviado_noite = None
    while True:
        agora = br_now()
        hoje  = agora.date()

        # Relatório da manhã — 6:00h
        if agora.hour == 6 and _enviado_manha != hoje:
            print(f"[RELATORIO] Enviando relatório da manhã...")
            enviar_relatorio_manha()
            _enviado_manha = hoje

        # Relatório da noite — 19:00h
        if agora.hour == 19 and _enviado_noite != hoje:
            print(f"[RELATORIO] Enviando relatório da semana...")
            enviar_relatorio_noite()
            _enviado_noite = hoje

        time.sleep(60)  # verifica a cada minuto



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
# ═══════════════  PAINEL NEGÓCIOS LOCAIS  |  painel_servidor_v2.html  ═════

PAINEL_HTML       = BASE_DIR / "painel_servidor_v2.html"
_painel_html_cache = None
_painel_html_lock  = threading.Lock()

def get_painel_html_base():
    """Lê o HTML do disco sempre (sem cache) para pegar mudanças após deploy."""
    global _painel_html_cache
    with _painel_html_lock:
        if not PAINEL_HTML.exists():
            return _painel_html_cache  # fallback para cache se arquivo sumiu
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
        "  var ls=document.getElementById('loadingScreen');\n"
        "  var le=document.getElementById('loadErr');\n"
        "  if(ls)ls.style.display='none';\n"
        "  if(le)le.style.display='none';\n"
        "  try{\n"
        "    window.RAW_DATA=dados.leads||[];\n"
        "    window.STATUS_MAP=dados.status_map||{};\n"
        "    window.FUNIS=dados.funis||[];\n"
        "    if(typeof preComputeLeads==='function')preComputeLeads();\n"
        "    if(typeof renderFunilBar==='function')renderFunilBar();\n"
        "    var range=window.currentRange||'tudo';\n"
        "    if(typeof render==='function')render(range);\n"
        "    if(typeof setPill==='function')setPill('live',window._PILL_TXT);\n"
        "    window._crm_loaded=true;\n"
        "  }catch(e){\n"
        "    console.error('[CRM render]',e);\n"
        "    if(le){le.style.display='block';le.innerHTML='Erro ao renderizar: '+e.message;}\n"
        "  }\n"
        "}\n"
        "window._crm_retries=0;\n"
        "function _setLoadTxt(t){var el=document.getElementById('loadTxt');if(el)el.textContent=t;}\n"
        "function _crm_fetch(){\n"
        "  var ctrl=new AbortController();\n"
        "  var tid=setTimeout(function(){ctrl.abort();},20000);\n"
        "  fetch(window._API_URL,{signal:ctrl.signal,credentials:'include'})\n"
        "    .then(function(r){clearTimeout(tid);return r.json();})\n"
        "    .then(function(j){\n"
        "      window._crm_retries++;\n"
        "      if(j&&j.dados){_crm_render(j.dados);}\n"
        "      else if(j&&j.erro){\n"
        "        var ls=document.getElementById('loadingScreen');if(ls)ls.style.display='none';\n"
        "        var le=document.getElementById('loadErr');\n"
        "        if(le){le.style.display='block';le.innerHTML='Erro: '+j.erro+'<br><br><button onclick=\\'location.reload()\\' style=\\'background:#1e3a8a;color:#fff;border:none;padding:8px 18px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:700\\'>&#8635; Recarregar</button>';}\n"
        "        setTimeout(_crm_fetch,15000);\n"
        "      }else{\n"
        "        _setLoadTxt('Buscando dados… (tentativa '+window._crm_retries+')');\n"
        "        if(window._crm_retries>=8){\n"
        "          var ls2=document.getElementById('loadingScreen');if(ls2)ls2.style.display='none';\n"
        "          var le2=document.getElementById('loadErr');\n"
        "          if(le2){le2.style.display='block';le2.innerHTML='Servidor demorando para responder.<br><br><button onclick=\\'location.reload()\\' style=\\'background:#1e3a8a;color:#fff;border:none;padding:8px 18px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:700\\'>&#8635; Tentar novamente</button>';}\n"
        "        }else{setTimeout(_crm_fetch,4000);}\n"
        "      }\n"
        "    })\n"
        "    .catch(function(e){\n"
        "      clearTimeout(tid);\n"
        "      window._crm_retries++;\n"
        "      if(window._crm_retries>=5){\n"
        "        var ls=document.getElementById('loadingScreen');if(ls)ls.style.display='none';\n"
        "        var le=document.getElementById('loadErr');\n"
        "        if(le){le.style.display='block';le.innerHTML='Sem conexao com o servidor.<br><br><button onclick=\\'location.reload()\\' style=\\'background:#1e3a8a;color:#fff;border:none;padding:8px 18px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:700\\'>&#8635; Tentar novamente</button>';}\n"
        "      }else{_setLoadTxt('Reconectando… ('+window._crm_retries+'/5)');setTimeout(_crm_fetch,5000);}\n"
        "    });\n"
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
        "  fetch('/api/refresh',{credentials:'include'}).then(function(){setTimeout(_crm_fetch,800);}).catch(function(e){console.warn('[refresh]',e);});\n"
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
    html = html.replace(",currentRange=", ";var currentRange=", 1)
    html = html.replace("let FUNIL_SEL=", "var FUNIL_SEL=", 1)
    html = html.replace("let STATUS_MAP=", "var STATUS_MAP=", 1)
    html = html.replace("let FUNIS=", "var FUNIS=", 1)
    html = html.replace("let selectedMonthKey=", "var selectedMonthKey=", 1)
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

# ═══════════════════  PAINEL CORRETOR  |  HTML: painel_corretor.html  ════════
def _load_corretor_html():
    """Carrega painel_corretor.html do disco. Usa versão embutida como fallback."""
    from pathlib import Path
    p = Path(__file__).parent / "painel_corretor.html"
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return _CORRETOR_HTML_INLINE

_CORRETOR_HTML_INLINE = """<!DOCTYPE html>
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__CNOME__ &mdash; Painel Corretor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#07090f;color:#eaeef7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}
.header{background:linear-gradient(135deg,__CCOR__,__CCOR2__);padding:18px 20px;display:flex;align-items:center;justify-content:space-between}
.header-title{font-size:18px;font-weight:700}
.header-sub{font-size:12px;opacity:.8;margin-top:2px}
.tab-bar{display:flex;background:#0d1117;border-bottom:1px solid #1d2740;overflow-x:auto}
.tab-btn{flex-shrink:0;background:transparent;border:none;border-bottom:2px solid transparent;color:#64748b;font-size:12px;font-weight:600;padding:12px 14px;cursor:pointer;font-family:inherit;transition:all .15s;white-space:nowrap}
.tab-btn.active{color:__CCOR__;border-bottom-color:__CCOR__}
.tab-btn:hover:not(.active){color:#eaeef7}
.tab-content{display:none;padding:16px;max-width:700px;margin:0 auto}
.tab-content.active{display:block}
.card{background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:18px 20px;margin-bottom:14px}
.card-title{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:12px}
.metric{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1a2236}
.metric:last-child{border-bottom:none}
.metric-label{font-size:13px;color:#8590a8}
.metric-value{font-size:15px;font-weight:700}
.green{color:#22c55e} .yellow{color:#f59e0b} .red{color:#ef4444} .blue{color:#3b82f6}
.stat-big{text-align:center;padding:14px 0}
.stat-big .num{font-size:40px;font-weight:800;background:linear-gradient(135deg,__CCOR__,__CCOR2__);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat-big .lbl{font-size:12px;color:#64748b;margin-top:4px}
/* Pipeline */
.pipeline-item{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #1a2236}
.pipeline-item:last-child{border-bottom:none}
.pipeline-dot{width:10px;height:10px;border-radius:50%;background:__CCOR__;flex-shrink:0}
.pipeline-name{font-size:13px;color:#eaeef7;flex:1}
.pipeline-count{font-size:12px;font-weight:700;color:#8590a8}
/* Meta animada */
.meta-wrap{margin-top:12px}
.meta-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.meta-valor{font-size:28px;font-weight:800;color:#22c55e}
.meta-falta{font-size:12px;color:#64748b}
.meta-bar{height:12px;background:#1a2236;border-radius:6px;overflow:hidden;margin-bottom:6px;position:relative}
.meta-fill{height:100%;border-radius:6px;background:linear-gradient(90deg,__CCOR__,__CCOR2__);transition:width 1.2s cubic-bezier(.22,1,.36,1);position:relative}
.meta-fill::after{content:'';position:absolute;right:0;top:0;bottom:0;width:3px;background:rgba(255,255,255,.5);border-radius:3px}
.meta-pct-badge{display:inline-block;background:linear-gradient(135deg,__CCOR__,__CCOR2__);color:#fff;font-size:12px;font-weight:800;padding:3px 10px;border-radius:20px;margin-right:6px}
.meta-labels{display:flex;justify-content:space-between;font-size:11px;color:#64748b;margin-top:4px}
/* Follow-up alerts */
.followup-item{display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-bottom:1px solid #1a2236;cursor:pointer}
.followup-item:last-child{border-bottom:none}
.followup-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex-shrink:0}
.followup-name{font-size:13px;font-weight:600;color:#eaeef7}
.followup-dias{font-size:11px;color:#64748b;margin-top:2px}
.followup-badge{font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;flex-shrink:0}
.badge-red{background:rgba(239,68,68,.15);color:#ef4444;border:1px solid rgba(239,68,68,.3)}
.badge-yellow{background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.3)}
/* Agenda */
.agenda-item{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #1a2236;align-items:flex-start}
.agenda-item:last-child{border-bottom:none}
.agenda-hora{font-size:13px;font-weight:700;color:__CCOR__;min-width:44px;flex-shrink:0;padding-top:2px}
.agenda-body{flex:1}
.agenda-nome{font-size:13px;font-weight:600;color:#eaeef7}
.agenda-end{font-size:11px;color:#8590a8;margin-top:2px}
.agenda-del{background:transparent;border:none;color:#4a5568;cursor:pointer;font-size:14px;padding:2px 6px;border-radius:4px;flex-shrink:0}
.agenda-del:hover{color:#ef4444}
.agenda-form{display:flex;flex-direction:column;gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid #1d2740}
.agenda-form input,.agenda-form textarea{background:#131a2b;border:1px solid #1d2740;color:#eaeef7;padding:8px 10px;border-radius:7px;font-size:12.5px;font-family:inherit;outline:none}
.agenda-form input:focus,.agenda-form textarea:focus{border-color:__CCOR__}
.agenda-add-btn{background:__CCOR__;border:none;color:#fff;font-size:12.5px;font-weight:700;padding:9px;border-radius:8px;cursor:pointer;font-family:inherit;transition:opacity .15s}
.agenda-add-btn:hover{opacity:.85}
/* Histórico */
.hist-bar-wrap{margin-bottom:12px}
.hist-label{display:flex;justify-content:space-between;font-size:12px;color:#8590a8;margin-bottom:4px}
.hist-bar{height:10px;background:#1a2236;border-radius:5px;overflow:hidden}
.hist-fill{height:100%;border-radius:5px;background:linear-gradient(90deg,__CCOR__,__CCOR2__);transition:width .8s}
.logout-btn{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);color:#fff;font-size:12px;font-weight:600;padding:7px 14px;border-radius:7px;cursor:pointer;font-family:inherit;text-decoration:none}
.quente-card{background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.25);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.quente-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px}
.quente-nome{font-size:13px;font-weight:700;color:#eaeef7;flex:1}
.quente-etapa{font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.3);flex-shrink:0;margin-left:8px}
.quente-dias{font-size:12px;font-weight:600;margin-bottom:8px}
.quente-funil{font-size:11px;color:#64748b;margin-bottom:8px}
.wa-btn{display:inline-flex;align-items:center;gap:6px;background:#25d366;border:none;color:#fff;font-size:12px;font-weight:700;padding:7px 14px;border-radius:8px;cursor:pointer;font-family:inherit;text-decoration:none;transition:opacity .15s}
.wa-btn:hover{opacity:.85}
.obj-item{border:1px solid #1d2740;border-radius:10px;margin-bottom:10px;overflow:hidden}
.obj-header{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;cursor:pointer;background:#0f1320}
.obj-header:hover{background:#131a2b}
.obj-titulo{font-size:13px;font-weight:700;color:#eaeef7;flex:1}
.obj-tag{font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(239,68,68,.12);color:#ef4444;border:1px solid rgba(239,68,68,.25);flex-shrink:0}
.obj-arrow{font-size:11px;color:#64748b;transition:transform .2s;margin-left:8px}
.obj-arrow.open{transform:rotate(180deg)}
.obj-body{display:none;padding:0 16px 14px;background:#0c1019}
.obj-body.open{display:block}
.obj-tecnica{font-size:10px;font-weight:700;color:__CCOR__;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;padding-top:12px}
.obj-resposta{font-size:13px;color:#c8d0e0;line-height:1.7}
.obj-dica{margin-top:10px;padding:8px 12px;background:rgba(59,130,246,.07);border-left:3px solid __CCOR__;border-radius:0 6px 6px 0;font-size:11.5px;color:#8590a8;line-height:1.55}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="header-title">&#127968; __CNOME__</div>
    <div class="header-sub">Painel do Corretor</div>
  </div>
  <a href="/logout" class="logout-btn">Sair</a>
</div>
<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('hoje')">&#128197; Hoje</button>
  <button class="tab-btn" onclick="switchTab('agenda')">&#128467; Agenda</button>
  <button class="tab-btn" onclick="switchTab('pipeline')">&#128202; Pipeline</button>
  <button class="tab-btn" onclick="switchTab('clientes')">&#128101; Clientes</button>
  <button class="tab-btn" onclick="switchTab('comissao')">&#128176; Comiss&#227;o</button>
  <button class="tab-btn" onclick="switchTab('objecoes')">&#128172; Obje&#231;&#245;es</button>
</div>

<!-- ABA: HOJE -->
<div id="tab-hoje" class="tab-content active">
  <div class="card" style="margin-top:4px">
    <div class="card-title">Resumo de hoje</div>
    <div class="stat-big">
      <div class="num" id="hoje-leads">&#8212;</div>
      <div class="lbl">novos leads hoje</div>
    </div>
    <div class="metric"><span class="metric-label">Em aberto</span><span class="metric-value" id="hoje-aberto">&#8212;</span></div>
    <div class="metric"><span class="metric-label">Conversas hoje</span><span class="metric-value" id="hoje-conv">&#8212;</span></div>
  </div>
  <div class="card">
    <div class="card-title">&#128680; Follow-up necessário</div>
    <div id="followup-list"><div style="text-align:center;padding:16px;color:#5a657d;font-size:12px">Carregando...</div></div>
  </div>
</div>

  <div class="card" style="border-color:rgba(239,68,68,.3);background:rgba(239,68,68,.04)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div class="card-title" style="margin-bottom:0;color:#ef4444">&#128293; Leads quentes parados</div>
      <span style="font-size:10px;color:#64748b">Proposta / Negociação</span>
    </div>
    <div id="quentes-list"><div style="text-align:center;padding:16px;color:#5a657d;font-size:12px">Carregando...</div></div>
  </div>
</div>

<!-- ABA: AGENDA -->
<div id="tab-agenda" class="tab-content">
  <div class="card" style="margin-top:4px">
    <div class="card-title">Visitas de hoje — <span id="agenda-data">&#8212;</span></div>
    <div id="agenda-list"><div style="text-align:center;padding:16px;color:#5a657d;font-size:12px">Nenhuma visita agendada.</div></div>
    <div class="agenda-form">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <input type="time" id="ag-hora" placeholder="Horário"/>
        <input type="text" id="ag-nome" placeholder="Nome do cliente"/>
      </div>
      <input type="text" id="ag-end" placeholder="Endereço / link da visita"/>
      <button class="agenda-add-btn" onclick="agendaAdicionar()">+ Adicionar visita</button>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Próximas visitas</div>
    <div id="agenda-proximas"><div style="text-align:center;padding:12px;color:#5a657d;font-size:12px">Nenhuma.</div></div>
  </div>
</div>

<!-- ABA: PIPELINE -->
<div id="tab-pipeline" class="tab-content">
  <div class="card" style="margin-top:4px">
    <div class="card-title">Funil de vendas</div>
    <div id="pipeline-list"><div style="text-align:center;padding:20px;color:#5a657d">Carregando...</div></div>
  </div>
  <div class="card">
    <div class="stat-big">
      <div class="num" id="pipe-total">&#8212;</div>
      <div class="lbl">leads no pipeline</div>
    </div>
  </div>
</div>

<!-- ABA: CLIENTES -->
<div id="tab-clientes" class="tab-content">
  <div class="card" style="margin-top:4px">
    <div class="card-title">Base de clientes</div>
    <div class="metric"><span class="metric-label">Total de contatos</span><span class="metric-value" id="cli-total">&#8212;</span></div>
    <div class="metric"><span class="metric-label">Ativos (30 dias)</span><span class="metric-value green" id="cli-ativos">&#8212;</span></div>
    <div class="metric"><span class="metric-label">Leads ganhos</span><span class="metric-value green" id="cli-ganhos">&#8212;</span></div>
    <div class="metric"><span class="metric-label">Leads perdidos</span><span class="metric-value red" id="cli-perdidos">&#8212;</span></div>
  </div>
  <div class="card">
    <div class="card-title">Taxa de conversão</div>
    <div class="stat-big">
      <div class="num" id="cli-taxa">&#8212;</div>
      <div class="lbl">% de conversão total</div>
    </div>
  </div>
</div>

<!-- ABA: COMISSÃO -->
<div id="tab-comissao" class="tab-content">
  <div class="card" style="margin-top:4px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div class="card-title" style="margin-bottom:0">Meta do mês — comissão</div>
      <button onclick="toggleConfigComissao()" style="background:transparent;border:1px solid #1d2740;color:#8590a8;font-size:11px;font-weight:600;padding:4px 10px;border-radius:6px;cursor:pointer;font-family:inherit">⚙ Configurar</button>
    </div>
    <div id="config-comissao" style="display:none;background:#131a2b;border:1px solid #1d2740;border-radius:8px;padding:12px;margin-bottom:14px">
      <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px">Minhas configurações</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
        <div>
          <label style="font-size:11px;color:#8590a8;display:block;margin-bottom:4px">Minha comissão (%)</label>
          <input type="number" id="cfg-pct" min="0" max="100" step="0.5" placeholder="ex: 3" style="width:100%;background:#0f1320;border:1px solid #1d2740;color:#eaeef7;padding:7px 10px;border-radius:7px;font-size:13px;font-family:inherit;outline:none"/>
        </div>
        <div>
          <label style="font-size:11px;color:#8590a8;display:block;margin-bottom:4px">Meta mensal (R$)</label>
          <input type="number" id="cfg-meta" min="0" step="500" placeholder="ex: 5000" style="width:100%;background:#0f1320;border:1px solid #1d2740;color:#eaeef7;padding:7px 10px;border-radius:7px;font-size:13px;font-family:inherit;outline:none"/>
        </div>
      </div>
      <button onclick="salvarConfig()" style="background:__CCOR__;border:none;color:#fff;font-size:12.5px;font-weight:700;padding:8px 18px;border-radius:7px;cursor:pointer;font-family:inherit;width:100%">Salvar configurações</button>
    </div>
    <div class="meta-wrap">
      <div class="meta-top">
        <div class="meta-valor" id="com-valor">R$ &#8212;</div>
        <div class="meta-falta">falta <b id="com-falta" style="color:#eaeef7">R$ &#8212;</b></div>
      </div>
      <div class="meta-bar">
        <div class="meta-fill" id="com-meta-bar" style="width:0%"></div>
      </div>
      <div style="display:flex;align-items:center;margin-top:6px">
        <span class="meta-pct-badge" id="com-meta-pct">0%</span>
        <span style="font-size:11px;color:#64748b">da meta de <span id="com-meta-label">—</span></span>
      </div>
      <div class="meta-labels">
        <span>R$ 0</span><span id="com-meta-label2">—</span>
      </div>
    </div>
    <div style="border-top:1px solid #1a2236;margin-top:14px;padding-top:12px">
      <div class="metric"><span class="metric-label">Negócios ganhos</span><span class="metric-value green" id="com-ganhos">&#8212;</span></div>
      <div class="metric"><span class="metric-label">Ticket médio</span><span class="metric-value" id="com-ticket">&#8212;</span></div>
      <div class="metric"><span class="metric-label" id="com-pct-label">Comissão</span><span class="metric-value" id="com-base">&#8212;</span></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Histórico mensal de comissões</div>
    <div id="hist-comissoes">
      <div style="text-align:center;padding:16px;color:#5a657d;font-size:12px">Carregando...</div>
    </div>
  </div>
</div>

<!-- ABA: OBJECOES -->
<div id="tab-objecoes" class="tab-content">
  <div class="card" style="margin-top:4px;background:rgba(59,130,246,.04);border-color:rgba(59,130,246,.2)">
    <div style="display:flex;align-items:center;gap:10px"><span style="font-size:22px">&#128172;</span><div><div style="font-size:14px;font-weight:700;color:#eaeef7">Respostas para Objeções</div><div style="font-size:11px;color:#64748b;margin-top:2px">Toque na objeção para ver como responder</div></div></div>
  </div>
  <div id="obj-lista"></div>
</div>

<script>
var _dadosCarregados = null;
var _tabAtiva = 'hoje';
var COMISSAO_PCT = parseFloat(localStorage.getItem('cor_pct')) || 0;
var META_COMISSAO = parseFloat(localStorage.getItem('cor_meta')) || 0;

function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if(name==='comissao')checkConfigurado();
  var tabs = ['hoje','agenda','pipeline','clientes','comissao','objecoes'];
  document.querySelectorAll('.tab-btn')[tabs.indexOf(name)].classList.add('active');
  _tabAtiva = name;
  if(name==='objecoes'){renderObjecoes();return;}
  if(_dadosCarregados) renderTab(name, _dadosCarregados);
}

function fmt(n){ return (n===null||n===undefined)?'&#8212;':String(n); }
function fmtBRL(n){ if(!n&&n!==0)return 'R$ &#8212;'; return 'R$ '+parseFloat(n).toLocaleString('pt-BR',{minimumFractionDigits:0,maximumFractionDigits:0}); }

/* ── CONFIG COMISSÃO ── */
function toggleConfigComissao(forceOpen){
  var el=document.getElementById('config-comissao');
  var visible=el.style.display!=='none'&&!forceOpen;
  el.style.display=visible?'none':'block';
  if(!visible||forceOpen){
    document.getElementById('cfg-pct').value=COMISSAO_PCT||'';
    document.getElementById('cfg-meta').value=META_COMISSAO||'';
  }
}
function checkConfigurado(){
  if(META_COMISSAO===0||COMISSAO_PCT===0){
    var w=document.getElementById('comissao-aviso');if(w)w.style.display='block';
  }
}

function salvarConfig(){
  var pct=parseFloat(document.getElementById('cfg-pct').value);
  var meta=parseFloat(document.getElementById('cfg-meta').value);
  if(isNaN(pct)||pct<0||pct>100){alert('Comissão inválida (0–100%)');return;}
  if(isNaN(meta)||meta<0){alert('Meta inválida');return;}
  COMISSAO_PCT=pct; META_COMISSAO=meta;
  try{localStorage.setItem('cor_pct',pct);localStorage.setItem('cor_meta',meta);}catch(e){}
  document.getElementById('config-comissao').style.display='none';
  if(_dadosCarregados) renderTab('comissao',_dadosCarregados);
}

/* ── AGENDA (localStorage) ── */
function agendaChave(){ return 'agenda_' + new Date().toISOString().slice(0,10); }
function agendaCarregar(){ try{ return JSON.parse(localStorage.getItem(agendaChave())||'[]'); }catch(e){ return []; } }
function agendaSalvar(items){ try{ localStorage.setItem(agendaChave(), JSON.stringify(items)); }catch(e){} }

function agendaAdicionar(){
  var hora = document.getElementById('ag-hora').value;
  var nome = document.getElementById('ag-nome').value.trim();
  var end  = document.getElementById('ag-end').value.trim();
  if(!hora || !nome){ alert('Preencha horário e nome.'); return; }
  var items = agendaCarregar();
  items.push({hora, nome, end, id: Date.now()});
  items.sort((a,b)=>a.hora.localeCompare(b.hora));
  agendaSalvar(items);
  document.getElementById('ag-hora').value='';
  document.getElementById('ag-nome').value='';
  document.getElementById('ag-end').value='';
  renderAgenda();
}

function agendaRemover(id){
  var items = agendaCarregar().filter(i=>i.id!==id);
  agendaSalvar(items);
  renderAgenda();
}

function renderAgenda(){
  var hoje = new Date();
  document.getElementById('agenda-data').textContent = hoje.toLocaleDateString('pt-BR',{weekday:'long',day:'2-digit',month:'long'});
  var items = agendaCarregar();
  var listEl = document.getElementById('agenda-list');
  if(!items.length){
    listEl.innerHTML = '<div style="text-align:center;padding:16px;color:#5a657d;font-size:12px">Nenhuma visita agendada para hoje.</div>';
  } else {
    listEl.innerHTML = items.map(item=>
      '<div class="agenda-item">' +
      '<div class="agenda-hora">'+(item.hora||'—')+'</div>' +
      '<div class="agenda-body">' +
      '<div class="agenda-nome">'+escHtml(item.nome)+'</div>' +
      (item.end?'<div class="agenda-end">&#128205; '+escHtml(item.end)+'</div>':'') +
      '</div>' +
      '<button class="agenda-del" onclick="agendaRemover('+item.id+')">✕</button>' +
      '</div>'
    ).join('');
  }

  /* Próximas visitas (próximos 7 dias, exceto hoje) */
  var proxItems = [];
  for(var i=1;i<=7;i++){
    var d = new Date(); d.setDate(d.getDate()+i);
    var chave = 'agenda_'+d.toISOString().slice(0,10);
    try{
      var arr = JSON.parse(localStorage.getItem(chave)||'[]');
      arr.forEach(it=>proxItems.push({...it, data:d.toLocaleDateString('pt-BR',{weekday:'short',day:'2-digit',month:'2-digit'})}));
    }catch(e){}
  }
  var proxEl = document.getElementById('agenda-proximas');
  if(!proxItems.length){
    proxEl.innerHTML = '<div style="text-align:center;padding:12px;color:#5a657d;font-size:12px">Nenhuma visita nos próximos 7 dias.</div>';
  } else {
    proxEl.innerHTML = proxItems.map(it=>
      '<div class="agenda-item">' +
      '<div class="agenda-hora" style="min-width:64px;font-size:11px;line-height:1.4">'+it.data+'<br/><span style="font-size:13px">'+it.hora+'</span></div>'+
      '<div class="agenda-body"><div class="agenda-nome">'+escHtml(it.nome)+'</div>'+(it.end?'<div class="agenda-end">'+escHtml(it.end)+'</div>':'')+
      '</div></div>'
    ).join('');
  }
}

function escHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ── RENDER TABS ── */
function renderTab(name, d){
  var funis = d.funis || [];
  var leads = d.leads || [];
  var hoje = new Date(); hoje.setHours(0,0,0,0);

  if(name==='hoje'){
    var novosHoje = leads.filter(l=>{var dt=new Date(l.created_at*1000);dt.setHours(0,0,0,0);return dt.getTime()===hoje.getTime();}).length;
    document.getElementById('hoje-leads').textContent = novosHoje;
    var aberto = leads.filter(l=>!l.closed_at&&!l.loss_reason_id).length;
    document.getElementById('hoje-aberto').textContent = aberto;
    var convHoje = leads.filter(l=>{var dt=l.updated_at?new Date(l.updated_at*1000):null;if(!dt)return false;dt.setHours(0,0,0,0);return dt.getTime()===hoje.getTime();}).length;
    document.getElementById('hoje-conv').textContent = convHoje;

    /* Follow-up com WhatsApp */
    var fl = document.getElementById('followup-list');
    var semContato = leads
      .filter(function(l){return !l.closed_at&&!l.loss_reason_id;})
      .map(function(l){return{
        nome:l.name||l.nome||'Lead',
        telefone:l.telefone||l.phone||'',
        etapa:l.etapa||l.status||'',
        dias:l.updated_at?Math.floor((Date.now()/1000-l.updated_at)/86400):999
      };})
      .filter(function(l){return l.dias>=2;})
      .sort(function(a,b){return b.dias-a.dias;})
      .slice(0,10);

    if(!semContato.length){
      fl.innerHTML='<div style="text-align:center;padding:14px;color:#22c55e;font-size:12px">&#10003; Todos os leads têm contato recente!</div>';
    } else {
      fl.innerHTML = semContato.map(function(l){
        var urgente = l.dias>=5;
        var fone = l.telefone ? String(l.telefone).replace(/\\D/g,'') : '';
        var waLink = fone ? 'https://wa.me/55'+fone : '';
        return '<div class="followup-item" style="flex-direction:column;gap:4px">'+
          '<div style="display:flex;align-items:flex-start;gap:10px;width:100%">'+
          '<div class="followup-dot" style="background:'+(urgente?'#ef4444':'#f59e0b')+';margin-top:5px"></div>'+
          '<div style="flex:1"><div class="followup-name">'+escHtml(l.nome)+'</div>'+
          '<div class="followup-dias">'+l.dias+' dia'+(l.dias>1?'s':'')+' sem contato'+(l.etapa?' &middot; <span style="color:#a78bfa">'+escHtml(l.etapa)+'</span>':'')+'</div></div>'+
          '<span class="followup-badge '+(urgente?'badge-red':'badge-yellow')+'">'+(urgente?'Urgente':'Atenção')+'</span>'+
          '</div>'+
          (waLink?'<div style="padding-left:18px"><a class="wa-btn" style="font-size:11px;padding:5px 10px" href="'+waLink+'" target="_blank">&#128172; WhatsApp</a></div>':'')+
          '</div>';
      }).join('');
    }

    /* Leads QUENTES parados — Proposta / Negociação / Visita */
    var ql = document.getElementById('quentes-list');
    var etapasQ = ['proposta','negoci','fechar','contrato','visita','apresenta'];
    var quentes = leads
      .filter(function(l){
        if(l.closed_at||l.loss_reason_id) return false;
        var etapa = String(l.etapa||l.status||'').toLowerCase();
        return etapasQ.some(function(e){return etapa.indexOf(e)>=0;});
      })
      .map(function(l){return{
        nome:l.name||l.nome||'Lead',
        telefone:l.telefone||l.phone||'',
        etapa:l.etapa||l.status||'—',
        funil:l.funil||'',
        valor:l.valor||l.sale||0,
        dias:l.updated_at?Math.floor((Date.now()/1000-l.updated_at)/86400):999
      };})
      .filter(function(l){return l.dias>=1;})
      .sort(function(a,b){return b.dias-a.dias;})
      .slice(0,8);

    if(!quentes.length){
      ql.innerHTML='<div style="text-align:center;padding:14px;color:#22c55e;font-size:12px">&#10003; Nenhum lead quente parado!</div>';
    } else {
      ql.innerHTML = quentes.map(function(l){
        var fone = l.telefone ? String(l.telefone).replace(/\\D/g,'') : '';
        var msgTxt = 'Olá '+l.nome+', tudo certo? Passando para dar continuidade à nossa conversa sobre o imóvel. Tem um momento esta semana?';
        var waLink = fone ? 'https://wa.me/55'+fone+'?text='+encodeURIComponent(msgTxt) : '';
        var diasCor = l.dias>=5?'#ef4444':l.dias>=3?'#f59e0b':'#a78bfa';
        var vlr = l.valor>0 ? ' &middot; <span style="color:#22c55e;font-size:11px">R$ '+Number(l.valor).toLocaleString('pt-BR')+'</span>' : '';
        return '<div class="quente-card">'+
          '<div class="quente-header">'+
          '<div class="quente-nome">'+escHtml(l.nome)+vlr+'</div>'+
          '<span class="quente-etapa">'+escHtml(l.etapa)+'</span>'+
          '</div>'+
          '<div class="quente-dias" style="color:'+diasCor+'">&#9201; Parado há '+l.dias+' dia'+(l.dias>1?'s':'')+' nesta etapa</div>'+
          (l.funil?'<div class="quente-funil">Funil: '+escHtml(l.funil)+'</div>':'')+
          (waLink
            ? '<a class="wa-btn" href="'+waLink+'" target="_blank">&#128172; Chamar agora no WhatsApp</a>'
            : '<span style="font-size:11px;color:#5a657d">Sem telefone no CRM</span>')+
          '</div>';
      }).join('');
    }
  }

  if(name==='agenda'){ renderAgenda(); }

  if(name==='pipeline'){
    var pipeList = document.getElementById('pipeline-list');
    pipeList.innerHTML='';
    var total=0;
    funis.forEach(f=>{
      var cnt=(f.leads||[]).length; total+=cnt;
      var el=document.createElement('div');el.className='pipeline-item';
      el.innerHTML='<div class="pipeline-dot"></div><div class="pipeline-name">'+(f.nome||f.name||'—')+'</div><div class="pipeline-count">'+cnt+'</div>';
      pipeList.appendChild(el);
    });
    document.getElementById('pipe-total').textContent=total;
  }

  if(name==='clientes'){
    var umMes=Date.now()/1000-30*86400;
    var ganhos=leads.filter(l=>l.closed_at&&!l.loss_reason_id);
    var perdidos=leads.filter(l=>l.loss_reason_id);
    document.getElementById('cli-total').textContent=leads.length;
    document.getElementById('cli-ativos').textContent=leads.filter(l=>l.updated_at&&l.updated_at>umMes).length;
    document.getElementById('cli-ganhos').textContent=ganhos.length;
    document.getElementById('cli-perdidos').textContent=perdidos.length;
    document.getElementById('cli-taxa').textContent=(leads.length>0?Math.round(ganhos.length/leads.length*100):0)+'%';
  }

  if(name==='comissao'){
    /* Mês atual */
    var agora=new Date();
    var inicioMes=new Date(agora.getFullYear(),agora.getMonth(),1,0,0,0,0);
    var ganhosMes=leads.filter(l=>{
      if(!l.closed_at||l.loss_reason_id)return false;
      var d=new Date(l.closed_at*1000);
      return d>=inicioMes;
    });
    var valorMes=ganhosMes.reduce((s,l)=>s+(l.sale||0),0);
    var comissao=valorMes*(COMISSAO_PCT/100);
    var falta=Math.max(0,META_COMISSAO-comissao);
    var pctMeta=META_COMISSAO>0?Math.min(100,Math.round(comissao/META_COMISSAO*100)):0;
    var ticket=ganhosMes.length>0?valorMes/ganhosMes.length:0;

    document.getElementById('com-valor').textContent=fmtBRL(comissao);
    document.getElementById('com-falta').textContent=fmtBRL(falta);
    document.getElementById('com-ganhos').textContent=ganhosMes.length;
    document.getElementById('com-ticket').textContent=fmtBRL(ticket);
    document.getElementById('com-base').textContent=fmtBRL(valorMes)+' total';
    document.getElementById('com-pct-label').textContent='Comissão ('+COMISSAO_PCT+'%)';
    /* Update meta labels */
    var metaLabels=document.querySelectorAll('.meta-labels span');
    if(metaLabels.length>=2)metaLabels[1].textContent=fmtBRL(META_COMISSAO).replace('&#8212;','—');
    var metaInfo=document.querySelector('[style*="meta de R$"]');
    var _ml=document.getElementById('com-meta-label');if(_ml)_ml.textContent=fmtBRL(META_COMISSAO);var _ml2=document.getElementById('com-meta-label2');if(_ml2)_ml2.textContent=fmtBRL(META_COMISSAO);if(metaInfo)metaInfo.innerHTML='<span class="meta-pct-badge" id="com-meta-pct">'+pctMeta+'%</span><span style="font-size:11px;color:#64748b">da meta de '+fmtBRL(META_COMISSAO)+'</span>';
    document.getElementById('com-meta-pct').textContent=pctMeta+'%';

    /* Anima a barra depois de um frame */
    requestAnimationFrame(function(){
      setTimeout(function(){
        document.getElementById('com-meta-bar').style.width=pctMeta+'%';
      },100);
    });

    /* Histórico últimos 6 meses */
    var meses=[];
    for(var i=5;i>=0;i--){
      var ano=agora.getFullYear(), mes=agora.getMonth()-i;
      while(mes<0){mes+=12;ano--;}
      var ini=new Date(ano,mes,1,0,0,0,0);
      var fim=new Date(ano,mes+1,0,23,59,59,999);
      var g=leads.filter(l=>{if(!l.closed_at||l.loss_reason_id)return false;var d=new Date(l.closed_at*1000);return d>=ini&&d<=fim;});
      var val=g.reduce((s,l)=>s+(l.sale||0),0);
      meses.push({label:ini.toLocaleDateString('pt-BR',{month:'short',year:'2-digit'}),comissao:val*(COMISSAO_PCT/100),ganhos:g.length});
    }
    var maxC=Math.max(...meses.map(m=>m.comissao),1);
    var histEl=document.getElementById('hist-comissoes');
    histEl.innerHTML=meses.map((m,i)=>{
      var pct=Math.round(m.comissao/maxC*100);
      var isCurrent=i===5;
      return '<div class="hist-bar-wrap">'+
        '<div class="hist-label"><span style="font-weight:'+(isCurrent?'700':'400')+';color:'+(isCurrent?'#eaeef7':'#8590a8')+'">'+m.label+(isCurrent?' (atual)':'')+'</span>'+
        '<span style="color:'+(isCurrent?'#22c55e':'#64748b')+';font-weight:700">'+fmtBRL(m.comissao)+' &nbsp;<span style="font-weight:400;color:#5a657d">'+m.ganhos+' fechado'+(m.ganhos!==1?'s':'')+'</span></span></div>'+
        '<div class="hist-bar"><div class="hist-fill" style="width:'+pct+'%;'+(isCurrent?'opacity:1':'opacity:.6')+'"></div></div>'+
        '</div>';
    }).join('');
  }
}

var OBJECOES=[
/* ── OBJEÇÕES DE ADIAMENTO ── */
{categoria:"Adiamento",titulo:"Preciso pensar melhor...",tecnica:"Técnica do Prazo + Custo da Demora",resposta:"Claro, faz sentido refletir. Me conta: o que especificamente você precisa pensar? Se for financeiro, localização ou documentação, posso trazer as informações agora. Imóveis assim saem em menos de 30 dias — e hoje você tem condições que podem não existir semana que vem.",dica:"'Preciso pensar' quase sempre esconde outra objeção. Pergunte o que especificamente antes de aceitar o adiamento."},
{categoria:"Adiamento",titulo:"Deixa eu consultar meu advogado/contador...",tecnica:"Técnica da Validação + Urgência Suave",resposta:"Ótima ideia consultar — isso mostra responsabilidade. Posso te adiantar toda a documentação e análise jurídica do imóvel para você levar já pronto para ele avaliar. Assim a reunião com ele é mais rápida e objetiva. Quando vocês se encontram? Porque dependendo, consigo reservar o imóvel por 48 horas para você não perder enquanto consulta.",dica:"Não brigue com o consultor. Facilite e proponha reserva. Isso cria compromisso sem pressão."},
{categoria:"Adiamento",titulo:"Vou esperar o preço cair...",tecnica:"Técnica dos Dados de Mercado",resposta:"Entendo a cautela. Mas esperar para comprar mais barato em mercado aquecido raramente funciona — o que muda é o seu poder de compra relativo. Enquanto você espera, está pagando aluguel ou deixando capital parado. Qual o custo real de esperar mais 6 meses para você?",dica:"A pergunta final transfere o peso concreto da espera para o cliente calcular."},
{categoria:"Adiamento",titulo:"Não é o momento certo...",tecnica:"Técnica do Critério Concreto",resposta:"Quando seria o momento certo para você? Pergunto porque quero entender o que precisa mudar — renda, entrada, emprego. Às vezes o 'momento certo' não chega porque não tem critério definido. E enquanto isso, o imóvel certo foi comprado por outra pessoa. O que exatamente precisa acontecer?",dica:"Force um critério concreto e mensurável. Sem critério a objeção é infinita."},
{categoria:"Adiamento",titulo:"Preciso resolver uns problemas primeiro...",tecnica:"Técnica da Prioridade Invertida",resposta:"Me conta: esses problemas têm prazo para resolver? Porque às vezes o imóvel certo resolve um dos problemas — custo de moradia, localização, estabilidade. E em outros casos dá para estruturar a compra sem interferir. Me conta mais e a gente vê se faz sentido agora ou é melhor esperar.",dica:"Em 40% dos casos o imóvel pode ser parte da solução. Entenda o problema antes de aceitar o não."},
/* ── OBJEÇÕES DE PREÇO ── */
{categoria:"Preço",titulo:"Está caro, vi mais barato...",tecnica:"Técnica da Comparação Real + Valor",resposta:"Fico feliz que você esteja comparando. Me conta onde viu: era no mesmo bairro, mesmo tamanho, mesmo acabamento e condomínio? O preço por metro aqui está alinhado com a região. O que esse imóvel entrega que o outro não entrega?",dica:"Nunca discuta preço diretamente. Desloque para comparação justa e valor entregue."},
{categoria:"Preço",titulo:"Já vi outro mais barato...",tecnica:"Técnica do Custo Total de Propriedade",resposta:"Me conta mais: qual o condomínio mensal, a idade do prédio, o andar, a metragem real útil? Às vezes o preço menor esconde custos maiores: condomínio alto, reforma, localização que exige mais deslocamento. Quando você coloca tudo na ponta do lápis o custo total pode inverter. Posso te ajudar a fazer essa conta agora?",dica:"Peça detalhes do outro imóvel. Raramente o cliente os tem. Isso abre espaço para mostrar diferença real."},
{categoria:"Preço",titulo:"Consegue fazer um desconto?",tecnica:"Técnica da Troca de Valor",resposta:"Vou verificar o que é possível. Mas antes me conta: se conseguirmos uma condição melhor, você fecha hoje? Pergunto porque às vezes consigo uma vantagem — seja no preço, no prazo ou em algum benefício adicional — mas preciso saber se isso realmente resolve para você. O que faria você fechar agora?",dica:"Nunca dê desconto sem condição. Amarre a concessão ao fechamento imediato."},
{categoria:"Preço",titulo:"Os juros do financiamento estão muito altos...",tecnica:"Técnica do Custo de Oportunidade",resposta:"Entendo a preocupação com os juros. Mas quero mostrar uma comparação: o aluguel de um imóvel equivalente é quanto por mês? Somando aluguel por 10 anos, você paga muito mais sem ter nada ao final. Com o financiamento, cada parcela aumenta o seu patrimônio. E se os juros baixarem, você pode refinanciar. E se o imóvel valorizar — o que historicamente acontece — você ganhou nos dois lados.",dica:"Converta juros em patrimônio vs. aluguel zero. Números concretos vencem argumentos abstratos."},
/* ── OBJEÇÕES DE FECHAMENTO ── */
{categoria:"Fechamento",titulo:"Posso assinar amanhã / na semana que vem?",tecnica:"Técnica da Escassez Real",resposta:"Consigo segurar para você até amanhã, mas preciso ser honesto: tive outras pessoas interessadas nesse imóvel essa semana. Não estou dizendo para pressionar — estou dizendo porque seria ruim você perder por 24 horas depois de todo esse processo. O que impede de resolvermos hoje?",dica:"Use escassez real, nunca inventada. Se houver interesse real de outros, mencione. Se não houver, não minta."},
{categoria:"Fechamento",titulo:"Quero ver mais algumas opções antes...",tecnica:"Técnica do Perfil Confirmado",resposta:"Claro, você tem todo o direito de comparar. Me conta: o que esse imóvel tem que você gostou? E o que te faria escolher outro? Pergunto porque se eu entender seu critério posso te mostrar exatamente o que você precisa ver — e talvez economizamos tempo dos dois. O que esse aqui não tem que você consideraria dealbreaker?",dica:"Mapeie o critério real. Se o imóvel já atende, o cliente muitas vezes percebe sozinho quando tenta explicar o que falta."},
{categoria:"Fechamento",titulo:"Não me sinto pronto para assinar...",tecnica:"Técnica da Micro-Decisão",resposta:"Entendo completamente — assinar um contrato é sério. Mas me conta: você se sente inseguro com o imóvel em si ou com algum passo do processo? Porque às vezes o que trava não é a decisão grande, é uma dúvida específica sobre documentação, prazo ou forma de pagamento. Me conta o que te deixa com esse sentimento e a gente resolve ponto a ponto.",dica:"Quebre a decisão grande em micro-decisões. Geralmente há uma dúvida específica disfarçada de insegurança geral."},
{categoria:"Fechamento",titulo:"Preciso dormir mais uma noite...",tecnica:"Técnica do Resumo + Confirmação",resposta:"Faz todo sentido. Antes de você ir, me deixa fazer um resumo rápido do que a gente viu hoje: [imóvel] atende [necessidade], dentro do [orçamento], na [localização] que você queria, e com [condição especial]. O que especificamente você vai ponderar essa noite? Se eu souber, posso até te mandar uma análise por escrito para facilitar.",dica:"O resumo força o cliente a verbalizar o que falta. Na maioria das vezes ele não encontra nada e fecha."},
/* ── OBJEÇÕES DO PRODUTO ── */
{categoria:"Produto",titulo:"O imóvel precisa de reforma...",tecnica:"Técnica da Reforma como Vantagem",resposta:"Isso é na verdade uma vantagem que muita gente não enxerga: você compra mais barato e personaliza do jeito que quer. Um imóvel reformado por outra pessoa tem as escolhas dela. Aqui você define cada detalhe. Posso te colocar em contato com um profissional de confiança para fazer um orçamento realista antes de você decidir — assim você sabe exatamente o custo total.",dica:"Transforme reforma em personalização e vantagem de preço. Ofereça orçamento para reduzir a incerteza."},
{categoria:"Produto",titulo:"O condomínio é muito alto...",tecnica:"Técnica do Custo por Serviço",resposta:"Vamos analisar o que esse condomínio inclui: [listar itens — segurança 24h, academia, piscina, portaria]. Quanto você pagaria separado por cada um desses serviços? Academia sozinha é R$ 100-150/mês. Segurança e portaria, se você tivesse casa, sairia muito mais. Quando você divide assim, o valor por serviço fica bastante razoável.",dica:"Decomponha o condomínio em serviços individuais. O cliente percebe o valor quando compara item a item."},
{categoria:"Produto",titulo:"A área é muito pequena...",tecnica:"Técnica do Uso Real do Espaço",resposta:"Entendo a sensação. Me conta: quantas pessoas vão morar aqui? E quais ambientes são realmente essenciais para o seu dia a dia? Porque muitas vezes a sensação de espaço pequeno muda completamente com a disposição certa dos móveis. Imóveis menores também têm custo de manutenção menor, condomínio menor e muitas vezes localização muito melhor. Qual desses pontos pesa mais para você?",dica:"Foque no uso real, não na metragem bruta. Redirecione para localização e custo de manutenção."},
/* ── OBJEÇÕES DE PROCESSO ── */
{categoria:"Processo",titulo:"Tenho medo da documentação e burocracia...",tecnica:"Técnica da Responsabilidade Transferida",resposta:"Essa parte é exatamente o que eu estou aqui para resolver por você. Meu trabalho é cuidar de toda a documentação, prazo e burocracia — você só vai assinar quando tudo estiver conferido e explicado. Posso te mostrar o passo a passo do processo agora para você ver que é mais simples do que parece? São basicamente [X etapas] e eu acompanho cada uma.",dica:"Assuma a burocracia como sua responsabilidade. O cliente quer delegar isso — essa é parte do seu valor como corretor."},
{categoria:"Processo",titulo:"Tenho medo de me endividar...",tecnica:"Técnica do Reframing Financeiro",resposta:"Esse medo é saudável — significa responsabilidade. Mas financiamento imobiliário não é dívida como cartão. É uma troca: você para de pagar aluguel — que some — e passa a pagar parcela que vira patrimônio. O imóvel valoriza enquanto você paga. Em 5 anos você tem um ativo real. Posso te mostrar uma simulação rápida?",dica:"Reframe 'dívida' para 'investimento forçado com ativo real'. Uma simulação concreta fecha bem."},
{categoria:"Processo",titulo:"Meu cônjuge precisa ver...",tecnica:"Técnica do Aliado",resposta:"Ótimo — isso é sinal que você já tem interesse e quer decidir certo. Quando você pode trazer ele/ela para conhecer? Consigo uma visita rápida até no fim de semana. Se quiser, já preparo um resumo visual do imóvel para mostrar antes — assim a visita é mais objetiva e rápida.",dica:"Facilite o encontro. Quem tem interesse real traz o parceiro em 2 a 3 dias."}
];

function renderObjecoes(){
  var el=document.getElementById('obj-lista');
  if(!el)return;
  var catCores={"Adiamento":"#f59e0b","Preço":"#ef4444","Fechamento":"#22c55e","Produto":"#a78bfa","Processo":"#3b82f6"};
  var lastCat='';
  var html='';
  OBJECOES.forEach(function(o,i){
    if(o.categoria && o.categoria!==lastCat){
      var cor=catCores[o.categoria]||'#64748b';
      html+='<div style="font-size:10px;font-weight:700;color:'+cor+';text-transform:uppercase;letter-spacing:.1em;padding:14px 4px 6px;border-bottom:1px solid #1a2236;margin-bottom:8px">'+escHtml(o.categoria)+'</div>';
      lastCat=o.categoria;
    }
    html+='<div class="obj-item">'+
      '<div class="obj-header" onclick="toggleObj('+i+')">'+
      '<div class="obj-titulo">'+escHtml(o.titulo)+'</div>'+
      '<span class="obj-tag" style="background:rgba('+(o.categoria==='Fechamento'?'34,197,94':'239,68,68')+',.12);color:'+(o.categoria==='Fechamento'?'#22c55e':'#ef4444')+';border-color:'+(o.categoria==='Fechamento'?'rgba(34,197,94,.3)':'rgba(239,68,68,.25)')+'">'+escHtml(o.categoria||'Objeção')+'</span>'+
      '<span class="obj-arrow" id="obj-arrow-'+i+'">&#9660;</span>'+
      '</div>'+
      '<div class="obj-body" id="obj-body-'+i+'">'+
      '<div class="obj-tecnica">'+escHtml(o.tecnica)+'</div>'+
      '<div class="obj-resposta">'+escHtml(o.resposta)+'</div>'+
      '<div class="obj-dica"><b>&#128161; Dica:</b> '+escHtml(o.dica)+'</div>'+
      '</div></div>';
  });
  el.innerHTML=html;
}

function toggleObj(i){
  var body=document.getElementById('obj-body-'+i),arrow=document.getElementById('obj-arrow-'+i);
  var open=body.classList.contains('open');
  document.querySelectorAll('.obj-body').forEach(function(e){e.classList.remove('open');});
  document.querySelectorAll('.obj-arrow').forEach(function(e){e.classList.remove('open');});
  if(!open){body.classList.add('open');arrow.classList.add('open');}
}


function mostrarErroKommo(msg){
  var m=msg||'Verifique as credenciais do Kommo no painel do administrador.';
  var h='<div style="text-align:center;padding:14px;color:#ef4444;font-size:12px">⚠️ '+m+'</div>';
  ['followup-list','quentes-list','pipeline-list'].forEach(function(id){
    var el=document.getElementById(id);if(el)el.innerHTML=h;
  });
}
async function carregarDados(){
  try{
    var ctrl=new AbortController();
    var timer=setTimeout(function(){ctrl.abort();},100000);
    var r=await fetch('/api/dados',{signal:ctrl.signal});
    clearTimeout(timer);
    if(!r.ok){mostrarErroKommo('Erro de conexao com o servidor ('+r.status+').');return;}
    var j=await r.json();
    if(j.status==='erro'||(!j.dados&&j.erro)){mostrarErroKommo(j.erro||'Erro ao carregar dados do Kommo.');return;}
    _dadosCarregados=j.dados||{};
    renderTab(_tabAtiva,_dadosCarregados);
  }catch(e){
    if(e.name==='AbortError'){mostrarErroKommo('Tempo esgotado. Recarregue a pagina.');}
    else{mostrarErroKommo('Erro: '+e.message);}
    console.error(e);
  }
}

/* Init */
renderAgenda();
carregarDados();
setInterval(carregarDados,120000);
</script>
</body>
</html>
"""




# ══════════════════════════  PAINEL ADMIN  ═════════════════════════════════

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
.sub{{color:#8590a8;font-size:12.5px;margin-bottom:20px;margin-top:2px}}
.topbar{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:12px}}
.logout-btn{{background:transparent;border:1px solid #1d2740;color:#8590a8;font-size:12px;font-weight:600;padding:7px 14px;border-radius:7px;cursor:pointer;font-family:inherit;transition:all .15s;text-decoration:none;display:inline-flex;align-items:center;gap:6px}}
.logout-btn:hover{{color:#ef4444;border-color:#ef4444}}
/* Tabs */
.tab-bar{{display:flex;gap:0;border-bottom:1px solid #1d2740;margin-bottom:24px}}
.tab-btn{{background:transparent;border:none;border-bottom:2px solid transparent;color:#64748b;font-size:13px;font-weight:600;padding:10px 20px;cursor:pointer;font-family:inherit;transition:all .15s;margin-bottom:-1px}}
.tab-btn.active{{color:#3b82f6;border-bottom-color:#3b82f6}}
.tab-btn:hover:not(.active){{color:#eaeef7}}
.tab-content{{display:block}}
.tab-content.hidden{{display:none}}
/* Forms */
.add-form{{background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:22px 24px;margin-bottom:28px}}
.add-form h2{{font-size:14px;font-weight:700;margin-bottom:16px;color:#eaeef7}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:600px){{.form-grid{{grid-template-columns:1fr}}}}
.form-group{{display:flex;flex-direction:column;gap:5px}}
label{{font-size:11px;color:#8590a8;text-transform:uppercase;letter-spacing:.07em;font-weight:700}}
input,select{{background:#131a2b;border:1px solid #1d2740;color:#eaeef7;padding:9px 12px;border-radius:7px;font-size:13px;font-family:inherit;outline:none;transition:border-color .15s}}
input:focus,select:focus{{border-color:#3b82f6}}
.add-btn{{background:#1e3a8a;border:none;color:#fff;font-size:13px;font-weight:700;padding:10px 20px;border-radius:8px;cursor:pointer;font-family:inherit;margin-top:8px;transition:background .15s}}
.add-btn:hover{{background:#2c4dbd}}
/* Clients */
.clients-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.client-card{{background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:18px 20px}}
.client-card.bloqueado{{border-color:rgba(239,68,68,.3);background:rgba(239,68,68,.04)}}
.client-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}}
.client-name{{font-size:15px;font-weight:700}}
.client-user{{font-size:11.5px;color:#8590a8;margin-top:2px}}
.status-badge{{padding:3px 10px;border-radius:20px;font-size:10.5px;font-weight:700;text-transform:uppercase}}
.status-badge.ativo{{background:rgba(34,197,94,.13);color:#22c55e;border:1px solid rgba(34,197,94,.25)}}
.status-badge.bloqueado{{background:rgba(239,68,68,.13);color:#ef4444;border:1px solid rgba(239,68,68,.25)}}
.status-badge.trial{{background:rgba(245,158,11,.13);color:#f59e0b;border:1px solid rgba(245,158,11,.25)}}
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
/* Corretores */
.cor-card{{background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:18px 20px}}
.cor-card.bloqueado{{border-color:rgba(239,68,68,.3);background:rgba(239,68,68,.04)}}
.cor-avatar{{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;flex-shrink:0;color:#fff}}
.cor-name{{font-size:15px;font-weight:700}}
.cor-user{{font-size:11.5px;color:#8590a8;margin-top:2px}}
.cor-info{{font-size:11.5px;color:#8590a8;margin:10px 0 14px;line-height:1.7}}
.cor-info b{{color:#eaeef7}}
.corretores-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}}
</style>
</head>
<body>
<div style="max-width:1100px;margin:0 auto">
<div class="topbar">
  <div>
    <h1>Admin &mdash; Painel CRM</h1>
    <p class="sub">Gerencie todos os clientes e configurações</p>
  </div>
  <div style="display:flex;gap:8px">
    <a href="/admin/meu-painel" class="logout-btn" style="background:#1e3a8a;color:#fff;border-color:#2c4dbd">Meu Painel</a>
    <a href="/logout" class="logout-btn">Sair</a>
  </div>
</div>
<div class="msg">{msg}</div>

<!-- Tab bar -->
<div class="tab-bar">
  <button class="tab-btn active" id="tbClientes" onclick="switchTab('clientes')">👥 Clientes</button>
  <button class="tab-btn" id="tbEmails" onclick="switchTab('emails')">📧 Emails</button>
  <button class="tab-btn" id="tbCanais" onclick="switchTab('canais')">🔗 Canais & QR</button>
  <button class="tab-btn" id="tbCorretores" onclick="switchTab('corretores')">🏠 Corretores</button>
  <button class="tab-btn" id="tbConfig" onclick="switchTab('config')">⚙️ Configurações</button>
</div>

<!-- Tab: Clientes -->
<div id="tab-clientes" class="tab-content">
<div class="add-form">
  <h2>➕ Adicionar novo cliente</h2>
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
</div>

<!-- Tab: Emails -->
<div id="tab-emails" class="tab-content hidden">
__EMAIL_TAB__
</div>

<!-- Tab: Canais & QR -->
<div id="tab-canais" class="tab-content hidden">
__CANAL_TAB__
</div>

<!-- Tab: Corretores -->
<div id="tab-corretores" class="tab-content hidden">
__CORRETORES_TAB__
</div>

<!-- Tab: Configurações -->
<div id="tab-config" class="tab-content hidden">
  <div class="add-form" style="max-width:480px">
    <h2>🔑 Trocar minha senha</h2>
    <form method="POST" action="/admin/trocar_senha">
      <div class="form-group">
        <label>Senha atual</label>
        <input type="password" name="senha_atual" required placeholder="••••••••"/>
      </div>
      <div class="form-group">
        <label>Nova senha</label>
        <input type="password" name="nova_senha" required placeholder="Mínimo 6 caracteres"/>
      </div>
      <div class="form-group">
        <label>Confirmar nova senha</label>
        <input type="password" name="confirma_senha" required placeholder="Repita a nova senha"/>
      </div>
      <button class="btn" type="submit">Salvar nova senha</button>
    </form>
  </div>

  <div class="add-form" style="max-width:480px;margin-top:24px">
    <h2>👁️ Criar acesso demo</h2>
    <p style="color:#8590a8;font-size:13px;margin-bottom:16px;line-height:1.5">
      Cria um login temporário com os seus dados. Para revogar o acesso, vá em
      <b>Clientes</b> e clique em <b>Bloquear</b>.
    </p>
    <form method="POST" action="/admin/criar_demo">
      <div class="form-grid">
        <div class="form-group">
          <label>Login (ex: demo)</label>
          <input type="text" name="username" value="demo" required/>
        </div>
        <div class="form-group">
          <label>Nome exibido</label>
          <input type="text" name="nome" placeholder="Visitante Demo"/>
        </div>
        <div class="form-group" style="grid-column:1/-1">
          <label>Senha para o demo</label>
          <input type="text" name="password" required placeholder="Ex: demo2024"/>
        </div>
      </div>
      <button class="btn" type="submit" style="background:linear-gradient(135deg,#f59e0b,#d97706)">
        Criar acesso demo
      </button>
    </form>
  </div>
</div>

</div><!-- /max-width -->
<script>
function switchTab(name) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.remove('hidden');
  document.getElementById('tb' + name.charAt(0).toUpperCase() + name.slice(1)).classList.add('active');
  try {{ localStorage.setItem('adminTab', name); }} catch(e) {{}}
}}
// Restaura última aba
(function() {{
  try {{
    var t = localStorage.getItem('adminTab');
    if (t) switchTab(t);
  }} catch(e) {{}}
}})();
</script>
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
        # SameSite=None permite que o cookie funcione dentro de iframes cross-origin (ex: widget Kommo)
        self.send_header("Set-Cookie", f"{name}={value}; Path=/; HttpOnly; Secure; Max-Age={max_age}; SameSite=None")

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

    def _build_email_tab(self, users):
        """Constrói o HTML da aba Emails do admin — sem passar por .format() do ADMIN_HTML."""
        smtp_ok     = bool(GMAIL_USER and GMAIL_PASS)
        smtp_cor    = "#22c55e" if smtp_ok else "#ef4444"
        smtp_bg     = "#0a1f0a" if smtp_ok else "#1f0a0a"
        smtp_border = "#166534" if smtp_ok else "#7f1d1d"
        smtp_txt    = ("✅ SMTP configurado — " + GMAIL_USER) if smtp_ok else "❌ SMTP não configurado (defina GMAIL_USER e GMAIL_PASS)"

        with _email_log_lock:
            log_copy = list(_email_log)
        n_log = len(log_copy)
        n_ok  = sum(1 for e in log_copy if e["status"] == "ok")
        n_err = sum(1 for e in log_copy if e["status"] == "erro")

        # ── Opções de clientes para o dropdown ────────────────────────────────
        opts = '<option value="">📤 Todos os clientes (com email)</option>'
        for uname, ud in (users or {}).items():
            em   = ud.get("raiox_email") or ud.get("email") or ""
            nome = ud.get("nome", uname)
            if em:
                opts += f'<option value="{uname}">{nome} — {em}</option>'

        # ── Linhas do log ──────────────────────────────────────────────────────
        TIPO_LABEL  = {"manha": "☀️ Relatório Manhã", "semana": "📅 Relatório Semana",
                       "alerta": "🔔 Alerta", "raiox": "📊 Raio-X"}
        STATUS_CLS  = {"ok": "#22c55e", "erro": "#ef4444", "sem_config": "#f59e0b"}
        STATUS_ICON = {"ok": "✅", "erro": "❌", "sem_config": "⚠️"}
        rows = ""
        for i, e in enumerate(log_copy):
            cor        = STATUS_CLS.get(e["status"], "#94a3b8")
            icon       = STATUS_ICON.get(e["status"], "•")
            tipo_l     = TIPO_LABEL.get(e["tipo"], e["tipo"])
            ts_fmt     = e["ts"].replace("T", " ")
            ass_esc    = e["assunto"].replace("<","&lt;").replace(">","&gt;")
            dest_esc   = e["destinatario"].replace("<","&lt;").replace(">","&gt;")
            rows += (
                f'<tr onclick="verPreviewEmail({i})" style="cursor:pointer" class="erow">'
                f'<td style="color:#64748b;font-size:11px;white-space:nowrap">{ts_fmt}</td>'
                f'<td style="font-size:12px">{tipo_l}</td>'
                f'<td style="color:#60a5fa;font-size:12px">{dest_esc}</td>'
                f'<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">{ass_esc}</td>'
                f'<td style="color:{cor};font-weight:700;font-size:12px">{icon} {e["status"]}</td>'
                f'</tr>'
            )
        if not rows:
            rows = "<tr><td colspan='5' style='text-align:center;color:#475569;padding:32px 0;font-size:13px'>Nenhum email enviado ainda nesta sessão.</td></tr>"

        return f"""
<div style="max-width:860px">

  <!-- SMTP Status -->
  <div style="background:{smtp_bg};border:1px solid {smtp_border};border-radius:10px;
       padding:12px 18px;font-size:13px;font-weight:600;color:{smtp_cor};margin-bottom:20px">
    {smtp_txt}
    <span style="font-size:11px;font-weight:400;color:#64748b;margin-left:16px">
      Automático: ☀️ 06:00 (relatório ontem) &nbsp;·&nbsp; 📅 19:00 (semana) &nbsp;·&nbsp; 📊 Dia 31 às 08:00 (Raio-X mensal — dia 28 em meses curtos)
    </span>
  </div>

  <!-- Envio Manual -->
  <div style="background:#0f1320;border:1px solid #1d2740;border-radius:14px;
       overflow:hidden;margin-bottom:20px">
    <div style="background:#0a0f1e;border-bottom:1px solid #1d2740;padding:14px 20px;
         display:flex;align-items:center;gap:10px">
      <div style="background:#1e3a8a;width:34px;height:34px;border-radius:8px;
           display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0">✉️</div>
      <div>
        <div style="font-size:14px;font-weight:700;color:#eaeef7">Envio Manual</div>
        <div style="font-size:11px;color:#64748b;margin-top:1px">
          Escolha o cliente e o tipo de relatório para enviar agora
        </div>
      </div>
    </div>
    <div style="padding:20px 24px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px">
        <div>
          <label style="font-size:11px;color:#8590a8;text-transform:uppercase;
                 letter-spacing:.07em;font-weight:700;display:block;margin-bottom:6px">
            Cliente
          </label>
          <select id="sendCliente" style="width:100%;background:#131a2b;border:1px solid #1d2740;
                  color:#eaeef7;padding:9px 12px;border-radius:7px;font-size:13px;font-family:inherit">
            {opts}
          </select>
        </div>
        <div>
          <label style="font-size:11px;color:#8590a8;text-transform:uppercase;
                 letter-spacing:.07em;font-weight:700;display:block;margin-bottom:6px">
            Tipo de relatório
          </label>
          <select id="sendTipo" style="width:100%;background:#131a2b;border:1px solid #1d2740;
                  color:#eaeef7;padding:9px 12px;border-radius:7px;font-size:13px;font-family:inherit">
            <option value="manha">☀️ Relatório de Ontem (manhã)</option>
            <option value="semana">📅 Relatório da Semana</option>
          </select>
        </div>
      </div>
      <button onclick="enviarEmailManual()" id="btnEnviar"
              style="background:#1e3a8a;border:none;color:#fff;font-size:13px;font-weight:700;
                     padding:10px 24px;border-radius:8px;cursor:pointer;font-family:inherit;
                     transition:opacity .15s">
        ✉️ Enviar agora
      </button>
      <div id="sendResult" style="margin-top:12px;font-size:13px;display:none"></div>
    </div>
  </div>

  <!-- Contadores -->
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px">
    <div style="background:#0f1320;border:1px solid #1d2740;border-radius:10px;padding:14px 16px">
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin-bottom:4px">Enviados (sessão)</div>
      <div style="font-size:26px;font-weight:800;color:#3b82f6">{n_log}</div>
    </div>
    <div style="background:#0f1320;border:1px solid #1d2740;border-radius:10px;padding:14px 16px">
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin-bottom:4px">Entregues</div>
      <div style="font-size:26px;font-weight:800;color:#22c55e">{n_ok}</div>
    </div>
    <div style="background:#0f1320;border:1px solid #1d2740;border-radius:10px;padding:14px 16px">
      <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin-bottom:4px">Erros</div>
      <div style="font-size:26px;font-weight:800;color:#ef4444">{n_err}</div>
    </div>
  </div>

  <!-- Log de emails -->
  <div style="background:#0f1320;border:1px solid #1d2740;border-radius:12px;overflow:hidden">
    <div style="background:#0a0f1e;border-bottom:1px solid #1d2740;padding:12px 18px;
         display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:13px;font-weight:700;color:#eaeef7">📋 Histórico desta sessão</span>
      <button onclick="location.reload()" style="background:#1e293b;border:1px solid #1d2740;
              color:#8590a8;font-size:11px;font-weight:600;padding:5px 12px;border-radius:6px;
              cursor:pointer;font-family:inherit">↻ Atualizar</button>
    </div>
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="background:#0a0f1e">
            <th style="padding:10px 14px;text-align:left;font-size:10px;text-transform:uppercase;
                letter-spacing:.05em;color:#64748b;border-bottom:1px solid #1d2740">Data/Hora</th>
            <th style="padding:10px 14px;text-align:left;font-size:10px;text-transform:uppercase;
                letter-spacing:.05em;color:#64748b;border-bottom:1px solid #1d2740">Tipo</th>
            <th style="padding:10px 14px;text-align:left;font-size:10px;text-transform:uppercase;
                letter-spacing:.05em;color:#64748b;border-bottom:1px solid #1d2740">Destinatário</th>
            <th style="padding:10px 14px;text-align:left;font-size:10px;text-transform:uppercase;
                letter-spacing:.05em;color:#64748b;border-bottom:1px solid #1d2740">Assunto</th>
            <th style="padding:10px 14px;text-align:left;font-size:10px;text-transform:uppercase;
                letter-spacing:.05em;color:#64748b;border-bottom:1px solid #1d2740">Status</th>
          </tr>
        </thead>
        <tbody id="emailLogBody">{rows}</tbody>
      </table>
    </div>
    <div style="padding:10px 18px;font-size:11px;color:#475569;text-align:right;
         border-top:1px solid #1d274022">
      {n_log} email(s) registrado(s) nesta sessão
    </div>
  </div>

</div>

<!-- Preview modal -->
<div id="emailPreviewOverlay" onclick="fecharEmailPreview(event)"
     style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);
            z-index:1000;align-items:center;justify-content:center;padding:20px">
  <div style="background:#0f172a;border:1px solid #1d2740;border-radius:12px;
       width:100%;max-width:640px;max-height:90vh;overflow:hidden;display:flex;flex-direction:column">
    <div style="padding:14px 18px;border-bottom:1px solid #1d2740;
         display:flex;justify-content:space-between;align-items:center;font-size:13px;font-weight:700">
      <span id="emailPreviewTitle">Pré-visualização</span>
      <button onclick="fecharEmailPreview()" style="background:none;border:none;
              color:#94a3b8;cursor:pointer;font-size:18px;font-family:inherit">✕</button>
    </div>
    <iframe id="emailPreviewFrame" style="flex:1;border:none;background:#0f172a;min-height:400px"
            src="about:blank"></iframe>
  </div>
</div>

<script>
function enviarEmailManual() {{
  var cliente = document.getElementById('sendCliente').value;
  var tipo    = document.getElementById('sendTipo').value;
  var btn     = document.getElementById('btnEnviar');
  var res     = document.getElementById('sendResult');
  btn.disabled = true;
  btn.textContent = '⏳ Enviando...';
  btn.style.opacity = '.6';
  res.style.display = 'none';
  fetch('/api/admin/emails/send', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    credentials: 'include',
    body: JSON.stringify({{tipo: tipo, username: cliente}})
  }})
  .then(r => r.json())
  .then(j => {{
    res.style.display = 'block';
    if (j.ok) {{
      res.style.color = '#22c55e';
      res.innerHTML = '✅ ' + (j.msg || 'Enviado com sucesso!');
    }} else {{
      res.style.color = '#ef4444';
      res.innerHTML = '❌ ' + (j.erro || 'Erro ao enviar.');
    }}
    setTimeout(() => location.reload(), 3000);
  }})
  .catch(() => {{
    res.style.display = 'block';
    res.style.color = '#ef4444';
    res.textContent = '❌ Erro de conexão.';
    btn.disabled = false;
    btn.textContent = '✉️ Enviar agora';
    btn.style.opacity = '1';
  }});
}}

function verPreviewEmail(idx) {{
  document.getElementById('emailPreviewTitle').textContent = 'Carregando...';
  document.getElementById('emailPreviewFrame').src = '/api/admin/emails/preview?idx=' + idx;
  var ov = document.getElementById('emailPreviewOverlay');
  ov.style.display = 'flex';
  document.getElementById('emailPreviewFrame').onload = function() {{
    document.getElementById('emailPreviewTitle').textContent = 'Pré-visualização do Email';
  }};
}}
function fecharEmailPreview(e) {{
  if (!e || e.target === document.getElementById('emailPreviewOverlay')) {{
    document.getElementById('emailPreviewOverlay').style.display = 'none';
    document.getElementById('emailPreviewFrame').src = 'about:blank';
  }}
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') fecharEmailPreview();
}});
// highlight rows on hover
document.querySelectorAll('.erow').forEach(function(r) {{
  r.addEventListener('mouseenter', function() {{ this.style.background='rgba(255,255,255,.04)'; }});
  r.addEventListener('mouseleave', function() {{ this.style.background=''; }});
}});
</script>
"""

    def _build_canal_tab(self, users):
        """Constrói a aba Canais & QR do admin — gerador de QR codes rastreáveis."""
        # Lista de usuários para o debug selector
        user_opts = "".join(
            f'<option value="{u}">{ud.get("nome","") or u} (@{u})</option>'
            for u, ud in users.items()
        )
        return f"""
<div style="max-width:720px">

  <!-- Header -->
  <div style="margin-bottom:24px">
    <h2 style="color:#eaeef7;font-size:17px;font-weight:800;margin:0 0 6px">
      🔗 Gerador de QR Code com rastreamento
    </h2>
    <p style="color:#64748b;font-size:13px;line-height:1.6;margin:0">
      Crie links rastreáveis para cada campanha. Quando o cliente escanear e mandar mensagem,
      o Kommo receberá um texto contendo o código da origem — então você configura um robô
      no Kommo para <strong style="color:#94a3b8">adicionar a tag automaticamente</strong>
      (ex: tag <code style="background:#1d2740;padding:1px 5px;border-radius:4px">TIKTOK</code>).
      O painel detecta a tag e contabiliza o canal corretamente. ✅
    </p>
  </div>

  <!-- Gerador -->
  <div style="background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:22px;margin-bottom:20px">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px">
      <div>
        <label style="display:block;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">
          Número WhatsApp (com DDD, sem +55)
        </label>
        <input id="qrPhone" type="text" placeholder="ex: 11999999999"
          style="width:100%;background:#161d30;border:1px solid #1d2740;color:#eaeef7;padding:9px 12px;border-radius:8px;font-size:13px;font-family:inherit;box-sizing:border-box"
          oninput="gerarQRPreview()">
      </div>
      <div>
        <label style="display:block;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">
          Canal / Origem da campanha
        </label>
        <select id="qrCanal"
          style="width:100%;background:#161d30;border:1px solid #1d2740;color:#eaeef7;padding:9px 12px;border-radius:8px;font-size:13px;font-family:inherit;box-sizing:border-box"
          onchange="atualizarMensagemPadrao(); gerarQRPreview()">
          <option value="tiktok"     data-tag="TIKTOK"     data-emoji="🎵">TikTok</option>
          <option value="instagram"  data-tag="INSTAGRAM"  data-emoji="📸">Instagram</option>
          <option value="facebook"   data-tag="FACEBOOK"   data-emoji="👤">Facebook / Meta Ads</option>
          <option value="google"     data-tag="GOOGLE"     data-emoji="🔍">Google Ads</option>
          <option value="site"       data-tag="SITE"       data-emoji="🌐">Site / Landing Page</option>
          <option value="indicacao"  data-tag="INDICACAO"  data-emoji="🤝">Indicação</option>
          <option value="youtube"    data-tag="YOUTUBE"    data-emoji="▶️">YouTube</option>
          <option value="whatsapp"   data-tag="WPP"        data-emoji="💬">WhatsApp Direto</option>
        </select>
      </div>
    </div>
    <div style="margin-bottom:14px">
      <label style="display:block;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">
        Mensagem de abertura (enviada automaticamente pelo cliente)
      </label>
      <input id="qrMsg" type="text"
        style="width:100%;background:#161d30;border:1px solid #1d2740;color:#eaeef7;padding:9px 12px;border-radius:8px;font-size:13px;font-family:inherit;box-sizing:border-box"
        oninput="gerarQRPreview()" placeholder="Olá! Vim pelo TikTok 🎵 [SRC:tiktok]">
    </div>

    <!-- Preview e QR -->
    <div id="qrResultBox" style="display:none;background:#0a0f1e;border:1px solid #1d2740;border-radius:10px;padding:18px;margin-top:4px">
      <div style="display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap">
        <!-- QR Code -->
        <div style="flex-shrink:0;text-align:center">
          <img id="qrImg" src="" alt="QR Code"
            style="width:180px;height:180px;border-radius:8px;border:4px solid #fff;background:#fff;display:block">
          <div style="margin-top:8px">
            <a id="qrDownload" href="#" download="qr_rastreamento.png" target="_blank"
              style="font-size:11px;color:#3b82f6;text-decoration:none;font-weight:600">
              ⬇ Baixar QR Code
            </a>
          </div>
        </div>
        <!-- Link e instruções -->
        <div style="flex:1;min-width:220px">
          <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">
            Link gerado
          </div>
          <div id="qrLinkBox"
            style="background:#161d30;border:1px solid #1d2740;border-radius:6px;padding:9px 12px;font-size:11px;color:#94a3b8;word-break:break-all;line-height:1.5;margin-bottom:10px">
          </div>
          <button onclick="copiarLinkQR()"
            style="background:#1d2740;border:1px solid #2a3a5c;color:#94a3b8;font-size:12px;font-weight:600;padding:7px 14px;border-radius:6px;cursor:pointer;font-family:inherit;margin-right:8px">
            📋 Copiar link
          </button>
          <div id="qrCopyMsg" style="display:none;color:#22c55e;font-size:12px;margin-top:6px">✅ Copiado!</div>
          <div style="margin-top:14px">
            <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">
              Tag para configurar no Kommo
            </div>
            <code id="qrTagCode"
              style="background:#1d2740;color:#22c55e;padding:5px 10px;border-radius:6px;font-size:13px;font-weight:700">
            </code>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Instruções Kommo -->
  <div style="background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:20px;margin-bottom:20px">
    <div style="font-size:13px;font-weight:800;color:#eaeef7;margin-bottom:14px">
      ⚙ Como configurar o rastreamento no Kommo
    </div>
    <div style="display:grid;grid-template-columns:auto 1fr;gap:8px 14px;font-size:12.5px;color:#94a3b8;line-height:1.7">
      <div style="color:#3b82f6;font-weight:700;font-size:15px">1</div>
      <div>No Kommo, acesse <strong style="color:#eaeef7">Configurações → Automações → Robôs</strong></div>
      <div style="color:#3b82f6;font-weight:700;font-size:15px">2</div>
      <div>Crie um novo robô com gatilho: <strong style="color:#eaeef7">"Quando novo lead chega"</strong></div>
      <div style="color:#3b82f6;font-weight:700;font-size:15px">3</div>
      <div>Adicione condição: <strong style="color:#eaeef7">"Mensagem contém [SRC:tiktok]"</strong> (ou o código do canal)</div>
      <div style="color:#3b82f6;font-weight:700;font-size:15px">4</div>
      <div>Ação: <strong style="color:#eaeef7">"Adicionar tag: TIKTOK"</strong> ao lead</div>
      <div style="color:#3b82f6;font-weight:700;font-size:15px">5</div>
      <div>O painel detecta a tag automaticamente e conta o lead no canal certo ✅</div>
    </div>
    <div style="margin-top:14px;padding:12px;background:#0a0f1e;border-radius:8px;border-left:3px solid #3b82f6">
      <div style="font-size:11px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">
        Códigos de rastreamento por canal
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        <span style="background:#1d2740;padding:4px 10px;border-radius:6px;font-size:11px;color:#94a3b8"><b style="color:#f59e0b">TikTok:</b> [SRC:tiktok]</span>
        <span style="background:#1d2740;padding:4px 10px;border-radius:6px;font-size:11px;color:#94a3b8"><b style="color:#ec4899">Instagram:</b> [SRC:ig]</span>
        <span style="background:#1d2740;padding:4px 10px;border-radius:6px;font-size:11px;color:#94a3b8"><b style="color:#1877f2">Facebook:</b> [SRC:fb]</span>
        <span style="background:#1d2740;padding:4px 10px;border-radius:6px;font-size:11px;color:#94a3b8"><b style="color:#ea4335">Google:</b> [SRC:google]</span>
        <span style="background:#1d2740;padding:4px 10px;border-radius:6px;font-size:11px;color:#94a3b8"><b style="color:#22c55e">WhatsApp:</b> [SRC:wpp]</span>
        <span style="background:#1d2740;padding:4px 10px;border-radius:6px;font-size:11px;color:#94a3b8"><b style="color:#0ea5e9">Site:</b> [SRC:site]</span>
        <span style="background:#1d2740;padding:4px 10px;border-radius:6px;font-size:11px;color:#94a3b8"><b style="color:#d97706">Indicação:</b> [SRC:indicacao]</span>
      </div>
    </div>
  </div>

  <!-- Diagnóstico -->
  <div style="background:#0f1320;border:1px solid #1d2740;border-radius:12px;padding:20px">
    <div style="font-size:13px;font-weight:800;color:#eaeef7;margin-bottom:12px">
      🔍 Diagnóstico de canais
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
      <select id="diagUser"
        style="background:#161d30;border:1px solid #1d2740;color:#eaeef7;padding:8px 12px;border-radius:8px;font-size:12px;font-family:inherit">
        {user_opts}
      </select>
      <button onclick="rodarDiag()"
        style="background:#3b82f6;border:none;color:#fff;font-size:12px;font-weight:700;padding:9px 18px;border-radius:8px;cursor:pointer;font-family:inherit">
        Analisar canais
      </button>
    </div>
    <div id="diagResult" style="font-size:12px;color:#64748b">
      Selecione um cliente e clique em "Analisar canais" para ver a distribuição de canais e funis.
    </div>
  </div>

</div>

<script>
(function() {{
  // Preenche mensagem padrão no carregamento
  atualizarMensagemPadrao();
}})();

function atualizarMensagemPadrao() {{
  var sel = document.getElementById('qrCanal');
  var opt = sel.options[sel.selectedIndex];
  var canal = sel.value;
  var emoji = opt.getAttribute('data-emoji') || '';
  var canalNome = opt.text.split(' / ')[0];
  var codigo = '[SRC:' + canal + ']';
  document.getElementById('qrMsg').value = 'Olá! Vim pelo ' + canalNome + ' ' + emoji + ' ' + codigo;
  gerarQRPreview();
}}

function gerarQRPreview() {{
  var phone = (document.getElementById('qrPhone').value || '').replace(/\\D/g, '');
  var msg   = document.getElementById('qrMsg').value || '';
  var sel   = document.getElementById('qrCanal');
  var opt   = sel.options[sel.selectedIndex];
  var tag   = opt.getAttribute('data-tag') || sel.value.toUpperCase();
  var box   = document.getElementById('qrResultBox');

  if (!phone || phone.length < 10) {{ box.style.display='none'; return; }}

  // Monta link wa.me
  var num  = phone.startsWith('55') ? phone : '55' + phone;
  var link = 'https://wa.me/' + num + (msg ? '?text=' + encodeURIComponent(msg) : '');

  // QR via Google Charts API
  var qrUrl = 'https://chart.googleapis.com/chart?cht=qr&chs=300x300&chld=M|1&chl=' + encodeURIComponent(link);

  document.getElementById('qrImg').src = qrUrl;
  document.getElementById('qrDownload').href = qrUrl;
  document.getElementById('qrDownload').download = 'qr_' + sel.value + '.png';
  document.getElementById('qrLinkBox').textContent = link;
  document.getElementById('qrTagCode').textContent = tag;
  box.style.display = 'block';
}}

function copiarLinkQR() {{
  var link = document.getElementById('qrLinkBox').textContent;
  if (navigator.clipboard) {{
    navigator.clipboard.writeText(link).then(function() {{
      var m = document.getElementById('qrCopyMsg');
      m.style.display='block';
      setTimeout(function(){{m.style.display='none';}}, 2000);
    }});
  }}
}}

function rodarDiag() {{
  var user = document.getElementById('diagUser').value;
  var res  = document.getElementById('diagResult');
  res.innerHTML = '<span style="color:#3b82f6">⏳ Analisando...</span>';
  fetch('/debug/canais?user=' + encodeURIComponent(user), {{credentials:'include'}})
    .then(r => r.json())
    .then(j => {{
      if (j.erro) {{ res.innerHTML = '<span style="color:#ef4444">❌ ' + j.erro + '</span>'; return; }}
      var html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';

      // Contagem por canal
      html += '<div><div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Canais detectados</div>';
      var canais = Object.entries(j.contagem_canais || {{}}).sort((a,b)=>b[1]-a[1]);
      canais.forEach(([c,n])=>{{
        var pct = Math.round(n/j.total_leads*100);
        html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #1d274030">';
        html += '<span style="color:#94a3b8">' + c + '</span>';
        html += '<span style="color:#eaeef7;font-weight:700">' + n + ' <span style="color:#64748b;font-size:10px">(' + pct + '%)</span></span>';
        html += '</div>';
      }});
      html += '</div>';

      // Funis x canal
      html += '<div><div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Funis → Canais</div>';
      (j.funis_com_canais || []).slice(0,10).forEach(function(f){{
        var outros = f.canais.length===1 && f.canais[0]==='Outros';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #1d274030">';
        html += '<span style="color:' + (outros?'#f59e0b':'#94a3b8') + ';font-size:11px">' + f.funil.substring(0,22) + '</span>';
        html += '<span style="color:#eaeef7;font-size:11px"><b>' + f.total + '</b> · ' + f.canais.join(', ') + '</span>';
        html += '</div>';
      }});
      html += '</div>';

      html += '</div>';
      if (j.total_outros > 0) {{
        html += '<div style="margin-top:12px;padding:10px;background:#1a1000;border:1px solid #f59e0b44;border-radius:8px;font-size:12px;color:#f59e0b">';
        html += '⚠ <b>' + j.total_outros + ' leads</b> ainda em "Outros". ';
        html += j.dica;
        html += '</div>';
      }} else {{
        html += '<div style="margin-top:12px;padding:10px;background:#001a0a;border:1px solid #22c55e44;border-radius:8px;font-size:12px;color:#22c55e">✅ Todos os leads estão com canal identificado!</div>';
      }}
      res.innerHTML = html;
    }})
    .catch(()=>{{ res.innerHTML='<span style="color:#ef4444">❌ Erro de conexão</span>'; }});
}}
</script>
"""

    def render_admin(self, msg=""):
        users = load_users()
        if not users:
            clientes_html = '<div class="empty">Nenhum cliente cadastrado ainda.</div>'
        else:
            parts = []
            for uname, ud in users.items():
                ativo = ud.get("ativo", True)
                valido_trial, dias_rest, expirado_trial = trial_info(ud)
                ativado = ud.get("ativado", True)  # True = pago, False = trial/expirado

                # Status badge
                if not ativo:
                    status_cls, status_txt, card_cls = "bloqueado", "Bloqueado", "bloqueado"
                elif expirado_trial:
                    status_cls, status_txt, card_cls = "bloqueado", "Trial expirado", "bloqueado"
                elif not ativado and dias_rest is not None:
                    status_cls, status_txt, card_cls = "trial", f"Trial · {dias_rest}d restante{'s' if dias_rest!=1 else ''}", ""
                else:
                    status_cls, status_txt, card_cls = "ativo", "Ativo", ""

                btn_toggle = (
                    f'<form method="POST" action="/admin/block" style="display:inline">'
                    f'<input type="hidden" name="username" value="{uname}">'
                    f'<button class="btn-sm btn-block" type="submit">Bloquear</button></form>'
                ) if ativo else (
                    f'<form method="POST" action="/admin/unblock" style="display:inline">'
                    f'<input type="hidden" name="username" value="{uname}">'
                    f'<button class="btn-sm btn-unblock" type="submit">Desbloquear</button></form>'
                )

                btn_ativar = "" if ativado else (
                    f'<form method="POST" action="/admin/ativar" style="display:inline">'
                    f'<input type="hidden" name="username" value="{uname}">'
                    f'<button class="btn-sm" style="background:#10b981;border:none;color:#fff;'
                    f'cursor:pointer;font-size:12px;padding:5px 12px;border-radius:6px;font-weight:700" '
                    f'type="submit">✅ Ativar</button></form>'
                )

                parts.append(
                    f'<div class="client-card {card_cls}">'
                    f'<div class="client-top"><div>'
                    f'<div class="client-name">{ud.get("nome","")}</div>'
                    f'<div class="client-user">@{uname}</div></div>'
                    f'<span class="status-badge {status_cls}">{status_txt}</span></div>'
                    '<div class="client-info">'
                    f'<b>Login:</b> {uname} &nbsp;·&nbsp; <b>Senha:</b> '
                    + (f'<span style="font-family:monospace;background:#1a2235;padding:1px 6px;border-radius:4px">{ud.get("senha_plain","—")}</span>' ) +
                    '<br><b>Kommo:</b> ' + ud.get('kommo_subdomain','') + '.kommo.com' +
                    ('<br><b>Email:</b> ' + ud.get('email','') if ud.get('email') else '') +
                    ('<br><b>Link cartao:</b> <a href="' + ud.get('asaas_payment_link','') + '" target="_blank" style="color:#3b82f6">abrir link</a>' if ud.get('asaas_payment_link') else '<br><b>Link cartao:</b> <span style="color:#6b7280">não definido</span>') +
                    (f'<br><form method="POST" action="/admin/salvar_link" style="display:flex;gap:4px;margin-top:4px;align-items:center"><input type="hidden" name="username" value="{uname}"><input type="url" name="link" placeholder="https://www.asaas.com/c/..." value="{ud.get("asaas_payment_link","")}" style="font-size:11px;padding:3px 6px;border:1px solid #374151;background:#1f2937;color:#fff;border-radius:4px;width:220px"><button type="submit" style="background:#3b82f6;border:none;color:#fff;cursor:pointer;font-size:11px;padding:3px 8px;border-radius:4px">Salvar</button></form>') +
                    f'<br><form method="POST" action="/admin/salvar_cores" style="display:flex;gap:6px;margin-top:6px;align-items:center"><input type="hidden" name="username" value="{uname}"><label style="font-size:11px;color:#8590a8">Cor 1</label><input type="color" name="cor" value="{ud.get("cor_primaria","#3b82f6")}" style="width:36px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none"><label style="font-size:11px;color:#8590a8">Cor 2</label><input type="color" name="cor2" value="{ud.get("cor_secundaria","#1e3a8a")}" style="width:36px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none"><button type="submit" style="background:#6366f1;border:none;color:#fff;cursor:pointer;font-size:11px;padding:3px 8px;border-radius:4px">Aplicar cores</button></form>' +
                    '</div>'
                    f'<div class="client-actions">'
                    f'<a href="/admin/view/{uname}" class="btn-sm btn-view">Ver painel</a>'
                    f'{btn_ativar}'
                    f'{btn_toggle}'
                    f'<form method="POST" action="/admin/delete" style="display:inline" onsubmit="return confirm(\'Deletar {uname}?\')">'
                    f'<input type="hidden" name="username" value="{uname}">'
                    f'<button class="btn-sm btn-del" type="submit">Excluir</button></form>'
                    f'</div></div>'
                )
            clientes_html = "\n".join(parts)
        email_tab_html = self._build_email_tab(users)
        canal_tab_html = self._build_canal_tab(users)
        corretores_tab_html = self._build_corretores_tab()
        html = ADMIN_HTML.format(
            clientes_html=clientes_html,
            msg=msg,
            msg_display="block" if msg else "none"
        ).replace("__EMAIL_TAB__", email_tab_html
        ).replace("__CANAL_TAB__", canal_tab_html
        ).replace("__CORRETORES_TAB__", corretores_tab_html)
        return html.encode("utf-8")



    def _build_corretores_tab(self):
        """Constroi a aba Corretores do admin."""
        corretores = load_corretores()
        parts = []
        parts.append(
            '<div class="add-form">' +
            '<h2>&#127968; Adicionar novo corretor</h2>' +
            '<form method="POST" action="/admin/corretor/add">' +
            '<div class="form-grid">' +
            '<div class="form-group"><label>Usuario (login)</label><input type="text" name="username" placeholder="ex: joao_corretor" required/></div>' +
            '<div class="form-group"><label>Senha</label><input type="password" name="password" placeholder="senha do corretor" required/></div>' +
            '<div class="form-group"><label>Nome completo</label><input type="text" name="nome" placeholder="ex: Joao Silva" required/></div>' +
            '<div class="form-group"><label>E-mail</label><input type="email" name="email" placeholder="corretor@email.com"/></div>' +
            '<div class="form-group"><label>Kommo Subdomain</label><input type="text" name="subdomain" placeholder="ex: minhaempresa" required/></div>' +
            '<div class="form-group" style="grid-column:1/-1"><label>Kommo Token</label><input type="text" name="token" placeholder="token de acesso do Kommo" required/></div>' +
                        '<div class="form-group"><label>Cor primaria</label><input type="color" name="cor" value="#f59e0b" style="width:100%;height:38px;padding:2px;border:1px solid #1d2740;border-radius:6px;background:#0f1320;cursor:pointer"/></div>' +
            '<div class="form-group"><label>Cor secundaria</label><input type="color" name="cor2" value="#d97706" style="width:100%;height:38px;padding:2px;border:1px solid #1d2740;border-radius:6px;background:#0f1320;cursor:pointer"/></div>' +
            '</div>' +
            '<button class="add-btn" type="submit">Criar corretor</button>' +
            '</form></div>'
        )
        if not corretores:
            parts.append('<div class="empty">Nenhum corretor cadastrado ainda.</div>')
        else:
            parts.append('<div class="corretores-grid">')
            for cuname, cd in corretores.items():
                ativo = cd.get("ativo", True)
                status_cls = "ativo" if ativo else "bloqueado"
                status_txt = "Ativo" if ativo else "Bloqueado"
                card_cls   = "" if ativo else " bloqueado"
                cor1 = cd.get("cor_primaria", "#f59e0b")
                cor2v = cd.get("cor_secundaria", "#d97706")
                initial = cd.get("nome", cuname)[0].upper() if cd.get("nome", cuname) else "C"
                btn_toggle = (
                    f'<form method="POST" action="/admin/corretor/block" style="display:inline">' +
                    f'<input type="hidden" name="username" value="{cuname}">' +
                    '<button class="btn-sm btn-block" type="submit">Bloquear</button></form>'
                ) if ativo else (
                    f'<form method="POST" action="/admin/corretor/unblock" style="display:inline">' +
                    f'<input type="hidden" name="username" value="{cuname}">' +
                    '<button class="btn-sm btn-unblock" type="submit">Desbloquear</button></form>'
                )
                comissao_pct = cd.get("comissao_pct", 3)
                meta_comissao = cd.get("meta_comissao", 5000)
                parts.append(
                    f'<div class="cor-card{card_cls}">' +
                    '<div class="client-top">' +
                    f'<div style="display:flex;align-items:center;gap:12px">' +
                    f'<div class="cor-avatar" style="background:linear-gradient(135deg,{cor1},{cor2v})">{initial}</div>' +
                    '<div>' +
                    f'<div class="cor-name">{cd.get("nome","")}</div>' +
                    f'<div class="cor-user">@{cuname}</div>' +
                    '</div></div>' +
                    f'<span class="status-badge {status_cls}">{status_txt}</span>' +
                    '</div>' +
                    '<div class="cor-info">' +
                    f'<b>Kommo:</b> {cd.get("kommo_subdomain","")}.kommo.com' +
                    ('<br><b>Email:</b> ' + cd.get("email","") if cd.get("email") else '') +
                    '</div>' +
                    '<div class="client-actions">' +
                    f'<a href="/corretor/view/{cuname}" class="btn-sm btn-view" target="_blank">Ver painel</a>' +
                    btn_toggle +
                    f'<form method="POST" action="/admin/corretor/delete" style="display:inline" onsubmit="return confirm(&quot;Deletar {cuname}?&quot;)">' +
                    f'<input type="hidden" name="username" value="{cuname}">' +
                    '<button class="btn-sm btn-del" type="submit">Excluir</button>' +
                    '</form></div></div>'
                )
            parts.append('</div>')
        return "\n".join(parts)

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

        if path == "/manifest.json":
            import json as _json
            # Manifest personalizado por cliente — usa sessao para pegar nome/cor
            app_name = "Painel CRM"
            app_short = "CRM"
            theme_color = "#1e3a8a"
            start_url = "/painel"
            if sess and not sess.get("is_admin"):
                uname = sess["user"]
                users_m = load_users()
                ud_m = users_m.get(uname, {})
                nome_emp = ud_m.get("nome", "") or uname
                cor_p = ud_m.get("cor_primaria", "#1e3a8a") or "#1e3a8a"
                app_name = nome_emp + " · CRM"
                app_short = nome_emp[:12] if len(nome_emp) <= 12 else nome_emp[:10] + "…"
                theme_color = cor_p
                start_url = "/painel"
            manifest = {
                "name": app_name,
                "short_name": app_short,
                "description": "Painel CRM em tempo real",
                "start_url": start_url,
                "display": "standalone",
                "background_color": "#07090f",
                "theme_color": theme_color,
                "icons": [
                    {"src": "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/1f4ca.png", "sizes": "72x72", "type": "image/png"},
                    {"src": "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/1f4ca.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/1f4ca.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
                ]
            }
            body = _json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/sw.js":
            sw = """self.addEventListener('push',function(e){
  const d=e.data?e.data.json():{title:'Painel CRM',body:'Nova notificacao'};
  e.waitUntil(self.registration.showNotification(d.title||'Painel CRM',{body:d.body||'',icon:'/favicon.ico',badge:'/favicon.ico'}));
});
self.addEventListener('notificationclick',function(e){
  e.notification.close();
  e.waitUntil(clients.openWindow('/painel'));
});
self.addEventListener('install',function(e){self.skipWaiting();});
self.addEventListener('activate',function(e){e.waitUntil(clients.claim());});
"""
            body = sw.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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

        # ── Email management API (admin only) ──────────────────────
        if path == "/api/admin/emails":
            if not sess or not sess["is_admin"]: return self.send_json(403, {"erro": "acesso negado"})
            with _email_log_lock:
                log_copy = list(_email_log)
            safe = [{k: v for k, v in e.items() if k != "preview"} for e in log_copy]
            self.send_json(200, {"log": safe, "total": len(safe)})
            return

        if path == "/api/admin/emails/preview":
            if not sess or not sess["is_admin"]: return self.send_json(403, {"erro": "acesso negado"})
            qs   = self.path.split("?", 1)[1] if "?" in self.path else ""
            idx  = None
            for part in qs.split("&"):
                if part.startswith("idx="):
                    try: idx = int(part[4:])
                    except: pass
            with _email_log_lock:
                log_copy = list(_email_log)
            if idx is None or idx >= len(log_copy):
                return self.send_json(404, {"erro": "nao encontrado"})
            entry = log_copy[idx]
            body  = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
            <title>Preview — {entry['assunto']}</title>
            <style>body{{background:#0a0f1e;padding:24px;font-family:Arial,sans-serif}}
            .meta{{color:#64748b;font-size:12px;margin-bottom:16px;line-height:1.8}}
            .meta b{{color:#94a3b8}}</style></head><body>
            <div class="meta">
              <b>Assunto:</b> {entry['assunto']}<br>
              <b>Para:</b> {entry['destinatario']}<br>
              <b>Enviado em:</b> {entry['ts']}<br>
              <b>Tipo:</b> {entry['tipo']}<br>
              <b>Status:</b> {entry['status']}{'  — ' + entry['erro'] if entry['erro'] else ''}
            </div>
            {entry['preview']}
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/admin/emails":
            # Email management now embedded in /admin (tab Emails)
            return self.redirect("/admin")

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

        # Corretor: ver painel individual
        if path.startswith("/corretor/view/"):
            if not sess: return self.redirect("/login")
            cuname = path.replace("/corretor/view/", "").strip("/")
            if not sess["is_admin"] and sess["user"] != cuname:
                return self.redirect("/login")
            corretores = load_corretores()
            if cuname not in corretores: return self.redirect("/admin")
            cd = corretores[cuname]
            cor1 = cd.get("cor_primaria", "#f59e0b")
            cor2v = cd.get("cor_secundaria", "#d97706")
            page = (_load_corretor_html()
                .replace("__CNOME__", cd.get("nome", cuname))
                .replace("__CCOR__", cor1)
                .replace("__CCOR2__", cor2v)
                .replace("__CPCT__", "0")
                .replace("__CMETA__", "0")
            )
            self.send_html(200, page.encode("utf-8"))
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
            # Se for corretor, redireciona para painel do corretor
            if not ud:
                corretores = load_corretores()
                if uname in corretores:
                    return self.redirect(f"/corretor/view/{uname}")
            if not ud.get("ativo", True):
                self.send_html(403, _html_trial_bloqueado("Acesso suspenso", "Sua conta foi suspensa. Entre em contato para regularizar."))
                return
            # Verificação de trial
            valido, dias_rest, expirado = trial_info(ud)
            if expirado:
                self.send_html(403, _html_trial_bloqueado(
                    "Período de teste encerrado",
                    "Seu teste gratuito de 14 dias expirou. Entre em contato para ativar o acesso completo."
                ))
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
                    if not ud2.get("kommo_token"):
                        corretores = load_corretores()
                        ud2 = corretores.get(cache_key, ud2)
                    if not ud2.get("kommo_subdomain") or not ud2.get("kommo_token"):
                        with lock:
                            cache["dados"] = {"leads": [], "funis": [], "status_map": {}}
                            cache["status"] = "ok"
                            cache["atualizado_em"] = datetime.now().isoformat()
                            cache["erro"] = "Credenciais Kommo nao configuradas."
                    else:
                        threading.Thread(
                            target=buscar_kommo_user,
                            args=(cache_key, ud2.get("kommo_subdomain",""), ud2.get("kommo_token","")),
                            daemon=True
                        ).start()
            # Retorna imediatamente — frontend faz polling se status='atualizando'
            # (sleep bloqueante removido para não travar health check do Render)
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
            if sess["is_admin"]:
                threading.Thread(
                    target=buscar_kommo_user,
                    args=("__admin__", CFG["subdomain"], CFG["token"]),
                    daemon=True
                ).start()
            else:
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
            target_qs   = qs.get("user",        [None])[0]
            filtro_vend = qs.get("vendedor",     [None])[0]  # filtro por tag de vendedor
            vendor_tags_raw = qs.get("vendor_tags", [None])[0]  # tags configuradas no front
            # Processa lista de vendor_tags
            vendor_tags = []
            if vendor_tags_raw:
                vendor_tags = [t.strip().upper() for t in vendor_tags_raw.split(",") if t.strip()]
            if sess["is_admin"] and target_qs:
                cache_key = target_qs
            elif sess["is_admin"]:
                cache_key = "__admin__"
            else:
                cache_key = sess["user"]
            cache, lock = get_cache(cache_key)
            with lock:
                dados_cache = dict(cache.get("dados") or {})
            leads_raw = dados_cache.get("leads") or []
            if not leads_raw:
                return self.send_json(200, {"erro": "sem_dados", "msg": "Ainda sem dados — aguarde o carregamento do painel."})
            # Filtrar por tag de vendedor se solicitado
            if filtro_vend:
                fv = filtro_vend.upper().strip()
                def _lead_tem_tag(l, tag):
                    tags_str = (l.get("tags") or "").upper()
                    return any(p.strip() == tag for p in tags_str.split(",") if p.strip())
                filtrado = [l for l in leads_raw if _lead_tem_tag(l, fv)]
                if filtrado:
                    dados_cache = dict(dados_cache)
                    dados_cache["leads"] = filtrado
                else:
                    return self.send_json(200, {"erro": "sem_dados", "msg": f"Nenhum lead encontrado com a tag '{filtro_vend}'."})
            meta_val  = dados_cache.get("meta_mes") or CFG["meta_mes_padrao"]
            mes_param = qs.get("mes", [None])[0]  # mês selecionado pelo usuário
            if mes_param and re.match(r"^\d{4}-\d{2}$", mes_param):
                mes_atual = mes_param
            else:
                mes_atual = datetime.now().strftime("%Y-%m")
            try:
                stats = compute_raiox_stats(
                    dados_cache, mes_atual, meta=meta_val,
                    vendor_tags=vendor_tags,
                    filtro_vendedor=filtro_vend or ""
                )
            except Exception as e:
                import traceback
                print(f"[RAIOX] Erro em compute_raiox_stats: {e}\n{traceback.format_exc()}")
                return self.send_json(500, {"erro": "stats_error", "msg": f"Erro ao calcular estatísticas: {e}"})
            # Modo stats_only: retorna só os dados, sem chamar Claude (usado na comparação de meses)
            stats_only = qs.get("stats_only", [None])[0] == "1"
            if stats_only:
                return self.send_json(200, {"ok": True, "stats": stats, "filtro": filtro_vend or ""})
            if not ANTHROPIC_KEY:
                return self.send_json(200, {"erro": "sem_chave", "msg": "Variável ANTHROPIC_KEY não configurada no servidor.", "stats": stats})
            try:
                analise = claude_raiox(stats)
            except Exception as e:
                return self.send_json(200, {"erro": "api_error", "msg": f"Erro Claude API: {e}", "stats": stats})
            if analise is None:
                return self.send_json(200, {"erro": "api_error", "msg": "Resposta vazia da API. Tente novamente.", "stats": stats})
            return self.send_json(200, {"ok": True, "stats": stats, "analise": analise, "filtro": filtro_vend or ""})

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
            qs = parse_qs(parsed.query)
            target = qs.get("user", ["__admin__"])[0]
            cache, lock = get_cache(target)
            with lock:
                dados = cache.get("dados")
            if not dados:
                return self.send_json(200, {"erro": "sem dados em cache — acesse o painel primeiro"})
            leads_all = dados.get("leads") or []
            outros = [l for l in leads_all if l.get("canal") == "Outros"]
            amostra = []
            for l in outros[:40]:
                amostra.append({
                    "id":    l.get("id"),
                    "nome":  l.get("cliente",""),
                    "tags":  l.get("tags",""),
                    "canal": l.get("canal",""),
                    "funil": l.get("funil",""),
                })
            contagem = {}
            funil_contagem = {}
            for l in leads_all:
                c = l.get("canal","Outros")
                f = l.get("funil","?")
                contagem[c] = contagem.get(c, 0) + 1
                funil_contagem[f] = funil_contagem.get(f, 0) + 1
            # Dica de configuração por funil
            funil_dica = []
            for funil, count in sorted(funil_contagem.items(), key=lambda x: -x[1]):
                c_leads = [l.get("canal","Outros") for l in leads_all if l.get("funil") == funil]
                c_set = list(set(c_leads))
                funil_dica.append({"funil": funil, "total": count, "canais": c_set})
            return self.send_json(200, {
                "total_leads":       len(leads_all),
                "total_outros":      len(outros),
                "contagem_canais":   contagem,
                "funis_com_canais":  funil_dica,
                "amostra_outros":    amostra,
                "dica": "Se um funil tem só 'Outros', use o mapeamento Funil→Canal no painel ou adicione tags nos leads do Kommo.",
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

        if path == "/api/admin/emails/send":
            if not sess or not sess["is_admin"]: return self.send_json(403, {"erro": "acesso negado"})
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b"{}"
            try: payload = json.loads(body)
            except: payload = {}
            tipo     = payload.get("tipo", "")
            username = payload.get("username", "") or None  # None = todos os clientes
            alvo_txt = f"para @{username}" if username else "para todos os clientes"
            if tipo == "manha":
                threading.Thread(
                    target=enviar_relatorio_manha,
                    kwargs={"only_user": username},
                    daemon=True
                ).start()
                self.send_json(200, {"ok": True, "msg": f"☀️ Relatório de ontem enviando {alvo_txt}..."})
            elif tipo == "semana":
                threading.Thread(
                    target=enviar_relatorio_noite,
                    kwargs={"only_user": username},
                    daemon=True
                ).start()
                self.send_json(200, {"ok": True, "msg": f"📅 Relatório da semana enviando {alvo_txt}..."})
            else:
                self.send_json(400, {"erro": "tipo invalido. Use: manha ou semana"})
            return

        if path == "/login":
            form = self.parse_form()
            uname = form.get("username", "").strip()
            passwd = form.get("password", "").strip()

            # Admin master
            if uname == CFG["admin_user"] and check_admin_pass(passwd):
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
                cache, _ = get_cache(uname)
                if cache["dados"] is None:
                    threading.Thread(
                        target=buscar_kommo_user,
                        args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                        daemon=True
                    ).start()
                return

            # Corretor
            import hashlib as _hl
            corretores = load_corretores()
            cd = corretores.get(uname)
            if cd and cd.get("password_hash") == _hl.sha256(passwd.encode()).hexdigest():
                if not cd.get("ativo", True):
                    self.send_html(200, self.render_login(
                        error="Seu acesso está suspenso. Entre em contato com o administrador.",
                        cor=CFG["cor_primaria"]
                    ))
                    return
                new_token = create_session(uname, is_admin=False)
                self.send_response(302)
                self.set_cookie("crm_session", new_token)
                self.send_header("Location", f"/corretor/view/{uname}")
                self.end_headers()
                cache, _ = get_cache(uname)
                if cache["dados"] is None:
                    threading.Thread(
                        target=buscar_kommo_user,
                        args=(uname, cd.get("kommo_subdomain",""), cd.get("kommo_token","")),
                        daemon=True
                    ).start()
                return

            self.send_html(200, self.render_login(
                error="Usuário ou senha incorretos.",
                cor=CFG["cor_primaria"]
            ))
            return

        # Admin: salvar link de pagamento Asaas
        if path == "/admin/salvar_link":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","").strip()
            link  = form.get("link","").strip()
            users = load_users()
            if uname in users:
                users[uname]["asaas_payment_link"] = link
                save_users(users)
            self.send_html(200, self.render_admin(f"✅ Link de pagamento de '{uname}' atualizado."))
            return

        # Admin: salvar cores
        if path == "/admin/salvar_cores":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","").strip()
            users = load_users()
            if uname in users:
                if form.get("cor"):
                    users[uname]["cor_primaria"] = form.get("cor","")
                if form.get("cor2"):
                    users[uname]["cor_secundaria"] = form.get("cor2","")
                save_users(users)
            self.send_html(200, self.render_admin(f"🎨 Cores de '{uname}' atualizadas."))
            return

        # Admin: adicionar cliente
        if path == "/admin/add":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form, files = self.parse_multipart()
            uname = form.get("username","").strip().lower().replace(" ","_")
            if not uname:
                self.send_html(200, self.render_admin("❌ Usuário inválido."))
                return
            users = load_users()
            logo_url = ""
            if "logo_file" in files:
                f_obj = files["logo_file"]
                if f_obj.filename:
                    import mimetypes
                    ext = Path(f_obj.filename).suffix.lower() or ".jpg"
                    logos_dir = DATA_DIR / "logos"
                    logos_dir.mkdir(parents=True, exist_ok=True)
                    fname = f"{uname}{ext}"
                    (logos_dir / fname).write_bytes(f_obj.file.read())
                    logo_url = f"/img/{fname}"
            _pwd = form.get("password","123456")
            users[uname] = {
                "password_hash":    hash_pass(_pwd),
                "senha_plain":      _pwd,
                "ativo":            True,
                "ativado":          False,
                "trial_inicio":     br_now().isoformat(),
                "nome":             form.get("nome",""),
                "email":            form.get("email",""),
                "kommo_subdomain":  form.get("subdomain",""),
                "kommo_token":      form.get("token",""),
                "cor_primaria":     form.get("cor","") or CFG["cor_primaria"],
                "cor_secundaria":   form.get("cor2","") or CFG.get("cor_secundaria","#1e3a8a"),
                "logo_url":         logo_url,
                "asaas_payment_link": "",
            }
            save_users(users)
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

        # Admin: ativar (pós-pagamento — encerra trial e libera acesso permanente)
        if path == "/admin/ativar":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = form.get("username","")
            users = load_users()
            if uname in users:
                users[uname]["ativado"]      = True
                users[uname]["ativo"]        = True
                users[uname]["trial_inicio"] = users[uname].get("trial_inicio", br_now().isoformat())
                save_users(users)
                self.send_html(200, self.render_admin(f"✅ Cliente '{uname}' ativado com sucesso!"))
            else:
                self.send_html(200, self.render_admin(f"❌ Cliente '{uname}' não encontrado."))
            return

        # Admin: trocar senha
        if path == "/admin/trocar_senha":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form       = self.parse_form()
            senha_atual = form.get("senha_atual", "").strip()
            nova        = form.get("nova_senha", "").strip()
            confirma    = form.get("confirma_senha", "").strip()
            if not check_admin_pass(senha_atual):
                self.send_html(200, self.render_admin("❌ Senha atual incorreta."))
                return
            if len(nova) < 6:
                self.send_html(200, self.render_admin("❌ Nova senha precisa ter pelo menos 6 caracteres."))
                return
            if nova != confirma:
                self.send_html(200, self.render_admin("❌ As senhas não coincidem."))
                return
            set_admin_pass(nova)
            self.send_html(200, self.render_admin("✅ Senha alterada com sucesso!"))
            return

        # Admin: criar conta demo (copia credenciais do admin)
        if path == "/admin/criar_demo":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form  = self.parse_form()
            uname = sanitize_username(form.get("username", "demo"))
            senha = form.get("password", "").strip()
            if not uname:
                self.send_html(200, self.render_admin("❌ Nome de usuário inválido.")); return
            if len(senha) < 4:
                self.send_html(200, self.render_admin("❌ Senha precisa ter pelo menos 4 caracteres.")); return
            users = load_users()
            if uname in users:
                self.send_html(200, self.render_admin(f"❌ Usuário '{uname}' já existe.")); return
            users[uname] = {
                "password_hash":   hash_pass(senha),
                "senha_plain":     senha,
                "ativo":           True,
                "ativado":         True,   # demo não entra em trial
                "nome":            form.get("nome", "Demo"),
                "email":           "",
                "kommo_subdomain": CFG.get("subdomain", ""),
                "kommo_token":     CFG.get("token", ""),
                "cor_primaria":    CFG.get("cor_primaria", "#3b82f6"),
                "cor_secundaria":  CFG.get("cor_secundaria", "#1e3a8a"),
                "logo_url":        CFG.get("logo_url", ""),
                "asaas_payment_link": "",
                "is_demo":         True,
            }
            save_users(users)
            self.send_html(200, self.render_admin(
                f"✅ Conta demo criada! Login: <b>{uname}</b> / Senha: <b>{senha}</b> — "
                f"Para revogar acesso, clique em Bloquear na aba Clientes."
            ))
            return

        # Corretor: adicionar
        if path == "/admin/corretor/add":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form = self.parse_form()
            cuname = sanitize_username(form.get("username",""))
            if not cuname:
                self.send_html(200, self.render_admin("❌ Username invalido."))
                return
            corretores = load_corretores()
            if cuname in corretores:
                self.send_html(200, self.render_admin(f"❌ Corretor '{cuname}' ja existe."))
                return
            pwd = form.get("password","")
            if not pwd:
                self.send_html(200, self.render_admin("❌ Senha obrigatoria."))
                return
            import hashlib
            corretores[cuname] = {
                "password_hash": hashlib.sha256(pwd.encode()).hexdigest(),
                "nome": form.get("nome", cuname),
                "email": form.get("email",""),
                "kommo_subdomain": form.get("subdomain",""),
                "kommo_token": form.get("token",""),
                "cor_primaria": form.get("cor","#f59e0b"),
                "cor_secundaria": form.get("cor2","#d97706"),
                "ativo": True,
            }
            save_corretores(corretores)
            self.send_html(200, self.render_admin(f"✅ Corretor '{cuname}' criado com sucesso!"))
            return

        # Corretor: bloquear
        if path == "/admin/corretor/block":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form   = self.parse_form()
            cuname = form.get("username","")
            corretores = load_corretores()
            if cuname in corretores:
                corretores[cuname]["ativo"] = False
                save_corretores(corretores)
            self.send_html(200, self.render_admin(f"🔒 Corretor '{cuname}' bloqueado."))
            return

        # Corretor: desbloquear
        if path == "/admin/corretor/unblock":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form   = self.parse_form()
            cuname = form.get("username","")
            corretores = load_corretores()
            if cuname in corretores:
                corretores[cuname]["ativo"] = True
                save_corretores(corretores)
            self.send_html(200, self.render_admin(f"🔓 Corretor '{cuname}' desbloqueado."))
            return

        # Corretor: deletar
        if path == "/admin/corretor/delete":
            if not sess or not sess["is_admin"]: return self.redirect("/login")
            form   = self.parse_form()
            cuname = form.get("username","")
            corretores = load_corretores()
            if cuname in corretores:
                del corretores[cuname]
                save_corretores(corretores)
            self.send_html(200, self.render_admin(f"🗑 Corretor '{cuname}' removido."))
            return

        # Admin: deletar cliente
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

# ══════════════════════════  ENTRY POINT  ══════════════════════════════════

if __name__ == "__main__":
    print("=" * 52)
    print(f"  PAINEL CRM v4 — Multi-cliente + Login")
    print("=" * 52)
    print(f"  Admin : {CFG['admin_user']} / {CFG['admin_pass']}")
    print(f"  URL   : http://localhost:{CFG['porta']}")
    print(f"  Refresh: a cada {CFG['refresh_minutos']} min")
    print("=" * 52)

    _load_email_log()
    load_users()
    load_corretores()
    users = get_users()
    for uname, ud in users.items():
        if ud.get("ativo", True) and ud.get("kommo_token"):
            threading.Thread(
                target=buscar_kommo_user,
                args=(uname, ud.get("kommo_subdomain",""), ud.get("kommo_token","")),
                daemon=True
            ).start()

    threading.Thread(target=cleanup_sessions,   daemon=True).start()
    threading.Thread(target=auto_refresh,       daemon=True).start()
    threading.Thread(target=auto_raiox_mensal,  daemon=True).start()
    threading.Thread(target=auto_alertas_email, daemon=True).start()
    if ALERT_EMAIL or GMAIL_USER:
        print(f"  Alertas: email habilitado -> {ALERT_EMAIL or GMAIL_USER}")
    else:
        print("  Alertas: configure ALERT_EMAIL e GMAIL_USER/GMAIL_PASS para emails automaticos")
    server = HTTPServer(("0.0.0.0", CFG["porta"]), Handler)
    print("\n  Servidor rodando em http://localhost:" + str(CFG["porta"]) + "\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
