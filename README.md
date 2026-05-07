# 📊 Painel CRM Kommo — v3 White Label

Painel de vendas em tempo real integrado ao Kommo CRM.
Funciona 100% online via Railway — sem instalar nada no computador do cliente.

---

## 🚀 Deploy no Railway (gratuito, 5 minutos)

### 1. Criar conta
Acesse https://railway.app e crie uma conta gratuita com GitHub.

### 2. Subir o projeto
1. Acesse https://railway.app/new
2. Escolha **"Deploy from GitHub repo"**
3. Faça upload desta pasta como repositório **OU** use **"Deploy from local"**

> **Dica rápida:** Compacte esta pasta em .zip, suba no GitHub e conecte ao Railway.

### 3. Configurar as variáveis de ambiente
No painel do Railway, vá em **Variables** e adicione:

| Variável           | Valor                        |
|--------------------|------------------------------|
| `KOMMO_SUBDOMAIN`  | ex: `nomeempresa`            |
| `KOMMO_TOKEN`      | Token do Kommo do cliente    |
| `EMPRESA_NOME`     | ex: `Valentim Imóveis`       |
| `COR_PRIMARIA`     | ex: `#3b82f6` (azul padrão)  |
| `COR_SECUNDARIA`   | ex: `#1e3a8a`                |
| `META_MES`         | ex: `50000`                  |
| `EMPRESA_LOGO`     | URL da logo (opcional)       |

### 4. Pronto!
O Railway gera um link tipo:
```
https://seu-painel.railway.app
```
Compartilhe com o cliente — abre em qualquer navegador, sem instalar nada.

---

## 🎨 White Label — Como personalizar por cliente

### Opção A: Variáveis de ambiente (recomendado para Railway)
Configure as variáveis acima no painel do Railway.
Cada projeto Railway = um cliente diferente.

### Opção B: Editar config.json (para uso local)
```json
{
  "empresa": {
    "nome": "Nome da Empresa",
    "logo_url": "https://link-da-logo.com/logo.png",
    "cor_primaria": "#3b82f6",
    "cor_secundaria": "#1e3a8a"
  },
  "kommo": {
    "subdomain": "subdominio-do-cliente",
    "token": "token-do-cliente"
  },
  "painel": {
    "meta_mes_padrao": 50000,
    "refresh_minutos": 30
  }
}
```

---

## 🔑 Como gerar o token do Kommo do cliente

1. Acesse: `https://SUBDOMINIO.kommo.com/settings/oauth`
2. Crie um novo token de longa duração
3. Copie e cole na variável `KOMMO_TOKEN`

---

## 💻 Rodar localmente (opcional)

```bash
python servidor_v3.py
# Acesse: http://localhost:8080
```

Ou dê duplo clique no **abrir_painel.bat** (Windows).

---

## 📁 Estrutura dos arquivos

```
painel_crm/
├── servidor_v3.py          ← Servidor principal (use este)
├── painel_servidor_v2.html ← Interface do painel
├── config.json             ← Configurações white label
├── crm_cache.json          ← Cache dos dados (gerado automaticamente)
├── Procfile                ← Para Railway/Heroku
├── runtime.txt             ← Versão do Python
├── requirements.txt        ← Dependências (nenhuma externa necessária)
└── abrir_painel.bat        ← Atalho Windows (uso local)
```

---

## 💰 Modelo de negócio sugerido

| Plano     | O que inclui                          | Preço sugerido |
|-----------|---------------------------------------|----------------|
| Starter   | Painel online + 1 funil               | R$ 197/mês     |
| Pro       | Painel + white label + suporte        | R$ 397/mês     |
| Enterprise| Multi-funil + treinamento da equipe   | R$ 797/mês     |

---

Dúvidas? Entre em contato.
