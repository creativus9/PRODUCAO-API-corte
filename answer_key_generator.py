import os
from typing import Dict, List, Tuple

import ezdxf
from ezdxf import units


# Medidas fixas solicitadas para cada plaquinha.
PLACA_EXTERNA_LARGURA_MM = 129.0
PLACA_EXTERNA_ALTURA_MM = 187.8

# O gabarito interno recua 3 mm em cada lado.
# Portanto, cada dimensão total diminui 6 mm.
RECUO_INTERNO_POR_LADO_MM = 3.0
PLACA_INTERNA_LARGURA_MM = PLACA_EXTERNA_LARGURA_MM - (2 * RECUO_INTERNO_POR_LADO_MM)
PLACA_INTERNA_ALTURA_MM = PLACA_EXTERNA_ALTURA_MM - (2 * RECUO_INTERNO_POR_LADO_MM)

MAXIMO_PLACAS = 18
MARGEM_MARCA_MM = 8.5
TAMANHO_MARCA_MM = 17.0

COR_AMARELA_ACI = 2
COR_PRETA_ACI = 7

# Geometria das "meias bolinhas" de pega.
DIAMETRO_BOLINHA_MM = 25.0
RAIO_BOLINHA_MM = DIAMETRO_BOLINHA_MM / 2.0
DISTANCIA_CANTO_MM = 15.0
BULGE_SEMICIRCULO = -1.0  # semicírculo perfeito em LWPOLYLINE, voltado para fora


Coordenadas = Dict[int, List[float]]


def _validar_entrada(
    coordenadas_customizadas: Coordenadas,
    tamanho_chapa: List[float],
) -> Tuple[Dict[int, Tuple[float, float]], Tuple[float, float]]:
    if not coordenadas_customizadas:
        raise ValueError("Nenhuma coordenada foi fornecida para o gabarito.")

    if len(coordenadas_customizadas) > MAXIMO_PLACAS:
        raise ValueError(f"O gerador de gabarito aceita no máximo {MAXIMO_PLACAS} plaquinhas.")

    if not tamanho_chapa or len(tamanho_chapa) != 2:
        raise ValueError("tamanho_chapa deve conter exatamente [largura, altura] em milímetros.")

    try:
        largura_chapa = float(tamanho_chapa[0])
        altura_chapa = float(tamanho_chapa[1])
    except (TypeError, ValueError):
        raise ValueError("A largura e a altura da chapa devem ser números válidos.")

    if largura_chapa < TAMANHO_MARCA_MM or altura_chapa < TAMANHO_MARCA_MM:
        raise ValueError(
            f"A chapa deve ter pelo menos {TAMANHO_MARCA_MM} mm em cada dimensão para comportar as marcas dos cantos."
        )

    coordenadas_normalizadas: Dict[int, Tuple[float, float]] = {}

    for posicao, coordenada in coordenadas_customizadas.items():
        try:
            posicao_int = int(posicao)
        except (TypeError, ValueError):
            raise ValueError(f"Posição inválida recebida: {posicao!r}.")

        if posicao_int < 1 or posicao_int > MAXIMO_PLACAS:
            raise ValueError(f"Cada posição deve estar entre 1 e {MAXIMO_PLACAS}.")

        if not coordenada or len(coordenada) != 2:
            raise ValueError(f"A posição {posicao_int} deve conter exatamente [X, Y].")

        try:
            x = float(coordenada[0])
            y = float(coordenada[1])
        except (TypeError, ValueError):
            raise ValueError(f"As coordenadas da posição {posicao_int} devem ser números válidos.")

        coordenadas_normalizadas[posicao_int] = (x, y)

    return coordenadas_normalizadas, (largura_chapa, altura_chapa)


def _criar_contorno_com_bolinhas(
    centro_x: float,
    centro_y: float,
    largura: float,
    altura: float,
):
    """
    Cria o contorno fechado da plaquinha com duas meias bolinhas soldadas:

    - Esquerda: centro sobre a linha lateral esquerda, a 15 mm do canto superior.
    - Direita: centro sobre a linha lateral direita, a 15 mm do canto inferior.

    O contorno retorna no formato aceito por add_lwpolyline, incluindo o bulge
    dos segmentos em arco para formar semicírculos perfeitos.
    """
    metade_largura = largura / 2.0
    metade_altura = altura / 2.0

    esquerda = centro_x - metade_largura
    direita = centro_x + metade_largura
    inferior = centro_y - metade_altura
    superior = centro_y + metade_altura

    # Centros das bolinhas em relação à própria plaquinha.
    centro_bolinha_esq_y = superior - DISTANCIA_CANTO_MM
    centro_bolinha_dir_y = inferior + DISTANCIA_CANTO_MM

    if (centro_bolinha_esq_y + RAIO_BOLINHA_MM) > superior or (centro_bolinha_esq_y - RAIO_BOLINHA_MM) < inferior:
        raise ValueError("A bolinha esquerda não cabe dentro da altura da plaquinha com a distância solicitada.")

    if (centro_bolinha_dir_y + RAIO_BOLINHA_MM) > superior or (centro_bolinha_dir_y - RAIO_BOLINHA_MM) < inferior:
        raise ValueError("A bolinha direita não cabe dentro da altura da plaquinha com a distância solicitada.")

    # Caminho em sentido horário. Os segmentos com bulge=1 geram os semicírculos
    # para fora do retângulo, mantendo tudo soldado em uma única polilinha fechada.
    return [
        (esquerda, superior, 0.0),
        (direita, superior, 0.0),
        (direita, centro_bolinha_dir_y + RAIO_BOLINHA_MM, BULGE_SEMICIRCULO),
        (direita, centro_bolinha_dir_y - RAIO_BOLINHA_MM, 0.0),
        (direita, inferior, 0.0),
        (esquerda, inferior, 0.0),
        (esquerda, centro_bolinha_esq_y - RAIO_BOLINHA_MM, BULGE_SEMICIRCULO),
        (esquerda, centro_bolinha_esq_y + RAIO_BOLINHA_MM, 0.0),
    ]


def _adicionar_placa_com_bolinhas(
    msp,
    centro_x: float,
    centro_y: float,
    largura: float,
    altura: float,
):
    pontos = _criar_contorno_com_bolinhas(
        centro_x=centro_x,
        centro_y=centro_y,
        largura=largura,
        altura=altura,
    )

    entidade = msp.add_lwpolyline(
        pontos,
        format="xyb",
        close=True,
        dxfattribs={
            "layer": "GABARITO_PRETO",
            "color": COR_PRETA_ACI,
        },
    )

    # ACI 7 já representa preto no fluxo CAD. O True Color reforça o preto real
    # para programas que interpretam a cor 7 conforme o tema da interface.
    try:
        entidade.rgb = (0, 0, 0)
    except Exception:
        pass


def _adicionar_marca_amarela(msp, centro_x: float, centro_y: float):
    metade = TAMANHO_MARCA_MM / 2.0
    msp.add_lwpolyline(
        [
            (centro_x - metade, centro_y - metade),
            (centro_x + metade, centro_y - metade),
            (centro_x + metade, centro_y + metade),
            (centro_x - metade, centro_y + metade),
        ],
        close=True,
        dxfattribs={
            "layer": "MARCAS_BASE",
            "color": COR_AMARELA_ACI,
        },
    )


def _gerar_arquivo(
    caminho_saida: str,
    coordenadas: Dict[int, Tuple[float, float]],
    tamanho_chapa: Tuple[float, float],
    largura_placa: float,
    altura_placa: float,
) -> str:
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units.MM

    # Camadas independentes deixam as entidades claras sem afetar qualquer
    # camada ou lógica dos outros geradores existentes.
    if "GABARITO_PRETO" not in doc.layers:
        doc.layers.add("GABARITO_PRETO", color=COR_PRETA_ACI)
    if "MARCAS_BASE" not in doc.layers:
        doc.layers.add("MARCAS_BASE", color=COR_AMARELA_ACI)

    msp = doc.modelspace()
    largura_chapa, altura_chapa = tamanho_chapa

    # Mesma lógica já utilizada pela composição universal:
    # quadrados de 17 mm centralizados a 8,5 mm de cada borda.
    posicoes_marcas = [
        (MARGEM_MARCA_MM, MARGEM_MARCA_MM),
        (largura_chapa - MARGEM_MARCA_MM, MARGEM_MARCA_MM),
        (MARGEM_MARCA_MM, altura_chapa - MARGEM_MARCA_MM),
        (largura_chapa - MARGEM_MARCA_MM, altura_chapa - MARGEM_MARCA_MM),
    ]

    for x, y in posicoes_marcas:
        _adicionar_marca_amarela(msp, x, y)

    # As coordenadas recebidas representam o centro de cada plaquinha,
    # exatamente como já acontece no motor de composição atual.
    for posicao in sorted(coordenadas):
        x, y = coordenadas[posicao]
        _adicionar_placa_com_bolinhas(
            msp=msp,
            centro_x=x,
            centro_y=y,
            largura=largura_placa,
            altura=altura_placa,
        )

    os.makedirs(os.path.dirname(caminho_saida) or ".", exist_ok=True)
    doc.saveas(caminho_saida)
    return caminho_saida


def gerar_gabaritos(
    coordenadas_customizadas: Coordenadas,
    tamanho_chapa: List[float],
    caminho_externo: str,
    caminho_interno: str,
) -> Tuple[str, str]:
    """
    Gera os dois arquivos DXF do gabarito:

    - Externo: plaquinhas de 129 x 187,8 mm com duas meias bolinhas soldadas.
    - Interno: 3 mm menores em cada lado, mantendo os mesmos centros e as
      mesmas bolinhas 3 mm para dentro por consequência do recuo uniforme.

    As quatro marcas amarelas permanecem exatamente nas mesmas posições nos
    dois arquivos.
    """
    coordenadas, chapa = _validar_entrada(coordenadas_customizadas, tamanho_chapa)

    _gerar_arquivo(
        caminho_saida=caminho_externo,
        coordenadas=coordenadas,
        tamanho_chapa=chapa,
        largura_placa=PLACA_EXTERNA_LARGURA_MM,
        altura_placa=PLACA_EXTERNA_ALTURA_MM,
    )

    _gerar_arquivo(
        caminho_saida=caminho_interno,
        coordenadas=coordenadas,
        tamanho_chapa=chapa,
        largura_placa=PLACA_INTERNA_LARGURA_MM,
        altura_placa=PLACA_INTERNA_ALTURA_MM,
    )

    return caminho_externo, caminho_interno
