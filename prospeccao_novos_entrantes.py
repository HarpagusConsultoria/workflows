#!/usr/bin/env python3
"""
Prospeccao de novos entrantes -- funil "novos entrantes" (Harpagus Consultoria)

PARTE A da rotina descrita no documento "Prompt de Prospeccao -- Funil Novos
Entrantes (Gratuito)". Roda FORA de qualquer Claude Project -- precisa de
acesso normal a internet e de espaco em disco real (o dump mensal da Receita
Federal tem dezenas de GB). Gera uma lista CURTA e ja filtrada de candidatos
(candidatos_novos_entrantes_AAAA-MM.csv), que e o que deve ser entregue ao
Claude Project para a Parte B (cruzamento de socios, elo em comum,
qualificacao e redacao da abordagem).

AVISO IMPORTANTE:
Em 2026 a Receita Federal migrou a hospedagem dos dados abertos de CNPJ para
um novo sistema ("SERPRO+ - Repositorio de Arquivos da Receita Federal"),
baseado em Nextcloud, acessivel publicamente em:
  https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9
A antiga URL direta (.../dados/cnpj/dados_abertos_cnpj/AAAA-MM/Arquivo.zip)
NAO existe mais e retorna erro 404. O layout de pastas por mes (AAAA-MM) e os
nomes dos arquivos (Empresas0.zip, Estabelecimentos0.zip, Socios0.zip, ...)
continuam os mesmos -- confirmado manualmente em setembro/2026 dentro da
pasta "2026-09" desse repositorio, que contem: Cnaes.zip, Empresas0.zip a
Empresas9.zip, Estabelecimentos0.zip em diante, etc.

Antes de confiar 100% na automacao:

  1. Rode uma vez e confira se a pasta do mes (AAAA-MM) realmente existe com
     esse nome dentro do link acima -- a Receita pode voltar a mudar o
     padrao no futuro.
  2. Confirme a URL direta de download da lista de administradoras de
     beneficios da ANS (ADMINISTRADORAS_URL abaixo esta vazia de proposito).
  3. Confira se o layout de colunas (numero e ordem dos campos) ainda bate
     com o documentado -- a Receita já mudou esse layout no passado.

Requisitos: Python 3.9+, bibliotecas `requests` e `curl_cffi`
(pip install requests curl_cffi).

NOVO DIAGNOSTICO (setembro/2026): mesmo apontando para a URL nova e correta
(SERPRO+/Nextcloud), o download continuava falhando de dentro do GitHub
Actions com o erro "RemoteDisconnected" -- a conexao e derrubada
imediatamente, sem nenhuma resposta HTTP. Isso e a marca registrada de um
firewall/WAF que bloqueia pela "impressao digital" da conexao segura (TLS),
nao pelo cabecalho User-Agent -- por isso trocar o User-Agent nao resolveu.
Servidores do governo costumam bloquear assim conexoes vindas de bibliotecas
como `requests`/Python, mas deixam passar navegadores de verdade. A
biblioteca `curl_cffi` resolve isso "imitando" a conexao de um navegador
Chrome de verdade. Por isso o script agora tenta usar `curl_cffi` quando
disponivel (e cai de volta para `requests` normal se nao estiver instalada).
"""

import csv
import io
import re
import zipfile
import argparse
import datetime
from pathlib import Path

try:
    from curl_cffi import requests  # imita a "impressao digital" de um navegador
    _USANDO_CURL_CFFI = True
except ImportError:
    import requests
    _USANDO_CURL_CFFI = False

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

# Novo sistema de hospedagem dos dados abertos de CNPJ (SERPRO+ / Nextcloud),
# em vigor desde 2026. O "token" abaixo identifica o link publico e permanente
# de compartilhamento -- confirmado manualmente em setembro/2026.
SHARE_BASE_URL = "https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9/download"

CADOP_URL = "https://dadosabertos.ans.gov.br/FTP/PDA/operadoras_de_plano_de_saude_ativas/Relatorio_cadop.csv"

# TODO: confirmar a URL direta (CSV/planilha) da lista de administradoras de
# beneficios da ANS -- a pagina abaixo e so a versao em HTML para humanos:
# https://www.ans.gov.br/planos-de-saude-e-operadoras/contratacao-e-troca-de-plano/dicas-para-escolher-um-plano/1439-lista-de-administradoras-de-beneficios-com-registro-na-ans
ADMINISTRADORAS_URL = None  # preencher quando a URL de download for confirmada

CNAE_ALVO = "6550200"  # 6550-2/00, planos de saude, sem pontuacao

# Situacao cadastral "02" = Ativa (layout oficial da Receita Federal)
SITUACAO_ATIVA = "02"

DATA_DIR = Path("./dados_prospeccao")
CACHE_ARQUIVO = DATA_DIR / "cnpjs_ja_vistos.txt"

# Alguns servidores do governo derrubam a conexao quando veem o User-Agent
# padrao da biblioteca requests ("python-requests/x.x"), tratando-o como
# bot. Um User-Agent de navegador comum resolve isso para dados abertos
# publicos como este.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def mes_referencia(hoje=None):
    """Pasta do mes a consultar: por padrao, o mes corrente (AAAA-MM).
    Ajuste manualmente (--mes) se a Receita publicar com atraso e voce
    quiser reprocessar o mes anterior."""
    hoje = hoje or datetime.date.today()
    return hoje.strftime("%Y-%m")


def baixar(url, destino, tentativas=3):
    destino.parent.mkdir(parents=True, exist_ok=True)
    kwargs = dict(stream=True, timeout=60, headers=HEADERS)
    if _USANDO_CURL_CFFI:
        kwargs["impersonate"] = "chrome110"  # imita o "aperto de mao" TLS de um Chrome real
    for tentativa in range(1, tentativas + 1):
        try:
            with requests.get(url, **kwargs) as r:
                if r.status_code == 404:
                    return False
                r.raise_for_status()
                with open(destino, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            # O Nextcloud, para um arquivo que nao existe dentro do path
            # pedido, pode responder 200 com uma pagina de erro em HTML em
            # vez de um 404 "limpo". Por isso validamos que o que foi
            # baixado e mesmo um .zip valido antes de dar como sucesso.
            if destino.suffix == ".zip" and not zipfile.is_zipfile(destino):
                print(f"  [aviso] resposta para {url} nao e um .zip valido (provavelmente arquivo inexistente).")
                destino.unlink(missing_ok=True)
                return False
            return True
        except Exception as e:
            print(f"  [aviso] falha ao baixar {url}: {e} (tentativa {tentativa}/{tentativas})")
    return False


def listar_partes(prefixo, pasta_mes, max_partes=12):
    """A Receita costuma dividir Empresas/Estabelecimentos/Socios em varias
    partes numeradas (0, 1, 2, ...). Tenta de 0 ate max_partes-1 e para
    quando um numero nao existir mais. Os arquivos ficam dentro da pasta do
    mes (AAAA-MM) no link publico de compartilhamento (SHARE_BASE_URL)."""
    return [
        f"{SHARE_BASE_URL}?path=%2F{pasta_mes}&files={prefixo}{i}.zip"
        for i in range(max_partes)
    ]


def ler_csv_dentro_do_zip(caminho_zip):
    """Gera linhas (listas de campos) de todos os .csv/.txt dentro do zip,
    decodificados em latin-1 (padrao da Receita Federal) e separados por
    ';'. As colunas vem sem cabecalho -- a ordem e a documentada no layout
    oficial da Receita Federal."""
    with zipfile.ZipFile(caminho_zip) as z:
        for nome in z.namelist():
            with z.open(nome) as fh:
                texto = io.TextIOWrapper(fh, encoding="latin-1", newline="")
                leitor = csv.reader(texto, delimiter=";", quotechar='"')
                yield from leitor


# ---------------------------------------------------------------------------
# Parte 1 -- filtrar Estabelecimentos pelo CNAE-alvo
# ---------------------------------------------------------------------------

def filtrar_estabelecimentos(pasta_mes, tmp_dir):
    """Baixa as partes de Estabelecimentos, filtra quem tem o CNAE-alvo como
    principal OU secundario e situacao ativa, e devolve um dict
    {cnpj_basico: {dados}}."""
    candidatos = {}
    urls = listar_partes("Estabelecimentos", pasta_mes)
    for idx, url in enumerate(urls):
        destino = tmp_dir / f"estabelecimentos_{idx}.zip"
        print(f"Baixando {url} ...")
        if not baixar(url, destino):
            print(f"  parte {idx} nao existe ou falhou -- assumindo fim das partes.")
            break
        for linha in ler_csv_dentro_do_zip(destino):
            if len(linha) < 12:
                continue
            cnpj_basico = linha[0]
            situacao = linha[5]
            data_inicio = linha[10]
            cnae_principal = linha[11]
            cnae_secundarias = linha[12]
            if situacao != SITUACAO_ATIVA:
                continue
            if cnae_principal != CNAE_ALVO and CNAE_ALVO not in cnae_secundarias.split(","):
                continue
            cnpj_completo = f"{linha[0]}{linha[1]}{linha[2]}"
            candidatos[cnpj_basico] = {
                "cnpj": cnpj_completo,
                "cnpj_basico": cnpj_basico,
                "nome_fantasia": linha[4],
                "uf": linha[19],
                "municipio": linha[20],
                "data_inicio_atividade": data_inicio,
                "cnae_principal": cnae_principal,
                "cnae_secundarias": cnae_secundarias,
            }
        destino.unlink(missing_ok=True)
    return candidatos


# ---------------------------------------------------------------------------
# Parte 2 -- completar com razao social e capital (Empresas)
# ---------------------------------------------------------------------------

def completar_com_empresas(candidatos, pasta_mes, tmp_dir):
    if not candidatos:
        return
    basicos_procurados = set(candidatos.keys())
    urls = listar_partes("Empresas", pasta_mes)
    for idx, url in enumerate(urls):
        destino = tmp_dir / f"empresas_{idx}.zip"
        print(f"Baixando {url} ...")
        if not baixar(url, destino):
            break
        for linha in ler_csv_dentro_do_zip(destino):
            if len(linha) < 6:
                continue
            cnpj_basico = linha[0]
            if cnpj_basico in basicos_procurados:
                candidatos[cnpj_basico]["razao_social"] = linha[1]
                candidatos[cnpj_basico]["natureza_juridica"] = linha[2]
                candidatos[cnpj_basico]["capital_social"] = linha[4]
        destino.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Parte 3 -- socios (QSA)
# ---------------------------------------------------------------------------

def completar_com_socios(candidatos, pasta_mes, tmp_dir):
    if not candidatos:
        return
    basicos_procurados = set(candidatos.keys())
    urls = listar_partes("Socios", pasta_mes)
    for idx, url in enumerate(urls):
        destino = tmp_dir / f"socios_{idx}.zip"
        print(f"Baixando {url} ...")
        if not baixar(url, destino):
            break
        for linha in ler_csv_dentro_do_zip(destino):
            if len(linha) < 3:
                continue
            cnpj_basico = linha[0]
            if cnpj_basico in basicos_procurados:
                candidatos[cnpj_basico].setdefault("socios", []).append(linha[2])
        destino.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Parte 4 -- remover quem ja esta no CADOP ou na lista de administradoras
# ---------------------------------------------------------------------------

def cnpjs_ja_registrados_na_ans():
    ja_registrados = set()
    kwargs = dict(timeout=60, headers=HEADERS)
    if _USANDO_CURL_CFFI:
        kwargs["impersonate"] = "chrome110"
    try:
        r = requests.get(CADOP_URL, **kwargs)
        r.raise_for_status()
        texto = r.content.decode("latin-1", errors="ignore")
        leitor = csv.reader(io.StringIO(texto), delimiter=";")
        cabecalho = next(leitor, None)
        col_cnpj = None
        if cabecalho:
            for i, nome in enumerate(cabecalho):
                if "cnpj" in nome.lower():
                    col_cnpj = i
                    break
        for linha in leitor:
            if col_cnpj is not None and col_cnpj < len(linha):
                ja_registrados.add(re.sub(r"\D", "", linha[col_cnpj]))
    except Exception as e:
        print(f"[aviso] nao consegui baixar o CADOP agora ({e}) -- seguindo sem esse filtro.")
    if ADMINISTRADORAS_URL:
        try:
            r = requests.get(ADMINISTRADORAS_URL, **kwargs)
            r.raise_for_status()
            # TODO: ajustar o parsing conforme o formato real do arquivo
            # quando a URL de download for confirmada.
        except Exception as e:
            print(f"[aviso] nao consegui baixar a lista de administradoras ({e}).")
    return ja_registrados


# ---------------------------------------------------------------------------
# Parte 5 -- nao reprocessar quem ja foi visto em meses anteriores
# ---------------------------------------------------------------------------

def carregar_cache():
    if CACHE_ARQUIVO.exists():
        return set(CACHE_ARQUIVO.read_text().splitlines())
    return set()


def atualizar_cache(cnpjs_novos):
    CACHE_ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_ARQUIVO, "a") as f:
        for cnpj in sorted(cnpjs_novos):
            f.write(cnpj + "\n")


# ---------------------------------------------------------------------------
# Saida
# ---------------------------------------------------------------------------

def gravar_saida(candidatos, pasta_mes):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    caminho = DATA_DIR / f"candidatos_novos_entrantes_{pasta_mes}.csv"
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        campos = [
            "cnpj", "razao_social", "nome_fantasia", "municipio", "uf",
            "data_inicio_atividade", "cnae_principal", "cnae_secundarias",
            "natureza_juridica", "capital_social", "socios",
        ]
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for dados in candidatos.values():
            linha = dict(dados)
            linha["socios"] = "; ".join(dados.get("socios", []))
            w.writerow(linha)
    return caminho


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mes", default=None, help="Pasta do mes a consultar (AAAA-MM). Padrao: mes corrente.")
    args = parser.parse_args()

    pasta_mes = args.mes or mes_referencia()
    tmp_dir = DATA_DIR / "tmp" / pasta_mes

    print(f"=== Prospeccao novos entrantes -- mes {pasta_mes} ===")

    print("\n[1/5] Filtrando Estabelecimentos pelo CNAE 6550-2/00 ...")
    candidatos = filtrar_estabelecimentos(pasta_mes, tmp_dir)
    print(f"  {len(candidatos)} CNPJs candidatos antes das exclusoes.")

    print("\n[2/5] Completando com razao social e capital (Empresas) ...")
    completar_com_empresas(candidatos, pasta_mes, tmp_dir)

    print("\n[3/5] Puxando socios (QSA) ...")
    completar_com_socios(candidatos, pasta_mes, tmp_dir)

    print("\n[4/5] Removendo quem ja esta registrado na ANS (CADOP/administradoras) ...")
    ja_registrados = cnpjs_ja_registrados_na_ans()
    candidatos = {k: v for k, v in candidatos.items() if v["cnpj"] not in ja_registrados}
    print(f"  {len(candidatos)} restantes depois da exclusao.")

    print("\n[5/5] Removendo quem ja apareceu em meses anteriores ...")
    ja_vistos = carregar_cache()
    novos = {k: v for k, v in candidatos.items() if v["cnpj"] not in ja_vistos}
    print(f"  {len(novos)} CNPJs realmente novos neste mes.")

    caminho_saida = gravar_saida(novos, pasta_mes)
    atualizar_cache({v["cnpj"] for v in novos.values()})

    print(f"\nPronto. Lista final: {caminho_saida}")
    print("Entregue esse CSV ao Claude Project para a Parte B da rotina")
    print("(cruzamento de socios, elo em comum, qualificacao e redacao).")


if __name__ == "__main__":
    main()
