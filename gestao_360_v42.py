from qgis.core import *
from qgis.gui import QgsDockWidget
from qgis.utils import iface
from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QAction, QLineEdit,
                             QMessageBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QFileDialog, QApplication)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import math
import os

# --- CACHE GLOBAL ---
global CACHE_360
CACHE_360 = {
    'layer': None, 
    'vias': None, 
    'grid_layer': None,
    'kb': 0, 
    't_tot': None, 
    'kl_m': 0, 
    'processed': False
}

# --- PARÂMETROS ---
NOME_VIAS_PADRAO = 'valentina_vias'
EPSG_PROJETADO = 'EPSG:31985' # UTM 25S (Paraíba)
DIST_MIN = 2.7 
TAMANHO_GRID = 250 
LIMITE_GRID_SEGURO = 4000

# --- 1. FUNÇÕES AUXILIARES ---

def gerar_ids_sequenciais(layer):
    pr = layer.dataProvider()
    if layer.fields().indexOf('id_seq') == -1:
        pr.addAttributes([QgsField("id_seq", QVariant.Int)])
        layer.updateFields()
    idx = layer.fields().indexOf('id_seq')
    layer.startEditing()
    count = 1
    for feat in layer.getFeatures():
        layer.changeAttributeValue(feat.id(), idx, count)
        count += 1
    layer.commitChanges()

def ativar_rotulos(layer):
    settings = QgsPalLayerSettings()
    settings.fieldName = "id_seq"
    format = QgsTextFormat()
    format.setSize(8)
    format.setColor(QColor('black'))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.7)
    buffer.setColor(QColor('white'))
    format.setBuffer(buffer)
    settings.setFormat(format)
    settings.placement = QgsPalLayerSettings.OrderedPositionsAroundPoint
    settings.dist = 0
    settings.yOffset = -2.0 
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()

def aplicar_estilo_grid_seguro(vl):
    # Estilo Verde Transparente (OK)
    sym_ok = QgsFillSymbol.createSimple({
        'color': '0,255,0,40',      
        'outline_color': '0,180,0',
        'outline_width': '0.6',
        'outline_style': 'solid'
    })
    # Estilo Transparente Borda Azul (Pendente)
    sym_pendente = QgsFillSymbol.createSimple({
        'color': '0,0,0,0',         
        'outline_color': '0,0,255', 
        'outline_width': '0.4',
        'outline_style': 'dash'
    })

    cat_ok = QgsRendererCategory("OK", sym_ok, "Concluído")
    cat_pendente = QgsRendererCategory("Pendente", sym_pendente, "Pendente")
    
    renderer = QgsCategorizedSymbolRenderer("status", [cat_ok, cat_pendente])
    vl.setRenderer(renderer)
    vl.setOpacity(0.7)

def gerar_grid_persistente(layer_ref):
    """
    Cria o Grid e salva no disco como GeoPackage (.gpkg)
    """
    try:
        # 1. Validação CRS
        if not layer_ref.crs().isValid():
            iface.messageBar().pushMessage("Erro", "Camada sem CRS!", level=2)
            return None

        # 2. Transformação (Cálculo da Geometria em Metros)
        crs_src = layer_ref.crs()
        crs_dest = QgsCoordinateReferenceSystem(EPSG_PROJETADO)
        transform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
        ext_original = layer_ref.extent()
        ext_utm = transform.transformBoundingBox(ext_original)
        
        # 3. Trava de Segurança (Anti-Crash)
        width = ext_utm.width()
        height = ext_utm.height()
        cols = math.ceil(width / TAMANHO_GRID)
        rows = math.ceil(height / TAMANHO_GRID)
        if (cols * rows) > LIMITE_GRID_SEGURO:
            QMessageBox.critical(None, "Segurança", "Área muito grande para o Grid.")
            return None

        # 4. Salvar Como (Diálogo)
        caminho_arquivo, _ = QFileDialog.getSaveFileName(
            None, 
            "Salvar Grid de Controle Como...", 
            "", 
            "GeoPackage (*.gpkg)"
        )
        
        if not caminho_arquivo:
            return None # Cancelado
            
        if not caminho_arquivo.endswith('.gpkg'):
            caminho_arquivo += '.gpkg'

        # 5. Criar Arquivo Físico
        fields = QgsFields()
        fields.append(QgsField("status", QVariant.String))
        
        writer = QgsVectorFileWriter(
            caminho_arquivo,
            "UTF-8",
            fields,
            QgsWkbTypes.Polygon,
            crs_dest,
            "GPKG"
        )
        
        if writer.hasError() != QgsVectorFileWriter.NoError:
            QMessageBox.critical(None, "Erro", f"Erro ao criar arquivo: {writer.errorMessage()}")
            return None

        # 6. Gerar Quadrados
        xmin = math.floor(ext_utm.xMinimum() / TAMANHO_GRID) * TAMANHO_GRID
        ymin = math.floor(ext_utm.yMinimum() / TAMANHO_GRID) * TAMANHO_GRID
        
        for i in range(cols + 2):
            for j in range(rows + 2):
                x = xmin + (i * TAMANHO_GRID)
                y = ymin + (j * TAMANHO_GRID)
                geom = QgsGeometry.fromRect(QgsRectangle(x, y, x + TAMANHO_GRID, y + TAMANHO_GRID))
                
                f = QgsFeature()
                f.setGeometry(geom)
                f.setAttributes(["Pendente"])
                writer.addFeature(f)
        
        del writer # Fecha e salva o arquivo
        
        # 7. Carregar Camada
        vl = QgsVectorLayer(caminho_arquivo, "Grid_Controle_360", "ogr")
        if not vl.isValid():
            QMessageBox.critical(None, "Erro", "Falha ao carregar o arquivo salvo.")
            return None
            
        aplicar_estilo_grid_seguro(vl)
        QgsProject.instance().addMapLayer(vl)
        CACHE_360['grid_layer'] = vl
        
        iface.mapCanvas().setExtent(ext_original)
        iface.mapCanvas().refresh()
        
        iface.messageBar().pushMessage("Sucesso", f"Grid salvo em: {caminho_arquivo}", level=3)
        return vl
        
    except Exception as e:
        QMessageBox.critical(None, "Erro", f"Falha ao gerar grid: {str(e)}")
        return None

def alternar_status_grid():
    vl = CACHE_360.get('grid_layer')
    
    # Tenta recuperar layer se perdeu a referência
    if not vl or not vl.isValid():
        layers = QgsProject.instance().mapLayersByName("Grid_Controle_360")
        if layers:
            vl = layers[0]
            CACHE_360['grid_layer'] = vl
        else:
            iface.messageBar().pushMessage("Erro", "Grid não encontrado. Carregue ou crie o Grid.", level=1)
            return

    selecao = vl.selectedFeatures()
    if not selecao:
        iface.messageBar().pushMessage("Aviso", "Selecione quadrados para marcar.", level=1)
        return

    idx = vl.fields().indexOf('status')
    vl.startEditing()
    for f in selecao:
        atual = f['status']
        novo = 'Pendente' if atual == 'OK' else 'OK'
        vl.changeAttributeValue(f.id(), idx, novo)
    
    vl.commitChanges() 
    vl.triggerRepaint()
    vl.removeSelection() 

def resetar_grid_inteligente():
    vl = CACHE_360.get('grid_layer')
    if not vl or not vl.isValid():
        layers = QgsProject.instance().mapLayersByName("Grid_Controle_360")
        if layers:
            vl = layers[0]
            CACHE_360['grid_layer'] = vl
        else:
            iface.messageBar().pushMessage("Erro", "Grid não encontrado.", level=1)
            return

    selecao = vl.selectedFeatures()
    idx = vl.fields().indexOf('status')
    vl.startEditing()
    
    if selecao:
        for f in selecao: vl.changeAttributeValue(f.id(), idx, 'Pendente')
        vl.removeSelection()
        msg = "Seleção resetada!"
    else:
        reply = QMessageBox.question(None, 'Resetar', "Resetar Grid INTEIRO?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            for f in vl.getFeatures(): vl.changeAttributeValue(f.id(), idx, 'Pendente')
            msg = "Grid resetado!"
        else:
            vl.rollBack()
            return

    vl.commitChanges()
    vl.triggerRepaint()
    iface.messageBar().pushMessage("Grid", msg, level=3)

# --- 2. SELETOR E TEMPO ---
class SeletorCompleto(QDialog):
    def __init__(self):
        super().__init__(iface.mainWindow())
        self.setWindowTitle('Workstation V42 (Final)')
        self.setMinimumWidth(400)
        self.gpx_files = []
        layout = QVBoxLayout()
        layout.addWidget(QLabel('<b>1. Camada de Pontos (QGIS):</b>'))
        self.combo = QComboBox()
        layers = [l.name() for l in QgsProject.instance().mapLayers().values() 
                  if l.type() == QgsMapLayer.VectorLayer and l.geometryType() == QgsWkbTypes.PointGeometry]
        self.combo.addItems(layers)
        layout.addWidget(self.combo)
        layout.addWidget(QLabel('<hr>'))
        layout.addWidget(QLabel('<b>2. Arquivos GPX (Tempo):</b>'))
        self.lbl_gpx = QLabel('Nenhum selecionado')
        layout.addWidget(self.lbl_gpx)
        btn_gpx = QPushButton('📂 Selecionar GPX')
        btn_gpx.clicked.connect(self.buscar_gpx)
        layout.addWidget(btn_gpx)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.setLayout(layout)
    def buscar_gpx(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Selecione GPX", "", "GPX Files (*.gpx)")
        if files:
            self.gpx_files = files
            self.lbl_gpx.setText(f'{len(files)} arquivos.')

def calcular_tempo_mosaico(gpx_paths):
    tempo_total = timedelta(0)
    for gpx_path in gpx_paths:
        try:
            tree = ET.parse(gpx_path)
            root = tree.getroot()
            times = []
            for elem in root.iter():
                if elem.tag.endswith('time') and elem.text:
                    t_str = elem.text.strip().replace('Z', '')
                    try:
                        if '.' in t_str: t_str = t_str.split('.')[0] + '.' + t_str.split('.')[1][:6]
                        fmt = "%Y-%m-%dT%H:%M:%S.%f" if '.' in t_str else "%Y-%m-%dT%H:%M:%S"
                        times.append(datetime.strptime(t_str, fmt))
                    except: continue
            if times: tempo_total += (max(times) - min(times))
        except: continue
    return tempo_total

# --- 3. PAINEL V42 ---
class PainelDockV42(QgsDockWidget):
    def __init__(self, layer_fotos, layer_vias, kb, kl_inicial_m, tempo_obj):
        super().__init__(f'Gestão 360 - {layer_fotos.name()}', iface.mainWindow())
        self.layer = layer_fotos
        self.lyr_v = layer_vias
        self.kb = kb
        self.kl_m = kl_inicial_m
        
        crs_target = QgsCoordinateReferenceSystem(EPSG_PROJETADO)
        self.tr_f = QgsCoordinateTransform(self.layer.crs(), crs_target, QgsProject.instance())
        self.tr_v = QgsCoordinateTransform(self.lyr_v.crs(), crs_target, QgsProject.instance())
        
        ts = int(tempo_obj.total_seconds())
        h, rem = divmod(ts, 3600)
        m, s = divmod(rem, 60)
        self.tempo_str = f"{h}h {m}m {s}s"
        
        self.initUI()

    def initUI(self):
        self.setObjectName("PainelGestao360_V42")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        
        w = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignTop)
        
        # MÉTRICAS
        fr_metrics = QFrame()
        fr_metrics.setFrameShape(QFrame.StyledPanel)
        l_met = QVBoxLayout(fr_metrics)
        l_met.setSpacing(2)
        l_met.addWidget(QLabel(f'⏱️ Tempo: <b>{self.tempo_str}</b>'))
        l_met.addWidget(QLabel(f'🚗 Km Bruto: <b>{self.kb:.3f} km</b>'))
        self.lbl_metros = QLabel(f'📏 Metros Líq: {self.kl_m:.2f} m')
        self.lbl_metros.setStyleSheet("color: #d35400; font-weight: bold;")
        l_met.addWidget(self.lbl_metros)
        self.lbl_liq = QLabel(f'✅ Km Líquido:  {self.kl_m/1000:.4f} km')
        self.lbl_liq.setStyleSheet("color: #27ae60; font-weight: bold;")
        l_met.addWidget(self.lbl_liq)
        self.lbl_efi = QLabel(f'📊 Eficiência: {(self.kl_m/1000/self.kb*100):.1f}%')
        l_met.addWidget(self.lbl_efi)
        self.lbl_count = QLabel('📍 Pontos Ativos: ...')
        l_met.addWidget(self.lbl_count)
        layout.addWidget(fr_metrics)

        # GRID CONTROLE (PERSISTENTE)
        layout.addWidget(QLabel('<b>Controle de Grid:</b>'))
        h_grid = QHBoxLayout()
        btn_gera_grid = QPushButton('1. Criar e Salvar') 
        btn_gera_grid.setStyleSheet("padding: 5px;")
        btn_gera_grid.clicked.connect(lambda: gerar_grid_persistente(self.layer)) 
        h_grid.addWidget(btn_gera_grid)
        btn_check = QPushButton('✅ Check')
        btn_check.setStyleSheet("background-color: #f1c40f; font-weight: bold; padding: 5px;")
        btn_check.clicked.connect(alternar_status_grid)
        h_grid.addWidget(btn_check)
        btn_reset = QPushButton('♻️ Reset')
        btn_reset.setStyleSheet("padding: 5px;")
        btn_reset.clicked.connect(resetar_grid_inteligente)
        h_grid.addWidget(btn_reset)
        layout.addLayout(h_grid)

        # LOTE (ID)
        layout.addWidget(QLabel('<hr><b>Edição em Lote (Por ID):</b>'))
        h_layout_ids = QHBoxLayout()
        self.input_start = QLineEdit()
        self.input_start.setPlaceholderText("De (ID)")
        h_layout_ids.addWidget(self.input_start)
        self.input_end = QLineEdit()
        self.input_end.setPlaceholderText("Até (ID)")
        h_layout_ids.addWidget(self.input_end)
        layout.addLayout(h_layout_ids)
        h_layout_btns_lote = QHBoxLayout()
        btn_lote_pri = QPushButton('Validar Lote')
        btn_lote_pri.setStyleSheet("background-color: #33ff33;")
        btn_lote_pri.clicked.connect(lambda: self.aplicar_lote('Principal'))
        h_layout_btns_lote.addWidget(btn_lote_pri)
        btn_lote_red = QPushButton('Descartar Lote')
        btn_lote_red.setStyleSheet("background-color: #ff3300; color: white;")
        btn_lote_red.clicked.connect(lambda: self.aplicar_lote('Redundante'))
        h_layout_btns_lote.addWidget(btn_lote_red)
        layout.addLayout(h_layout_btns_lote)

        # SELEÇÃO
        layout.addWidget(QLabel('<hr><b>Ajuste Visual (Seleção):</b>'))
        btn_pri = QPushButton('Validar Seleção (Verde)')
        btn_pri.setStyleSheet("background-color: #2ecc71; font-weight: bold;")
        btn_pri.clicked.connect(lambda: self.set_status_selection('Principal'))
        layout.addWidget(btn_pri)
        btn_red = QPushButton('Descartar Seleção (Vermelho)')
        btn_red.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        btn_red.clicked.connect(lambda: self.set_status_selection('Redundante'))
        layout.addWidget(btn_red)

        w.setLayout(layout)
        self.setWidget(w)

    def atualizar_metricas_interno(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.layer.triggerRepaint()
            vagas = {}
            idx_vias = QgsSpatialIndex(self.lyr_v.getFeatures())
            conta_verde = 0
            iterator = self.layer.getFeatures()
            for feat in iterator:
                try: status = feat['status_360']
                except KeyError: continue
                if status != 'Principal': continue
                conta_verde += 1
                if not feat.hasGeometry(): continue
                try:
                    g_p = QgsGeometry(feat.geometry())
                    g_p.transform(self.tr_f)
                    p_at = g_p.asPoint()
                    vizinhos = idx_vias.nearestNeighbor(p_at, 1, 20.0)
                    if vizinhos:
                        id_r = vizinhos[0]
                        r_feat = next(self.lyr_v.getFeatures(QgsFeatureRequest(id_r)))
                        g_r = QgsGeometry(r_feat.geometry())
                        g_r.transform(self.tr_v)
                        pos_lin = g_r.lineLocatePoint(g_p)
                        p1 = g_r.interpolate(pos_lin).asPoint()
                        p2 = g_r.interpolate(pos_lin + 0.1).asPoint()
                        lado = "dir" if ((p2.x()-p1.x())*(p_at.y()-p1.y()) - (p2.y()-p1.y())*(p_at.x()-p1.x())) > 0 else "esq"
                        if id_r not in vagas: vagas[id_r] = {'esq': [], 'dir': []}
                        vagas[id_r][lado].append(pos_lin)
                except: continue

            if vagas:
                total_metros = sum((max(vagas[r][l]) - min(vagas[r][l])) for r in vagas for l in ['esq', 'dir'] if vagas[r][l])
            else:
                total_metros = 0.0

            self.kl_m = total_metros
            CACHE_360['kl_m'] = total_metros
            self.lbl_metros.setText(f'📏 Metros Líq: <b>{self.kl_m:.2f} m</b>')
            self.lbl_liq.setText(f'✅ Km Líquido:  {self.kl_m/1000:.4f} km')
            self.lbl_count.setText(f'📍 Pontos Ativos: {conta_verde}')
            efi = (self.kl_m/1000 / self.kb * 100) if self.kb > 0 else 0
            self.lbl_efi.setText(f'📊 Eficiência: {efi:.1f}%')
        finally:
            QApplication.restoreOverrideCursor()

    def set_status_selection(self, status):
        selecao = self.layer.selectedFeatures()
        if not selecao: return
        if not self.layer.isEditable(): self.layer.startEditing()
        idx = self.layer.fields().indexOf('status_360')
        for f in selecao: self.layer.changeAttributeValue(f.id(), idx, status)
        self.layer.commitChanges()
        self.atualizar_metricas_interno()

    def aplicar_lote(self, status):
        try:
            start_id = int(self.input_start.text())
            end_id = int(self.input_end.text())
        except ValueError:
            iface.messageBar().pushMessage("Erro", "IDs inválidos.", level=2)
            return
        expr = f'"id_seq" >= {start_id} AND "id_seq" <= {end_id}'
        req = QgsFeatureRequest().setFilterExpression(expr)
        ids_para_alterar = [f.id() for f in self.layer.getFeatures(req)]
        if not ids_para_alterar:
            iface.messageBar().pushMessage("Aviso", "Nenhum ponto encontrado.", level=1)
            return
        if not self.layer.isEditable(): self.layer.startEditing()
        idx = self.layer.fields().indexOf('status_360')
        for fid in ids_para_alterar:
            self.layer.changeAttributeValue(fid, idx, status)
        self.layer.commitChanges()
        iface.messageBar().pushMessage("Sucesso", f"{len(ids_para_alterar)} pontos atualizados!", level=3)
        self.atualizar_metricas_interno()

# --- 4. EXECUÇÃO E SUPORTE ---
def criar_painel_visual(layer, vias, kb, kl, t_tot):
    global painel_dock_v42
    docks = iface.mainWindow().findChildren(QgsDockWidget, "PainelGestao360_V42")
    for d in docks: iface.removeDockWidget(d)
    painel_dock_v42 = PainelDockV42(layer, vias, kb, kl, t_tot)
    iface.addDockWidget(Qt.RightDockWidgetArea, painel_dock_v42)
    painel_dock_v42.show()
    painel_dock_v42.atualizar_metricas_interno()

def restaurar_painel():
    if not CACHE_360['processed']: return
    try: criar_painel_visual(CACHE_360['layer'], CACHE_360['vias'], CACHE_360['kb'], CACHE_360['kl_m'], CACHE_360['t_tot'])
    except: pass

def adicionar_botao_toolbar():
    action_name = "ActionRestaurarPainel360"
    for action in iface.mainWindow().findChildren(QAction):
        if action.objectName() == action_name: iface.mainWindow().removeAction(action)
    icon = QgsApplication.getThemeIcon('/mActionOptions.svg')
    action = QAction(icon, "Abrir Workstation 360", iface.mainWindow())
    action.setObjectName(action_name)
    action.setShortcut("F12")
    action.triggered.connect(restaurar_painel)
    iface.addToolBarIcon(action)

def executar_v42_grid_save():
    d = SeletorCompleto()
    if d.exec_() != QDialog.Accepted: return
    n_lyr = d.combo.currentText()
    gpxs = d.gpx_files
    lyr_f = QgsProject.instance().mapLayersByName(n_lyr)[0]
    try: lyr_v = QgsProject.instance().mapLayersByName(NOME_VIAS_PADRAO)[0]
    except: return

    t_tot = timedelta(0)
    if gpxs: t_tot = calcular_tempo_mosaico(gpxs)
    CACHE_360['layer'] = lyr_f
    CACHE_360['vias'] = lyr_v
    CACHE_360['t_tot'] = t_tot

    gerar_ids_sequenciais(lyr_f)
    ativar_rotulos(lyr_f)

    crs = QgsCoordinateReferenceSystem(EPSG_PROJETADO)
    tr_f = QgsCoordinateTransform(lyr_f.crs(), crs, QgsProject.instance())
    tr_v = QgsCoordinateTransform(lyr_v.crs(), crs, QgsProject.instance())

    if lyr_f.fields().indexOf('status_360') == -1:
        lyr_f.dataProvider().addAttributes([QgsField("status_360", QVariant.String)])
        lyr_f.updateFields()
    idx_s = lyr_f.fields().indexOf('status_360')
    idx_vias = QgsSpatialIndex(lyr_v.getFeatures())
    
    kb_m = 0
    p_prev = None
    vagas = {}

    try:
        lyr_f.startEditing()
        for feat in lyr_f.getFeatures():
            if not feat.hasGeometry(): continue
            try:
                g_p = QgsGeometry(feat.geometry())
                g_p.transform(tr_f)
                p_at = g_p.asPoint()
                if p_prev: kb_m += p_prev.distance(p_at)
                p_prev = p_at
                viz = idx_vias.nearestNeighbor(p_at, 1, 15.0)
                if viz:
                    id_r = viz[0]
                    r_feat = next(lyr_v.getFeatures(QgsFeatureRequest(id_r)))
                    g_r = QgsGeometry(r_feat.geometry())
                    g_r.transform(tr_v)
                    pos = g_r.lineLocatePoint(g_p)
                    p1 = g_r.interpolate(pos).asPoint()
                    p2 = g_r.interpolate(pos + 0.1).asPoint()
                    lado = "dir" if ((p2.x()-p1.x())*(p_at.y()-p1.y()) - (p2.y()-p1.y())*(p_at.x()-p1.x())) > 0 else "esq"
                    if id_r not in vagas: vagas[id_r] = {'esq': [], 'dir': []}
                    if not feat['status_360']:
                        red = any(abs(v - pos) < DIST_MIN for v in vagas[id_r][lado])
                        st = "Redundante" if red else "Principal"
                        lyr_f.changeAttributeValue(feat.id(), idx_s, st)
                        if not red: vagas[id_r][lado].append(pos)
                    else:
                        if feat['status_360'] == 'Principal': vagas[id_r][lado].append(pos)
                else:
                    if not feat['status_360']: lyr_f.changeAttributeValue(feat.id(), idx_s, "Principal")
            except: continue
    except: pass
    finally:
        if lyr_f.isEditable(): lyr_f.commitChanges()

    total_metros = sum((max(vagas[r][l]) - min(vagas[r][l])) for r in vagas for l in ['esq', 'dir'] if vagas[r][l])
    CACHE_360['kb'] = kb_m/1000
    CACHE_360['kl_m'] = total_metros
    CACHE_360['processed'] = True

    sym = QgsMarkerSymbol.createSimple({'name': 'arrow', 'color': '#33ff33', 'size': '3'})
    sym.setDataDefinedAngle(QgsProperty.fromField("azimuth"))
    lyr_f.setRenderer(QgsCategorizedSymbolRenderer("status_360", [
        QgsRendererCategory("Principal", sym, "Principal"),
        QgsRendererCategory("Redundante", QgsMarkerSymbol.createSimple({'color': 'red', 'size': '1.5'}), "Redundante")
    ]))
    lyr_f.triggerRepaint()

    adicionar_botao_toolbar()
    criar_painel_visual(lyr_f, lyr_v, kb_m/1000, total_metros, t_tot)

executar_v42_grid_save()