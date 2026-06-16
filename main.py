from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware 

# Importações das funções de composição DXF e de interação com o Google Drive
from compose_dxf import compor_dxf_com_base as compor_dxf_com_base_18
from compose_dxf_32 import compor_dxf_com_base_32
from compose_dxf_32_2 import compor_dxf_com_base_32_2

# Importações para a rota de Placas Personalizadas
from detects_plaque import processar_ids_placas, limpar_dxf_placas, mapear_cor, preparar_placas_pedido, extrair_placas_de_arquivo_local

from google_drive import upload_to_drive, listar_arquivos_existentes, baixar_arquivo_drive, arquivo_existe_drive, mover_arquivos_antigos, buscar_dxf_personalizado

from datetime import datetime
from types import SimpleNamespace
import os
import math
import shutil 

app = FastAPI()

# --- Configuração CORS ---
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Entrada(BaseModel):
    arquivos: list[str]
    nome_arquivo: str = None
    maquina: str = "18"
    coordenadas_customizadas: dict[int, list[float]] = None
    tamanho_chapa: list[float] = None

class PlacaConfig(BaseModel):
    id: str
    quantidade: int = 1
    cor: str = ""
    arquivos_especificos: list[str] = None 

class EntradaPlacas(BaseModel):
    ids: list[str] = None 
    placas: list[PlacaConfig] = None
    tamanho_chapa: list[float] = None
    coordenadas_customizadas: dict[int, list[float]] = None
    nome_arquivo: str = None

class AnalisePlacasEntrada(BaseModel):
    ids: list[str]

# ==========================================
# ROTAS ANTIGAS MANTIDAS
# ==========================================
@app.post("/compor")
def compor(entrada: Entrada):
    total = len(entrada.arquivos)
    if total == 0:
        raise HTTPException(status_code=400, detail="Nenhum arquivo fornecido.")

    entrada.arquivos.sort(key=lambda x: x.lower())
    arquivos_faltando = []
    for nome in entrada.arquivos:
        if not arquivo_existe_drive(nome, subpasta="arquivos padronizados"):
            arquivos_faltando.append(nome)
    if arquivos_faltando:
        raise HTTPException(status_code=404, detail=f"Arquivos não encontrados no Google Drive:\n" + "\n".join(arquivos_faltando))

    existentes = listar_arquivos_existentes()
    nome_base = entrada.nome_arquivo
    is_custom = entrada.coordenadas_customizadas is not None and entrada.tamanho_chapa is not None

    if is_custom:
        max_por_plano = len(entrada.coordenadas_customizadas)
        def compor_fn(objs, path_saida):
            compor_dxf_com_base_18(objs, path_saida, custom_coords=entrada.coordenadas_customizadas, custom_chapa=entrada.tamanho_chapa)
    else:
        if entrada.maquina == "32":
            max_por_plano = 32
            compor_fn = compor_dxf_com_base_32
        elif entrada.maquina == "32-2":
            max_por_plano = 32
            compor_fn = compor_dxf_com_base_32_2
        else:
            max_por_plano = 18
            compor_fn = compor_dxf_com_base_18

    planos = []
    num_planos = (total + max_por_plano - 1) // max_por_plano

    for i in range(num_planos):
        chunk_names = entrada.arquivos[i*max_por_plano : (i+1)*max_por_plano]
        chunk_objs = [SimpleNamespace(nome=name, posicao=index + 1) for index, name in enumerate(chunk_names)]

        nome_saida = nome_base if i == 0 else nome_base.replace('.dxf', f'_{i+1:02d}.dxf')
        nome_saida_livre = nome_saida
        contador_extra = 2
        while nome_saida_livre in existentes:
            nome_saida_livre = nome_base.replace('.dxf', f'_{i+contador_extra:02d}.dxf')
            contador_extra += 1

        path_saida = f"/tmp/{nome_saida_livre}"
        compor_fn(chunk_objs, path_saida)
        url_dxf = upload_to_drive(path_saida, nome_saida_livre)
        url_png_simulado = url_dxf.replace('.dxf', '.png').replace('/view', '')
        planos.append({"nome": nome_saida_livre, "url": url_dxf, "url_png": url_png_simulado})

    return {"plans": planos}

@app.post("/mover-antigos")
def mover_antigos():
    try:
        moved_count = mover_arquivos_antigos()
        return {"moved": moved_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao mover arquivos: {e}")

# ==========================================
# NOVAS ROTAS - INTELIGÊNCIA DE PLACAS
# ==========================================

@app.post("/analisar_placas")
def analisar_placas(entrada: AnalisePlacasEntrada):
    if not entrada.ids:
        raise HTTPException(status_code=400, detail="Nenhum ID fornecido para análise.")
    
    resultados = preparar_placas_pedido(entrada.ids)
    return {"resultados": resultados}

@app.post("/upload_analisar_placa")
async def upload_analisar_placa(
    file: UploadFile = File(...), 
    target_id: str = Form(...),
    ja_espelhado: bool = Form(False) # NOVO: Recebe a flag enviada pelo frontend
):
    """ Endpoint para upload manual de DXF caso não ache no Drive. """
    caminho_temp = f"/tmp/{target_id}_uploaded.dxf"
    
    with open(caminho_temp, "wb") as buffer:
        buffer.write(await file.read())
        
    # NOVO: Passando a flag ja_espelhado para a função base
    resultado = extrair_placas_de_arquivo_local(caminho_temp, target_id, ja_espelhado)
    return resultado

@app.post("/engraved_plaque")
def engraved_plaque(entrada: EntradaPlacas):
    if entrada.ids and not entrada.placas:
        resultados = processar_ids_placas(entrada.ids)
        return {"resultados": resultados}
    
    if not entrada.placas:
        raise HTTPException(status_code=400, detail="A lista de placas não pode estar vazia.")
        
    if not entrada.tamanho_chapa or not entrada.coordenadas_customizadas or not entrada.nome_arquivo:
        raise HTTPException(status_code=400, detail="Para gerar o plano, informe tamanho_chapa, coordenadas_customizadas e nome_arquivo.")

    lista_arquivos_composicao = []
    resultados_log = []

    for placa in entrada.placas:
        sufixo_cor = mapear_cor(placa.cor) 

        if placa.arquivos_especificos and len(placa.arquivos_especificos) > 0:
            qtd_selecionada = len(placa.arquivos_especificos)
            insercoes_necessarias = math.ceil(placa.quantidade / qtd_selecionada)
            
            for _ in range(insercoes_necessarias):
                for caminho_tmp in placa.arquivos_especificos:
                    nome_base = os.path.basename(caminho_tmp)
                    
                    if f"-{sufixo_cor}.dxf" not in nome_base:
                        nome_com_cor = nome_base.replace(".dxf", f"-{sufixo_cor}.dxf")
                        novo_caminho = f"/tmp/{nome_com_cor}"
                        try:
                            shutil.copy(caminho_tmp, novo_caminho)
                            lista_arquivos_composicao.append(nome_com_cor)
                        except Exception as e:
                            lista_arquivos_composicao.append(nome_base)
                    else:
                        lista_arquivos_composicao.append(nome_base)
            
            resultados_log.append({"id": placa.id, "status": "sucesso", "placas_usadas": qtd_selecionada})
        else:
            caminho_local, nome_original = buscar_dxf_personalizado(placa.id)
            if not caminho_local:
                resultados_log.append({"id": placa.id, "status": "nao_encontrado"})
                continue
                
            nome_limpo = f"{placa.id}_limpo-{sufixo_cor}.dxf"
            caminho_limpo = f"/tmp/{nome_limpo}"
            
            qtd_encontrada = limpar_dxf_placas(caminho_local, caminho_limpo)
            resultados_log.append({"id": placa.id, "placas_internas_encontradas": qtd_encontrada, "cor_injetada": sufixo_cor})
            
            if qtd_encontrada > 0:
                insercoes_necessarias = math.ceil(placa.quantidade / qtd_encontrada)
                for _ in range(insercoes_necessarias):
                    lista_arquivos_composicao.append(nome_limpo)

    if not lista_arquivos_composicao:
        raise HTTPException(status_code=400, detail="Nenhuma placa válida encontrada para compor.")

    total = len(lista_arquivos_composicao)
    max_por_plano = len(entrada.coordenadas_customizadas)
    num_planos = (total + max_por_plano - 1) // max_por_plano
    
    existentes = listar_arquivos_existentes()
    nome_base = entrada.nome_arquivo
    planos = []

    for i in range(num_planos):
        chunk_names = lista_arquivos_composicao[i*max_por_plano : (i+1)*max_por_plano]
        chunk_objs = [SimpleNamespace(nome=name, posicao=index + 1) for index, name in enumerate(chunk_names)]

        nome_saida = nome_base if i == 0 else nome_base.replace('.dxf', f'_{i+1:02d}.dxf')
        nome_saida_livre = nome_saida
        contador_extra = 2
        while nome_saida_livre in existentes:
            nome_saida_livre = nome_base.replace('.dxf', f'_{i+contador_extra:02d}.dxf')
            contador_extra += 1

        path_saida = f"/tmp/{nome_saida_livre}"
        compor_dxf_com_base_18(chunk_objs, path_saida, custom_coords=entrada.coordenadas_customizadas, custom_chapa=entrada.tamanho_chapa)

        url_dxf = upload_to_drive(path_saida, nome_saida_livre)
        url_png_simulado = url_dxf.replace('.dxf', '.png').replace('/view', '')
        planos.append({"nome": nome_saida_livre, "url": url_dxf, "url_png": url_png_simulado})

    return {"logs_deteccao": resultados_log, "plans": planos}