"""Monitora o preço de GTA 6 e EA Sports FC 27 (PS5, mídia física) e avisa no iPhone quando cai.

Roda a cada 3 horas pelo GitHub Actions (.github/workflows/games.yml).
Fonte: busca do Buscapé, que junta as ofertas de várias lojas. Só lojas confiáveis entram.
Usa as mesmas notificações do app Feriado a Dois (VAPID_PRIVATE_KEY e PUSH_SUBSCRIPTIONS).
"""
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_prices import notificar, brl  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "games.json"
BRT = timezone(timedelta(hours=-3))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"

JOGOS = {
    "gta6": {"nome": "GTA 6", "busca": "gta 6 ps5", "alvo": 330,
             "casa": r"(grand theft auto vi\b|gta ?(vi|6)\b)", "faixa": (150, 700)},
    "fc27": {"nome": "EA Sports FC 27", "busca": "fc 27 ps5", "alvo": 250,
             "casa": r"\bfc ?27\b", "faixa": (100, 600)},
}
# Fora: acessórios, consoles, outras plataformas e edições de luxo que distorcem o menor preço.
EXCLUI = re.compile(r"capa|case|controle|console|playstation 5 \d|ps4\b|xbox|switch|pc\b|steam|camiseta|poster|"
                    r"chaveiro|caneca|adesivo|skin|ultimate|deluxe|colecionador|collector|bundle|kit|pontos|points|moedas",
                    re.I)
LOJAS = ["amazon", "kabum", "magazine luiza", "magalu", "fast shop", "mercado livre", "carrefour", "casas bahia",
         "ponto", "extra", "americanas", "submarino", "shoptime", "playstation", "sony", "girafa", "nagem", "pichau",
         "terabyte", "saraiva", "walmart", "fnac", "nuuvem", "gamestop", "ri happy", "leroy"]
QUEDA_MIN_RS = 10   # novo menor preço só avisa se cair pelo menos R$ 10
REALERTA_RS = 10


def buscar(jogo):
    r = requests.get("https://www.buscape.com.br/search", params={"q": jogo["busca"]},
                     headers={"User-Agent": UA, "Accept-Language": "pt-BR"}, timeout=40)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("página do Buscapé sem dados (layout mudou ou bloqueio)")
    hits = json.loads(m.group(1))["props"]["initialReduxState"]["hits"]["hits"]
    lo, hi = jogo["faixa"]
    ofertas = []
    for h in hits:
        nome = h.get("name") or ""
        preco = h.get("price")
        loja = h.get("merchantName") or (h.get("bestOffer") or {}).get("merchantName") or ""
        if not isinstance(preco, (int, float)) or not lo <= preco <= hi:
            continue
        if not re.search(jogo["casa"], nome, re.I) or EXCLUI.search(nome):
            continue
        if not any(l in loja.lower() for l in LOJAS):
            continue
        ofertas.append({"loja": loja, "preco": round(preco, 2), "nome": nome[:90],
                        "url": "https://www.buscape.com.br" + h.get("url", ""), "lojas": h.get("storeCount") or 1})
    ofertas.sort(key=lambda o: o["preco"])
    return ofertas


def main():
    dados = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"jogos": {}}
    agora = datetime.now(BRT)
    avisos = []
    for jid, jogo in JOGOS.items():
        antes = dados["jogos"].get(jid, {})
        try:
            ofertas = buscar(jogo)
        except Exception as e:
            print(f"{jid}: erro na busca: {e}")
            continue
        if not ofertas:
            print(f"{jid}: nenhuma oferta válida")
            continue
        melhor = ofertas[0]
        p = melhor["preco"]
        minimo = antes.get("min")
        ultimo = antes.get("ultimo_alerta")
        motivos = []
        if minimo is not None and p <= minimo - QUEDA_MIN_RS:
            motivos.append(f"menor preço já visto (antes {brl(minimo)})")
        if p <= jogo["alvo"] and (ultimo is None or ultimo > jogo["alvo"]):
            motivos.append(f"abaixo do seu alvo de {brl(jogo['alvo'])}")
        texto = None
        if motivos and (ultimo is None or p <= ultimo - REALERTA_RS):
            texto = f"{brl(p)} na {melhor['loja']}. " + "; ".join(motivos).capitalize().replace("r$", "R$") + "."
            avisos.append({"title": f"Baixou: {jogo['nome']}", "body": texto, "url": "./games.html#" + jid, "tag": jid})
        hist = antes.get("historico") or []
        if not hist or hist[-1][1] != p or hist[-1][0][:10] != agora.strftime("%Y-%m-%d"):
            hist = hist[-299:] + [[agora.isoformat(timespec="minutes"), p, melhor["loja"]]]
        dados["jogos"][jid] = {
            "nome": jogo["nome"], "alvo": jogo["alvo"], "preco": p, "loja": melhor["loja"], "url": melhor["url"],
            "min": min(p, minimo) if minimo is not None else p,
            "max": max(p, antes.get("max") or p),
            "ultimo_alerta": p if texto else ultimo, "aviso": texto or antes.get("aviso"),
            "ofertas": ofertas[:8], "historico": hist,
        }
        print(f"{jid}: {brl(p)} na {melhor['loja']} ({len(ofertas)} ofertas)" + (f"  >> AVISO: {texto}" if texto else ""))
    dados["atualizado"] = agora.isoformat(timespec="minutes")
    dados["fonte"] = "Buscapé (Amazon, Kabum, Magalu, Fast Shop e outras)"
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    if avisos:
        notificar(avisos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
