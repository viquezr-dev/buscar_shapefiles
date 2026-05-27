# -*- coding: utf-8 -*-
"""
buscar_shapefiles - Plugin para QGIS
Desarrollado por Raul Viquez (viquezr@gmail.com)
Version: 1.2.1 - Conversión CRS + Carga por lotes
"""

import os
import tempfile
import gc

from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QMessageBox,
    QGroupBox,
    QFileDialog,
    QFrame,
    QApplication,
    QComboBox,
    QCheckBox
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QTimer
from qgis.core import (
    QgsVectorLayer,
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsVectorFileWriter
)

# CRS por defecto (EPSG:32617 - WGS 84 / UTM zone 17N)
CRS_DEFAULT = QgsCoordinateReferenceSystem("EPSG:32617")
LOTE_LIMPIAR_MEMORIA = 5
TIEMPO_ENTRE_CAPAS = 50


def limpiar_archivos_temporales(ruta_base):
    """Limpia todos los archivos temporales asociados a un shapefile"""
    try:
        base_path = os.path.splitext(ruta_base)[0]
        extensiones = ['.shp', '.shx', '.dbf', '.prj', '.qpj']
        for ext in extensiones:
            archivo = base_path + ext
            if os.path.exists(archivo):
                os.remove(archivo)
    except Exception as e:
        print(f"Error limpiando archivos: {e}")


def verificar_y_convertir_crs(capa, crs_destino=CRS_DEFAULT):
    """Verifica y convierte CRS si es necesario"""
    try:
        crs_actual = capa.crs()

        if crs_actual.authid() == crs_destino.authid():
            return capa, False

        print(
            f"🔄 Convirtiendo capa '{capa.name()}' de "
            f"{crs_actual.authid()} a {crs_destino.authid()}"
        )

        nombre_convertido = f"{capa.name()}_convertido"
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"temp_{nombre_convertido}.shp")

        transform_context = QgsProject.instance().transformContext()
        transform = QgsCoordinateTransform(
            crs_actual, crs_destino, transform_context
        )

        fields = capa.fields()
        geom_type = capa.wkbType()

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "ESRI Shapefile"
        options.fileEncoding = "UTF-8"

        writer = QgsVectorFileWriter.create(
            temp_path,
            fields,
            geom_type,
            crs_destino,
            transform_context,
            options
        )

        if writer.hasError():
            print(f"Error creando archivo temporal: {writer.errorMessage()}")
            return None, False

        features = capa.getFeatures()
        features_convertidas = 0

        for feature in features:
            new_feature = QgsFeature(fields)
            new_feature.setAttributes(feature.attributes())
            if feature.hasGeometry():
                geom = feature.geometry()
                geom.transform(transform)
                new_feature.setGeometry(geom)
            writer.addFeature(new_feature)
            features_convertidas += 1

        del writer

        capa_convertida = QgsVectorLayer(temp_path, nombre_convertido, "ogr")

        if capa_convertida.isValid():
            return capa_convertida, True

        return None, False

    except Exception as e:
        print(f"Error en conversión de CRS: {str(e)}")
        return None, False


def cargar_shapefile_seguro(
        ruta,
        nombre,
        crs_destino=CRS_DEFAULT,
        convertir_automatico=True):
    """Carga shapefile con conversión opcional"""
    try:
        if not os.path.exists(ruta):
            return None, False

        base_path = os.path.splitext(ruta)[0]
        for ext in ['.shp', '.shx', '.dbf']:
            if not os.path.exists(base_path + ext):
                return None, False

        lyr = QgsVectorLayer(ruta, nombre, "ogr")

        if not lyr.isValid():
            return None, False

        if convertir_automatico and lyr.crs().authid() != crs_destino.authid():
            lyr_convertida, fue_convertida = verificar_y_convertir_crs(
                lyr, crs_destino
            )
            if lyr_convertida and fue_convertida:
                return lyr_convertida, True
            else:
                return lyr, False

        return lyr, False

    except Exception as e:
        print(f"Error cargando shapefile {ruta}: {str(e)}")
        return None, False


class BuscarShapefileThread(QThread):
    progress_updated = pyqtSignal(int, str)
    finished_search = pyqtSignal(list)

    def __init__(self, carpeta_raiz, nombre_capa):
        super().__init__()
        self.carpeta_raiz = carpeta_raiz
        self.nombre_capa = nombre_capa
        self._is_cancelled = False

    def run(self):
        try:
            self.progress_updated.emit(0, "🔍 Buscando shapefiles...")
            shapefiles_encontrados = []

            for root, _dirs, files in os.walk(self.carpeta_raiz):
                if self._is_cancelled:
                    break
                for file in files:
                    if file.lower().endswith(".shp"):
                        nombre_archivo = os.path.splitext(file)[0]
                        if nombre_archivo.lower() == self.nombre_capa.lower():
                            ruta = os.path.join(root, file)
                            base_path = os.path.splitext(ruta)[0]
                            if all(os.path.exists(base_path + ext)
                                   for ext in ['.shp', '.shx', '.dbf']):
                                shapefiles_encontrados.append(ruta)

            self.finished_search.emit(shapefiles_encontrados)

        except Exception as e:
            print(f"Error en el hilo de búsqueda: {e}")
            self.finished_search.emit([])

    def cancel(self):
        self._is_cancelled = True


class BuscarShapefileDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔍 Buscar Shapefile")
        self.carpeta_raiz = ""
        self.crs_destino = CRS_DEFAULT
        self.convertir = True
        self.thread = None
        self.shapefiles_pendientes = []
        self.capas_cargadas = 0
        self.errores = []
        self.capas_convertidas = []

        self.setFixedWidth(600)
        self.setMinimumHeight(600)
        self.setMaximumHeight(600)
        self.setup_ui()
        self.aplicar_estilo()

    def aplicar_estilo(self):
        self.setStyleSheet("""
            QDialog { background-color: #f8fafc; }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #d0d7de;
                border-radius: 6px;
                margin-top: 10px;
                background-color: white;
            }
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:disabled {
                background-color: #cbd5e0;
                color: #64748b;
            }
            QPushButton#btn_buscar {
                background-color: #22c55e;
                color: black;
                font-size: 12px;
                padding: 10px 20px;
            }
            QPushButton#btn_buscar:hover { background-color: #16a34a; }
            QPushButton#btn_cancelar {
                background-color: #ef4444;
                color: black;
            }
            QPushButton#btn_cancelar:hover { background-color: #dc2626; }
            QLineEdit {
                border: 1px solid #cbd5e0;
                border-radius: 4px;
                padding: 6px;
            }
            QComboBox {
                border: 1px solid #cbd5e0;
                border-radius: 4px;
                padding: 5px;
            }
            QProgressBar {
                border: 1px solid #cbd5e0;
                border-radius: 4px;
                height: 20px;
                text-align: center;
                color: #2c3e50;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #22c55e;
                border-radius: 4px;
            }
        """)

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Título
        titulo = QLabel("🔍 BUSCADOR INTELIGENTE DE SHAPEFILES")
        titulo.setAlignment(Qt.AlignCenter)
        titulo.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 8px; "
            "background-color: #e8f4f8; border-radius: 4px;"
        )
        layout.addWidget(titulo)

        subtitulo = QLabel("v1.2.1 - Conversión CRS + Carga por lotes")
        subtitulo.setAlignment(Qt.AlignCenter)
        subtitulo.setStyleSheet("color: #7f8c8d; font-size: 10px;")
        layout.addWidget(subtitulo)

        # Línea
        linea = QFrame()
        linea.setFrameShape(QFrame.HLine)
        linea.setStyleSheet("background-color: #bdc3c7; max-height: 1px;")
        layout.addWidget(linea)

        # Grupo Carpeta
        grupo_carpeta = QGroupBox("📁 Carpeta de búsqueda")
        layout_carpeta = QVBoxLayout()
        self.carpeta_label = QLabel("📂 No se ha seleccionado carpeta")
        self.carpeta_label.setStyleSheet(
            "padding: 6px; border: 1px solid #cbd5e0; border-radius: 4px;"
        )
        self.btn_carpeta = QPushButton("📁 Examinar...")
        self.btn_carpeta.setMaximumWidth(120)
        self.btn_carpeta.clicked.connect(self.seleccionar_carpeta)
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.carpeta_label, 1)
        btn_layout.addWidget(self.btn_carpeta)
        layout_carpeta.addLayout(btn_layout)
        grupo_carpeta.setLayout(layout_carpeta)
        layout.addWidget(grupo_carpeta)

        # Grupo Nombre
        grupo_nombre = QGroupBox("📄 Nombre del shapefile")
        layout_nombre = QVBoxLayout()
        self.nombre_input = QLineEdit()
        self.nombre_input.setPlaceholderText(
            "ej: calles, parcelas, rios..."
        )
        self.nombre_input.textChanged.connect(self.validar_formulario)
        layout_nombre.addWidget(self.nombre_input)
        layout_nombre.addWidget(
            QLabel("💡 La búsqueda no distingue mayúsculas/minúsculas")
        )
        grupo_nombre.setLayout(layout_nombre)
        layout.addWidget(grupo_nombre)

        # Grupo CRS
        grupo_crs = QGroupBox("🗺️ Sistema de Coordenadas (CRS)")
        layout_crs = QVBoxLayout()
        self.check_convertir = QCheckBox(
            "✓ Convertir automáticamente al CRS destino"
        )
        self.check_convertir.setChecked(True)
        layout_crs.addWidget(self.check_convertir)

        crs_row = QHBoxLayout()
        crs_row.addWidget(QLabel("CRS Destino:"))
        self.crs_combo = QComboBox()
        self.crs_combo.addItem("EPSG:32617 - UTM zone 17N", "EPSG:32617")
        self.crs_combo.addItem("EPSG:4326 - WGS 84", "EPSG:4326")
        self.crs_combo.addItem("EPSG:3857 - Web Mercator", "EPSG:3857")
        self.crs_combo.addItem("Otro...", "custom")
        crs_row.addWidget(self.crs_combo, 1)
        layout_crs.addLayout(crs_row)

        self.custom_container = QHBoxLayout()
        self.custom_container.addSpacing(20)
        self.custom_container.addWidget(QLabel("Código EPSG:"))
        self.custom_crs_input = QLineEdit()
        self.custom_crs_input.setPlaceholderText("Ej: 32616")
        self.custom_crs_input.setEnabled(False)
        self.custom_container.addWidget(self.custom_crs_input)
        self.custom_container.addStretch()
        layout_crs.addLayout(self.custom_container)

        grupo_crs.setLayout(layout_crs)
        layout.addWidget(grupo_crs)

        # Progreso
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.estado_label = QLabel("")
        self.estado_label.setStyleSheet(
            "padding: 8px; background-color: #fef9e7; border-radius: 4px;"
        )
        self.estado_label.setVisible(False)
        self.estado_label.setWordWrap(True)
        layout.addWidget(self.estado_label)

        layout.addStretch()

        # Botones
        botones_layout = QHBoxLayout()
        botones_layout.setSpacing(10)

        self.btn_buscar = QPushButton("🚀 BUSCAR Y CARGAR")
        self.btn_buscar.setObjectName("btn_buscar")
        self.btn_buscar.setEnabled(False)
        self.btn_buscar.setMinimumHeight(40)
        self.btn_buscar.setMinimumWidth(160)
        self.btn_buscar.clicked.connect(self.buscar_shapefiles)

        self.btn_cancelar = QPushButton("✖ CERRAR")
        self.btn_cancelar.setObjectName("btn_cancelar")
        self.btn_cancelar.setMinimumHeight(40)
        self.btn_cancelar.setMinimumWidth(160)
        self.btn_cancelar.clicked.connect(self.reject)

        botones_layout.addStretch()
        botones_layout.addWidget(self.btn_buscar)
        botones_layout.addWidget(self.btn_cancelar)
        botones_layout.addStretch()

        layout.addLayout(botones_layout)

        self.setLayout(layout)
        self.crs_combo.currentIndexChanged.connect(
            self.cambiar_crs_seleccion
        )

    def cambiar_crs_seleccion(self, index):
        es_personalizado = self.crs_combo.currentData() == "custom"
        self.custom_crs_input.setEnabled(es_personalizado)

    def obtener_crs_destino(self):
        if self.crs_combo.currentData() == "custom":
            codigo = self.custom_crs_input.text().strip()
            if not codigo:
                return CRS_DEFAULT
            if codigo.isdigit():
                codigo = f"EPSG:{codigo}"
            crs = QgsCoordinateReferenceSystem(codigo)
            return crs if crs.isValid() else CRS_DEFAULT
        return QgsCoordinateReferenceSystem(
            self.crs_combo.currentData()
        )

    def seleccionar_carpeta(self):
        carpeta = QFileDialog.getExistingDirectory(
            self, "Selecciona la carpeta"
        )
        if carpeta:
            self.carpeta_raiz = carpeta
            self.carpeta_label.setText(f"📁 {os.path.basename(carpeta)}")
            self.validar_formulario()

    def validar_formulario(self):
        habilitar = bool(
            self.carpeta_raiz and self.nombre_input.text().strip()
        )
        self.btn_buscar.setEnabled(habilitar)

    def buscar_shapefiles(self):
        if not self.carpeta_raiz or not self.nombre_input.text().strip():
            return

        self.crs_destino = self.obtener_crs_destino()
        self.convertir = self.check_convertir.isChecked()

        widgets = [
            self.btn_buscar,
            self.btn_carpeta,
            self.nombre_input,
            self.crs_combo,
            self.check_convertir,
            self.custom_crs_input
        ]
        for w in widgets:
            w.setEnabled(False)

        self.progress_bar.setVisible(True)
        self.estado_label.setVisible(True)
        self.progress_bar.setValue(0)
        self.estado_label.setText("🚀 Buscando shapefiles...")

        nombre_capa = self.nombre_input.text().strip()
        self.thread = BuscarShapefileThread(
            self.carpeta_raiz, nombre_capa
        )
        self.thread.progress_updated.connect(self.actualizar_progreso)
        self.thread.finished_search.connect(self.iniciar_carga_por_lotes)
        self.thread.start()

    def actualizar_progreso(self, valor, mensaje):
        self.progress_bar.setValue(valor)
        self.estado_label.setText(mensaje)

    def iniciar_carga_por_lotes(self, shapefiles):
        if not shapefiles:
            self.busqueda_completada()
            return

        self.shapefiles_pendientes = shapefiles
        self.capas_cargadas = 0
        self.errores = []
        self.capas_convertidas = []
        self.progress_bar.setMaximum(len(shapefiles))
        self.progress_bar.setValue(0)
        self.cargar_siguiente_lote()

    def cargar_siguiente_lote(self):
        if not self.shapefiles_pendientes:
            self.busqueda_completada()
            return

        lote = self.shapefiles_pendientes[:LOTE_LIMPIAR_MEMORIA]
        restantes = self.shapefiles_pendientes[LOTE_LIMPIAR_MEMORIA:]
        self.shapefiles_pendientes = restantes

        total = self.capas_cargadas + len(lote) + len(restantes)

        for ruta in lote:
            nombre = os.path.splitext(os.path.basename(ruta))[0]
            actual = self.capas_cargadas + 1
            self.estado_label.setText(
                f"📄 Cargando {nombre}... ({actual} de {total})"
            )
            QApplication.processEvents()

            resultado = cargar_shapefile_seguro(
                ruta, nombre, self.crs_destino, self.convertir
            )

            if resultado:
                capa, fue_convertida = resultado
                if capa:
                    QgsProject.instance().addMapLayer(capa)
                    self.capas_cargadas += 1
                    self.progress_bar.setValue(self.capas_cargadas)
                    if fue_convertida:
                        self.capas_convertidas.append(nombre)
                        if hasattr(capa, 'source'):
                            limpiar_archivos_temporales(capa.source())
                else:
                    self.errores.append(nombre)
            else:
                self.errores.append(nombre)

            gc.collect()

        QTimer.singleShot(TIEMPO_ENTRE_CAPAS, self.cargar_siguiente_lote)

    def busqueda_completada(self):
        self.progress_bar.setVisible(False)
        self.estado_label.setVisible(False)

        widgets = [
            self.btn_buscar,
            self.btn_carpeta,
            self.nombre_input,
            self.crs_combo,
            self.check_convertir
        ]
        for w in widgets:
            w.setEnabled(True)

        self.custom_crs_input.setEnabled(
            self.crs_combo.currentData() == "custom"
        )

        if self.capas_cargadas == 0:
            QMessageBox.information(
                self, "Resultado", "No se encontraron shapefiles"
            )
        else:
            mensaje = (
                f"✅ {self.capas_cargadas} shapefile(s) cargados "
                "correctamente."
            )
            if self.capas_convertidas:
                mensaje += (
                    f"\n\n🔄 {len(self.capas_convertidas)} convertido(s) a "
                    f"{self.crs_destino.authid()}"
                )
            if self.errores:
                mensaje += f"\n\n⚠️ {len(self.errores)} error(es)"
            QMessageBox.information(self, "✅ Completado", mensaje)

        gc.collect()


class BuscarShapefile:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            from qgis.PyQt.QtGui import QIcon
            self.action = QAction(
                QIcon(icon_path),
                "🔍 Buscar Shapefile",
                self.iface.mainWindow()
            )
        else:
            self.action = QAction(
                "🔍 Buscar Shapefile",
                self.iface.mainWindow()
            )
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("Buscar Shapefile", self.action)

    def unload(self):
        self.iface.removePluginMenu("Buscar Shapefile", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        dialog = BuscarShapefileDialog(self.iface.mainWindow())
        dialog.exec_()
