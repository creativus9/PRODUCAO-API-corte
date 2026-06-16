import ezdxf
from ezdxf import bbox
from ezdxf.math import Matrix44
import logging
import unicodedata
import os
import base64

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==============================================================================
# CONFIGURAÇÃO DE FONTE ESTEPE (FALLBACK)
# ==============================================================================
try:
    ezdxf.options.default_font = 'DejaVuSans.ttf'
    try:
        from ezdxf.fonts import fonts
        font_paths_estepe = [
            './DejaVuSans.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/freefont/FreeSans.ttf'
        ]
        for fp in font_paths_estepe:
            if os.path.exists(fp):
                fonts.font_manager.add_font(fp)
                break
    except Exception:
        pass 
except Exception as e:
    logger.debug(f"Não foi possível injetar a fonte fallback no ezdxf: {e}")
# ==============================================================================

try:
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.svg import SVGBackend
    from ezdxf.addons.drawing.layout import Page
    CAN_DRAW_SVG = True
except ImportError:
    CAN_DRAW_SVG = False
    logger.warning("Módulo ezdxf.addons.drawing não disponível. SVGs não serão gerados.")

def contar_placas_no_dxf(caminho_arquivo: str) -> int:
    try:
        doc = ezdxf.readfile(caminho_arquivo)
        msp = doc.modelspace()
    except Exception as e:
        logger.error(f"Erro ao ler DXF {caminho_arquivo}: {e}")
        return 0

    count = 0
    for entity in msp.query('LWPOLYLINE'):
        if entity.dxf.color != 2: continue
        points = list(entity.get_points('xy'))
        is_closed = entity.closed or (len(points) == 5 and points[0] == points[-1])
        if not is_closed or len(points) not in (4, 5): continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        largura, altura = max(xs) - min(xs), max(ys) - min(ys)
        tol = 0.5
        if (abs(largura - 129.0) <= tol and abs(altura - 187.8) <= tol) or (abs(largura - 187.8) <= tol and abs(altura - 129.0) <= tol):
            count += 1
    return count

def mapear_cor(cor_texto: str) -> str:
    if not cor_texto: return "PRA" 
    cor_limpa = ''.join(c for c in unicodedata.normalize('NFD', cor_texto) if unicodedata.category(c) != 'Mn').upper()
    if "DOU" in cor_limpa or "OUR" in cor_limpa: return "DOU"
    elif "ROS" in cor_limpa: return "ROS"
    elif "PRA" in cor_limpa: return "PRA"
    return "PRA"

def limpar_dxf_placas(caminho_entrada: str, caminho_saida: str) -> int:
    try:
        doc = ezdxf.readfile(caminho_entrada)
        msp = doc.modelspace()
    except Exception as e:
        return 0
    placas_boxes = []
    centros_placas = []
    for entity in msp.query('LWPOLYLINE'):
        if entity.dxf.color == 2:
            points = list(entity.get_points('xy'))
            is_closed = entity.closed or (len(points) == 5 and points[0] == points[-1])
            if is_closed and len(points) in (4, 5):
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                largura, altura = max(xs) - min(xs), max(ys) - min(ys)
                tol = 0.5
                if (abs(largura - 129.0) <= tol and abs(altura - 187.8) <= tol) or (abs(largura - 187.8) <= tol and abs(altura - 129.0) <= tol):
                    placas_boxes.append((min(xs)-1, min(ys)-1, max(xs)+1, max(ys)+1))
                    centros_placas.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    
    qtd_placas = len(placas_boxes)
    if qtd_placas == 0: return 0
    cache = bbox.Cache()
    entities_to_delete = []
    for entity in msp:
        try:
            bb = bbox.extents([entity], cache=cache)
            if not bb.has_data: continue
            dentro = False
            for (px1, py1, px2, py2) in placas_boxes:
                if (bb.extmin.x >= px1 and bb.extmax.x <= px2 and bb.extmin.y >= py1 and bb.extmax.y <= py2):
                    dentro = True
                    break
            if not dentro: entities_to_delete.append(entity)
        except Exception: entities_to_delete.append(entity)
            
    for ent in entities_to_delete:
        try: msp.delete_entity(ent)
        except: pass
            
    if centros_placas:
        cx_global = sum(c[0] for c in centros_placas) / len(centros_placas)
        m_mirror = Matrix44.chain(
            Matrix44.translate(-cx_global, 0, 0),
            Matrix44.scale(-1, 1, 1),
            Matrix44.translate(cx_global, 0, 0)
        )
        for ent in msp:
            try: ent.transform(m_mirror)
            except AttributeError: pass
                
        novos_centros = [(2 * cx_global - c[0], c[1]) for c in centros_placas]
        pasta_base = os.path.dirname(os.path.abspath(__file__))
        caminho_sobrepor = os.path.join(pasta_base, "DXF Arquivos", "Placa_Sobrepor.dxf")
        if os.path.exists(caminho_sobrepor):
            try:
                doc_sobrepor = ezdxf.readfile(caminho_sobrepor)
                msp_sobrepor = doc_sobrepor.modelspace()
                cache_sob = bbox.Cache()
                bb_sob = bbox.extents(msp_sobrepor, cache=cache_sob)
                if bb_sob.has_data:
                    for (nx, ny) in novos_centros:
                        dx, dy = nx - bb_sob.center.x, ny - bb_sob.center.y
                        for ent in msp_sobrepor:
                            try:
                                novo_ent = ent.copy()
                                novo_ent.translate(dx, dy, 0)
                                msp.add_entity(novo_ent)
                            except Exception: pass
            except Exception: pass
    doc.saveas(caminho_saida)
    return qtd_placas

def processar_ids_placas(ids: list) -> list:
    from google_drive import buscar_dxf_personalizado
    resultados = []
    for target_id in ids:
        caminho_local, nome_arquivo = buscar_dxf_personalizado(target_id)
        if not caminho_local:
            resultados.append({"id": target_id, "status": "nao_encontrado", "quantidade": 0, "arquivo": None})
            continue
        qtd_placas = contar_placas_no_dxf(caminho_local)
        resultados.append({"id": target_id, "status": "sucesso", "quantidade": qtd_placas, "arquivo": nome_arquivo})
    return resultados

def gerar_svg_base64(doc_dxf) -> str:
    if not CAN_DRAW_SVG: return ""
    try:
        msp = doc_dxf.modelspace()
        ctx = RenderContext(doc_dxf)
        backend = SVGBackend()
        Frontend(ctx, backend).draw_layout(msp)
        page = Page(0, 0)
        svg_string = backend.get_string(page)
        return base64.b64encode(svg_string.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"Erro ao gerar SVG Base64: {e}")
        return ""

# NOVO: Adicionado ja_espelhado como argumento opcional, padrão False
def extrair_placas_de_arquivo_local(caminho_local: str, target_id: str, ja_espelhado: bool = False) -> dict:
    """ Função unificada para abrir um arquivo local, cortar, espelhar (se não estiver) e gerar os SVGs """
    pasta_base = os.path.dirname(os.path.abspath(__file__))
    caminho_sobrepor = os.path.join(pasta_base, "DXF Arquivos", "Placa_Sobrepor.dxf")

    try:
        doc_main = ezdxf.readfile(caminho_local)
        msp_main = doc_main.modelspace()
    except Exception:
        return {"id": target_id, "status": "erro_leitura", "placas": []}

    placas_boxes = []
    centros_placas = []
    for entity in msp_main.query('LWPOLYLINE'):
        if entity.dxf.color == 2:
            points = list(entity.get_points('xy'))
            is_closed = entity.closed or (len(points) == 5 and points[0] == points[-1])
            if is_closed and len(points) in (4, 5):
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                largura, altura = max(xs) - min(xs), max(ys) - min(ys)
                tol = 0.5
                if (abs(largura - 129.0) <= tol and abs(altura - 187.8) <= tol) or (abs(largura - 187.8) <= tol and abs(altura - 129.0) <= tol):
                    placas_boxes.append((min(xs)-1, min(ys)-1, max(xs)+1, max(ys)+1))
                    centros_placas.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))

    if not placas_boxes:
        return {"id": target_id, "status": "sem_placas", "placas": []}

    cx_global = sum(c[0] for c in centros_placas) / len(centros_placas)
    placas_extraidas = []
    cache_main = bbox.Cache()

    for i, (bx1, by1, bx2, by2) in enumerate(placas_boxes):
        caminho_temp = f"/tmp/{target_id}_plate_{i}.dxf"
        doc_temp = ezdxf.readfile(caminho_local)
        msp_temp = doc_temp.modelspace()

        entities_to_delete = []
        for entity in msp_temp:
            try:
                bb = bbox.extents([entity], cache=cache_main)
                if not bb.has_data: continue
                if not (bb.extmin.x >= bx1 and bb.extmax.x <= bx2 and bb.extmin.y >= by1 and bb.extmax.y <= by2):
                    entities_to_delete.append(entity)
            except Exception:
                entities_to_delete.append(entity)

        for ent in entities_to_delete:
            try: msp_temp.delete_entity(ent)
            except: pass

        cx_placa, cy_placa = centros_placas[i]
        
        # NOVO: Condição que checa se o usuário já marcou que está espelhado!
        if not ja_espelhado:
            m_mirror = Matrix44.chain(Matrix44.translate(-cx_global, 0, 0), Matrix44.scale(-1, 1, 1), Matrix44.translate(cx_global, 0, 0))
            for ent in msp_temp:
                try: ent.transform(m_mirror)
                except AttributeError: pass
            
            # Se espelhou, a nova posição do centro se moveu inversamente ao eixo X global
            nx, ny = 2 * cx_global - cx_placa, cy_placa 
        else:
            # Se não espelhou, o centro da placa continua exatamente onde estava
            nx, ny = cx_placa, cy_placa

        if os.path.exists(caminho_sobrepor):
            try:
                doc_sobrepor = ezdxf.readfile(caminho_sobrepor)
                msp_sobrepor = doc_sobrepor.modelspace()
                bb_sob = bbox.extents(msp_sobrepor)
                if bb_sob.has_data:
                    dx, dy = nx - bb_sob.center.x, ny - bb_sob.center.y
                    for ent in msp_sobrepor:
                        novo_ent = ent.copy()
                        novo_ent.translate(dx, dy, 0)
                        msp_temp.add_entity(novo_ent)
            except Exception: pass

        m_to_origin = Matrix44.translate(-nx, -ny, 0)
        for ent in msp_temp:
            try: ent.transform(m_to_origin)
            except AttributeError: pass

        doc_temp.saveas(caminho_temp)
        svg_b64 = gerar_svg_base64(doc_temp)
        placas_extraidas.append({"index": i, "caminho_dxf": caminho_temp, "imagem": svg_b64})

    return {"id": target_id, "status": "sucesso", "placas": placas_extraidas}

def preparar_placas_pedido(ids: list) -> list:
    from google_drive import buscar_dxf_personalizado
    resultados = []
    
    for target_id in ids:
        caminho_local, nome_arquivo = buscar_dxf_personalizado(target_id)
        if not caminho_local:
            resultados.append({"id": target_id, "status": "nao_encontrado", "placas": []})
            continue

        res = extrair_placas_de_arquivo_local(caminho_local, target_id)
        resultados.append(res)

    return resultados