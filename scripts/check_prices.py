"""Confere os preços dos voos do feriado e avisa no iPhone quando aparece promoção.

Roda todo dia pelo GitHub Actions (.github/workflows/precos.yml).
Fonte: Google Voos, via SerpApi (plano grátis, 250 consultas por mês).
Ida sexta 5/fev/2027, volta terça 9/fev/2027, preço por pessoa, ida e volta.

Variáveis de ambiente (segredos do repositório):
  SERPAPI_KEY         chave do SerpApi (sem ela o script só pula a consulta)
  VAPID_PRIVATE_KEY   chave privada das notificações do app
  PUSH_SUBSCRIPTIONS  lista JSON com os códigos de ativação dos celulares
  TEST_PUSH=1         manda uma notificação de teste
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "precos.json"
SITE = "https://brunocesarg.github.io/feriado-a-dois/"
BRT = timezone(timedelta(hours=-3))
IDA, VOLTA = "2027-02-05", "2027-02-09"
TETO = 6000

# ref = preço por pessoa pesquisado em 09/10/2026; total = custo da viagem do casal com esse preço.
# Maceió e João Pessoa não estão no site (passavam do teto); ficam no radar para avisar se passarem a caber.
ROTAS = {
    "floripa":   {"nome": "Florianópolis",              "de": "CGH,GRU,VCP", "para": "FLN", "ref": 900,  "total": 6405},
    "bombinhas": {"nome": "Bombinhas (voo p/ Navegantes)", "de": "CGH,GRU,VCP", "para": "NVT", "ref": 866,  "total": 6520},
    "guarapari": {"nome": "Guarapari (voo p/ Vitória)",  "de": "GRU,CGH,VCP", "para": "VIX", "ref": 770,  "total": 6060},
    "porto":     {"nome": "Porto de Galinhas (voo p/ Recife)", "de": "GRU,CGH,VCP", "para": "REC", "ref": 1379, "total": 6960},
    "maceio":    {"nome": "Maceió",                     "de": "GRU,CGH,VCP", "para": "MCZ", "ref": 1779, "total": 7900, "radar": True},
    "jpa":       {"nome": "João Pessoa",                "de": "VCP,GRU,CGH", "para": "JPA", "ref": 1442, "total": 7200, "radar": True},
}
QUEDA_PROMO = 0.10      # 10% abaixo da referência
QUEDA_MINIMA_RS = 40    # novo menor preço só conta se cair pelo menos R$ 40
REALERTA_RS = 30        # depois de avisar, só avisa de novo se cair mais R$ 30


def brl(v):
    return "R$ " + f"{int(round(v)):,}".replace(",", ".")


def carregar():
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))
    return {"rotas": {}}


def consultar(chave, rota):
    params = {
        "engine": "google_flights", "type": "1", "adults": "1", "currency": "BRL", "hl": "pt", "gl": "br",
        "departure_id": rota["de"], "arrival_id": rota["para"],
        "outbound_date": IDA, "return_date": VOLTA, "api_key": chave,
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    r.raise_for_status()
    js = r.json()
    if js.get("error"):
        raise RuntimeError(js["error"])
    voos = [v for v in js.get("best_flights", []) + js.get("other_flights", []) if isinstance(v.get("price"), (int, float))]
    insights = js.get("price_insights", {}) or {}
    if not voos and not insights.get("lowest_price"):
        return None
    melhor = min(voos, key=lambda v: v["price"]) if voos else None
    preco = min([v["price"] for v in voos] + ([insights["lowest_price"]] if insights.get("lowest_price") else []))
    aeroporto = None
    if melhor and melhor.get("flights"):
        aeroporto = melhor["flights"][0].get("departure_airport", {}).get("id")
    return {"preco": int(preco), "nivel": insights.get("price_level"),
            "faixa_tipica": insights.get("typical_price_range"), "aeroporto": aeroporto}


def avaliar(rid, rota, atual, antes):
    """Decide se o preço novo é promoção. Devolve o texto do aviso ou None."""
    p, ref = atual["preco"], rota["ref"]
    total = rota["total"] + 2 * (p - ref)
    minimo_antes = antes.get("min", ref)
    ultimo_alerta = antes.get("ultimo_alerta")
    motivos = []
    if p <= ref * (1 - QUEDA_PROMO):
        motivos.append(f"{round((1 - p / ref) * 100)}% abaixo do preço de outubro")
    if p <= minimo_antes - QUEDA_MINIMA_RS and not rota.get("radar"):
        motivos.append("menor preço já visto")
    if atual.get("nivel") == "low" and antes.get("nivel") != "low" and not rota.get("radar"):
        motivos.append("o Google marca como preço baixo")
    if total <= TETO < rota["total"]:
        motivos.append("agora a viagem cabe nos R$ 6 mil")
    if not motivos:
        return None, total
    if ultimo_alerta is not None and p > ultimo_alerta - REALERTA_RS:
        return None, total  # já avisou por um preço parecido
    texto = (f"{brl(p)} ida e volta por pessoa (antes {brl(ref)}). "
             f"Viagem do casal: {brl(total)}. " + motivos[0][:1].upper() + "; ".join(motivos)[1:] + ".")
    return texto, total


def notificar(avisos, teste=False):
    chave = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    bruto = os.environ.get("PUSH_SUBSCRIPTIONS", "").strip()
    if not chave or not bruto:
        print("Notificação: sem chave ou sem celulares ativados; nada enviado.")
        return 0
    from pywebpush import webpush, WebPushException
    try:
        inscricoes = json.loads(bruto)
    except json.JSONDecodeError:
        print("PUSH_SUBSCRIPTIONS não é um JSON válido.")
        return 0
    if isinstance(inscricoes, dict):
        inscricoes = [inscricoes]
    if teste:
        avisos = [{"title": "Alertas do Feriado ativados", "body": "Quando um voo ficar mais barato, o aviso chega aqui.", "url": "./", "tag": "teste"}]
    enviados = 0
    for aviso in avisos:
        for sub in inscricoes:
            try:
                webpush(subscription_info=sub, data=json.dumps(aviso, ensure_ascii=False),
                        vapid_private_key=chave, vapid_claims={"sub": SITE}, ttl=86400)
                enviados += 1
            except WebPushException as e:
                print("Falha ao enviar para um celular:", getattr(e.response, "status_code", ""), str(e)[:200])
    print(f"Notificações enviadas: {enviados}")
    return enviados


def main():
    dados = carregar()
    dados.setdefault("rotas", {})
    teste = os.environ.get("TEST_PUSH") == "1"
    chave = os.environ.get("SERPAPI_KEY", "").strip()
    avisos = []
    hoje = datetime.now(BRT)

    if not chave:
        print("SERPAPI_KEY não configurada: preços não consultados.")
    else:
        for rid, rota in ROTAS.items():
            antes = dados["rotas"].get(rid, {})
            try:
                atual = consultar(chave, rota)
            except Exception as e:  # uma rota com erro não derruba as outras
                print(f"{rid}: erro na consulta: {e}")
                continue
            if not atual:
                print(f"{rid}: sem resultados")
                continue
            texto, total = avaliar(rid, rota, atual, antes)
            hist = (antes.get("historico") or [])[-89:] + [[hoje.strftime("%Y-%m-%d"), atual["preco"]]]
            reg = {
                "nome": rota["nome"], "preco": atual["preco"], "ref": rota["ref"],
                "min": min(atual["preco"], antes.get("min", rota["ref"])),
                "nivel": atual["nivel"], "faixa_tipica": atual["faixa_tipica"], "aeroporto": atual["aeroporto"],
                "total": total, "cabe": total <= TETO,
                "promo": bool(texto) or bool(antes.get("promo") and atual["preco"] <= (antes.get("ultimo_alerta") or 0)),
                "promo_texto": texto or (antes.get("promo_texto") if atual["preco"] <= (antes.get("ultimo_alerta") or 0) else None),
                "ultimo_alerta": atual["preco"] if texto else antes.get("ultimo_alerta"),
                "historico": hist,
            }
            dados["rotas"][rid] = reg
            print(f"{rid}: {brl(atual['preco'])} (ref {brl(rota['ref'])}) nível={atual['nivel']} total={brl(total)}"
                  + (f"  >> PROMOÇÃO: {texto}" if texto else ""))
            if texto:
                avisos.append({"title": f"Promoção: {rota['nome']}", "body": texto, "url": f"./#{rid}", "tag": rid})
        dados["atualizado"] = hoje.isoformat(timespec="minutes")
        dados["fonte"] = "Google Voos via SerpApi"
        dados["datas"] = {"ida": IDA, "volta": VOLTA}
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")

    if avisos or teste:
        notificar(avisos, teste=teste)
    return 0


if __name__ == "__main__":
    sys.exit(main())
