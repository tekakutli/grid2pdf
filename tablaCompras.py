#!/usr/bin/env python3
"""
tablaCompras.py
----------------
Genera un PDF con una tabla de compras organizada por secciones.
Los datos se cargan desde un archivo JSON con soporte multi-sheet (formato tksheet).
La interacción principal es a través de una GUI con tksheet.

Uso:
  python tablaCompras.py                        # genera PDF + abre editor gráfico
  python tablaCompras.py --json otro.json       # usa otro archivo JSON
  python tablaCompras.py --export-pdf           # solo genera PDF (sin editor)
"""
# https://chat.deepseek.com/a/chat/s/f5c3322e-c02c-486a-a517-e519aad1d661

import os
import sys
import argparse
import json
import re
import tempfile
from datetime import datetime
from math import ceil
from PIL import Image, ImageDraw, ImageFont

# Intentar importar tksheet y tkinter
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    import tksheet
    TKINTER_AVAILABLE = True
except ImportError as e:
    TKINTER_AVAILABLE = False
    print("⚠️  No se pudo importar tkinter/tksheet.")
    print("   Para usar el editor de cantidades, instala:")
    print("   pip install tksheet")
    print("   (tkinter normalmente viene con Python)")

# ==================== CONFIGURACIÓN ====================
DEFAULT_JSON = "tabla_compras.json"
HEADERS = ["Producto", "Descripción", "Costo", "Mes 1", "Mes 2", "Mes 3", "Mes 4"]
VERBOSE_HEADERS = ["Artículo", "Precio × Cantidad", "Subtotal"]

# Modo de generación (activa/desactiva dos páginas por hoja)
TWO_PAGES_PER_SHEET = True   # Cambia a False para el modo normal

# Tamaños de página a 300 DPI
if TWO_PAGES_PER_SHEET:
    PAGE_WIDTH = 3300   # 11 pulgadas
    PAGE_HEIGHT = 2550  # 8.5 pulgadas
else:
    PAGE_WIDTH = 2550
    PAGE_HEIGHT = 3300

DPI = 300

# Márgenes en centímetros
MARGIN_LEFT_CM = 2.0
MARGIN_RIGHT_CM = 2.0
MARGIN_TOP_CM = 2.0
MARGIN_BOTTOM_CM = 2.0

MARGIN_LEFT = int(MARGIN_LEFT_CM * DPI / 2.54)
MARGIN_RIGHT = int(MARGIN_RIGHT_CM * DPI / 2.54)
MARGIN_TOP = int(MARGIN_TOP_CM * DPI / 2.54)
MARGIN_BOTTOM = int(MARGIN_BOTTOM_CM * DPI / 2.54)

GAP_BETWEEN_PAGES = 20  # píxeles
PADDING_TOP = 8
PADDING_BOTTOM = 20
FOOTER_HEIGHT = 60

FONT_SIZE = 50
FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
# En Windows: "C:/Windows/Fonts/Arial.ttf"

TABLE_LINE_COLOR = "black"
LINE_WIDTH = 2

OUTPUT_PDF = "tabla_compras.pdf"
OUTPUT_VERBOSE_PDF = "tabla_compras_verbose.pdf"
OUTPUT_VERBOSE_SHORT_PDF = "tabla_compras_verbose_short.pdf"

# ===================================================

# Calcular dimensiones virtuales según el modo
if TWO_PAGES_PER_SHEET:
    avail_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    avail_height = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    page_width = (avail_width - GAP_BETWEEN_PAGES) // 2
    VIRTUAL_WIDTH = 2550
    VIRTUAL_HEIGHT = int(VIRTUAL_WIDTH * (avail_height / page_width))
else:
    VIRTUAL_WIDTH = 2550
    VIRTUAL_HEIGHT = 3300

# ==================== CARGA DE DATOS DESDE JSON ====================

def load_sections_from_json(json_file):
    """
    Carga los datos desde el archivo JSON y los convierte en la estructura SECTIONS.
    El archivo JSON debe existir y contener una hoja 'items' con columnas:
    nombre, descripcion, precio, seccion.
    """
    if not os.path.exists(json_file):
        print(f"❌ Error: El archivo JSON '{json_file}' no existe.")
        print("   Asegúrate de tener el archivo con la hoja 'items'.")
        sys.exit(1)

    # Cargar el JSON
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if "items" not in data:
        print(f"❌ Error: El archivo JSON '{json_file}' no contiene la hoja 'items'.")
        print("   La hoja 'items' debe existir con columnas: nombre, descripcion, precio, seccion.")
        sys.exit(1)

    rows = data["items"]
    if len(rows) < 2:
        print("⚠️  La hoja 'items' no contiene datos (solo encabezados).")
        return []

    # La primera fila es el encabezado, el resto son datos
    # Columnas: 0=nombre, 1=descripcion, 2=precio, 3=seccion
    sections_dict = {}
    for row in rows[1:]:
        if len(row) < 4:
            continue  # Omitir filas incompletas
        nombre = str(row[0]).strip()
        descripcion = str(row[1]).strip()
        try:
            precio = float(row[2])
        except (ValueError, TypeError):
            precio = 0
        seccion = str(row[3]).strip() if len(row) > 3 else "General"

        if not nombre:
            continue

        if seccion not in sections_dict:
            sections_dict[seccion] = []
        sections_dict[seccion].append((nombre, descripcion, precio))

    # Convertir a lista de secciones ordenada
    sections = []
    for sec_name, items in sections_dict.items():
        sections.append({"name": sec_name, "items": items})

    return sections

# Cargar secciones al inicio (se puede sobrescribir con --json)
SECTIONS = load_sections_from_json(DEFAULT_JSON)

# ==================== FUNCIONES DEL SCRIPT ====================

def get_text_bbox(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)

def draw_table_page(elements_chunk, page_num, total_pages, font_size,
                    virtual_width, virtual_height, use_margins=True, footer_height=0,
                    headers=None, col_widths=None):
    """Dibuja una página virtual con la tabla."""
    img = Image.new('RGB', (virtual_width, virtual_height), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(FONT_PATH, font_size) if FONT_PATH else ImageFont.load_default()
        try:
            font_bold = ImageFont.truetype(FONT_PATH, font_size, encoding="unic", index=1)
        except:
            font_bold = font
    except:
        font = ImageFont.load_default()
        font_bold = font

    if use_margins:
        v_margin_left = int(MARGIN_LEFT_CM * DPI / 2.54)
        v_margin_right = int(MARGIN_RIGHT_CM * DPI / 2.54)
        v_margin_top = int(MARGIN_TOP_CM * DPI / 2.54)
        v_margin_bottom = int(MARGIN_BOTTOM_CM * DPI / 2.54)
    else:
        v_margin_left = 0
        v_margin_right = 0
        v_margin_top = 0
        v_margin_bottom = footer_height

    total_width = virtual_width - v_margin_left - v_margin_right

    if col_widths is None:
        col_widths = [
            int(total_width * 0.40),
            int(total_width * 0.12),
            int(total_width * 0.12),
            int(total_width * 0.09),
            int(total_width * 0.09),
            int(total_width * 0.09),
            int(total_width * 0.09),
        ]
    diff = total_width - sum(col_widths)
    if diff != 0:
        col_widths[-1] += diff

    x_positions = [v_margin_left]
    for w in col_widths[:-1]:
        x_positions.append(x_positions[-1] + w)

    bbox = get_text_bbox(draw, "Ay", font)
    h = bbox[3] - bbox[1]
    row_height = h + PADDING_TOP + PADDING_BOTTOM
    header_height = row_height + 5

    table_available_height = virtual_height - v_margin_top - v_margin_bottom - header_height
    max_rows = table_available_height // row_height
    if max_rows <= 0:
        max_rows = 1

    if len(elements_chunk) > max_rows:
        elements_chunk = elements_chunk[:max_rows]

    total_table_height = header_height + len(elements_chunk) * row_height

    for x in x_positions[1:]:
        draw.line([x, v_margin_top, x, v_margin_top + total_table_height],
                  fill=TABLE_LINE_COLOR, width=LINE_WIDTH)

    y = v_margin_top
    draw.rectangle(
        [v_margin_left, y, v_margin_left + total_width, y + header_height],
        fill="lightgray",
        outline=TABLE_LINE_COLOR,
        width=LINE_WIDTH
    )
    if headers is None:
        headers = HEADERS
    for i, header in enumerate(headers):
        x = x_positions[i]
        bbox = get_text_bbox(draw, header, font_bold)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        top_offset = bbox[1]
        text_x = x + (col_widths[i] - tw) / 2
        text_y = y + (header_height - th) / 2 - top_offset
        draw.text((text_x, text_y), header, font=font_bold, fill="black")

    draw.line([v_margin_left, y + header_height, v_margin_left + total_width, y + header_height],
              fill=TABLE_LINE_COLOR, width=LINE_WIDTH)

    y = v_margin_top + header_height
    for idx, elem in enumerate(elements_chunk):
        row_y = y + idx * row_height

        if elem["type"] == "section":
            draw.rectangle(
                [v_margin_left, row_y, v_margin_left + total_width, row_y + row_height],
                fill="dimgray",
                outline=None
            )
            section_name = elem["name"]
            bbox = get_text_bbox(draw, section_name, font_bold)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = v_margin_left + (total_width - tw) / 2
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), section_name, font=font_bold, fill="white")

        elif elem["type"] in ("subtotal", "total"):
            if elem["type"] == "subtotal":
                fill_color = "lightcyan"
            else:
                fill_color = "lightgray"
            draw.rectangle(
                [v_margin_left, row_y, v_margin_left + total_width, row_y + row_height],
                fill=fill_color,
                outline=TABLE_LINE_COLOR,
                width=1
            )
            label = elem["name"]
            subtotal_val = elem["data"][2]
            bbox = get_text_bbox(draw, label, font_bold)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[0] + 5
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), label, font=font_bold, fill="black")
            total_str = f"${subtotal_val:.2f}"
            bbox = get_text_bbox(draw, total_str, font_bold)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[2] + (col_widths[2] - tw) / 2
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), total_str, font=font_bold, fill="black")

        else:
            producto, descripcion, costo = elem["data"]
            bbox = get_text_bbox(draw, producto, font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[0] + 5
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), producto, font=font, fill="black")

            bbox = get_text_bbox(draw, descripcion, font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[1] + 5
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), descripcion, font=font, fill="black")

            costo_str = f"${costo:.2f}"
            bbox = get_text_bbox(draw, costo_str, font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[2] + (col_widths[2] - tw) / 2
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), costo_str, font=font, fill="black")

            draw.line([v_margin_left, row_y + row_height, v_margin_left + total_width, row_y + row_height],
                      fill=TABLE_LINE_COLOR, width=1)

    draw.line([v_margin_left, v_margin_top, v_margin_left, v_margin_top + total_table_height],
              fill=TABLE_LINE_COLOR, width=LINE_WIDTH)
    draw.line([v_margin_left + total_width, v_margin_top,
               v_margin_left + total_width, v_margin_top + total_table_height],
              fill=TABLE_LINE_COLOR, width=LINE_WIDTH)

    footer_text = f"Página {page_num} de {total_pages}"
    bbox = get_text_bbox(draw, footer_text, font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    top_offset = bbox[1]

    if footer_height > 0:
        footer_y = virtual_height - footer_height
        text_x = (virtual_width - tw) // 2
        text_y = footer_y + (footer_height - th) // 2 - top_offset
        draw.text((text_x, text_y), footer_text, font=font, fill="black")
    else:
        text_x = virtual_width - v_margin_right - tw
        text_y = virtual_height - v_margin_bottom - th - top_offset
        draw.text((text_x, text_y), footer_text, font=font, fill="black")

    return img

def create_physical_page(virtual_pages):
    """Crea una hoja física a partir de una o dos páginas virtuales."""
    if not TWO_PAGES_PER_SHEET:
        return virtual_pages[0]

    physical = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')

    avail_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    avail_height = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    virtual_w, virtual_h = virtual_pages[0].size

    if len(virtual_pages) == 2:
        page_width = (avail_width - GAP_BETWEEN_PAGES) // 2
        gap = GAP_BETWEEN_PAGES
        scale = page_width / virtual_w
        if virtual_h * scale > avail_height:
            scale = avail_height / virtual_h
        new_w = int(virtual_w * scale)
        new_h = int(virtual_h * scale)
        x_positions = [MARGIN_LEFT, MARGIN_LEFT + page_width + gap]
        y_offset = MARGIN_TOP + (avail_height - new_h) // 2
    else:
        scale = min(avail_width / virtual_w, avail_height / virtual_h)
        new_w = int(virtual_w * scale)
        new_h = int(virtual_h * scale)
        x_positions = [MARGIN_LEFT]
        y_offset = MARGIN_TOP + (avail_height - new_h) // 2

    for idx, vimg in enumerate(virtual_pages):
        vimg_resized = vimg.resize((new_w, new_h), Image.Resampling.LANCZOS)
        physical.paste(vimg_resized, (x_positions[idx], y_offset))

    return physical

def save_pdf_with_png_fallback(pages, output_pdf, dpi):
    try:
        pages[0].save(
            output_pdf,
            save_all=True,
            append_images=pages[1:],
            resolution=dpi,
            title="Tabla de Compras"
        )
        return True
    except KeyError as e:
        print(f"Error al guardar PDF: {e}. Usando método alternativo...")
        temp_files = []
        try:
            for i, page in enumerate(pages):
                temp_png = tempfile.NamedTemporaryFile(suffix=f"_page_{i+1}.png", delete=False)
                temp_png.close()
                temp_files.append(temp_png.name)
                page.save(temp_png.name, "PNG", dpi=(dpi, dpi))
            png_pages = []
            for png_file in temp_files:
                img = Image.open(png_file)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                png_pages.append(img)
            png_pages[0].save(
                output_pdf,
                save_all=True,
                append_images=png_pages[1:],
                title="Tabla de Compras"
            )
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except:
                    pass
            return True
        except Exception as e2:
            print(f"El método alternativo también falló: {e2}")
            return False

def generate_pdf_from_elements(elements, headers, col_widths, output_pdf, virtual_width, virtual_height):
    """Genera un PDF a partir de una lista de elementos."""
    total_elements = len(elements)
    print("==============================================")
    print(f"Generando PDF: {output_pdf}")
    print("==============================================")
    print(f"Total de elementos: {total_elements}")
    print(f"Tamaño de fuente: {FONT_SIZE} px")
    print(f"Padding superior: {PADDING_TOP} px, inferior: {PADDING_BOTTOM} px")
    print(f"Márgenes (cm): Izq={MARGIN_LEFT_CM}, Der={MARGIN_RIGHT_CM}, Sup={MARGIN_TOP_CM}, Inf={MARGIN_BOTTOM_CM}")
    print(f"Modo dos páginas por hoja: {'SÍ' if TWO_PAGES_PER_SHEET else 'NO'}")
    if TWO_PAGES_PER_SHEET:
        print(f"Altura del pie de página: {FOOTER_HEIGHT} px")
    print(f"Tamaño virtual: {virtual_width}×{virtual_height} px")

    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE) if FONT_PATH else ImageFont.load_default()
    except:
        font = ImageFont.load_default()
    bbox = get_text_bbox(temp_draw, "Ay", font)
    h = bbox[3] - bbox[1]
    row_height = h + PADDING_TOP + PADDING_BOTTOM
    header_height = row_height + 5

    if TWO_PAGES_PER_SHEET:
        v_margin_top = 0
        v_margin_bottom = FOOTER_HEIGHT
    else:
        v_margin_top = int(MARGIN_TOP_CM * DPI / 2.54)
        v_margin_bottom = int(MARGIN_BOTTOM_CM * DPI / 2.54)

    available_height = virtual_height - v_margin_top - v_margin_bottom - header_height
    rows_per_page = available_height // row_height
    if rows_per_page <= 0:
        rows_per_page = 1

    print(f"Filas por página virtual: {rows_per_page}")

    virtual_pages_content = []
    current_page = []
    for elem in elements:
        if len(current_page) < rows_per_page:
            current_page.append(elem)
        else:
            virtual_pages_content.append(current_page)
            current_page = [elem]
    if current_page:
        virtual_pages_content.append(current_page)

    total_virtual_pages = len(virtual_pages_content)
    print(f"Páginas virtuales necesarias: {total_virtual_pages}")

    virtual_images = []
    for page_num, chunk in enumerate(virtual_pages_content, start=1):
        print(f"--- Generando página virtual {page_num}/{total_virtual_pages} ({len(chunk)} filas) ---")
        use_margins = not TWO_PAGES_PER_SHEET
        footer_h = FOOTER_HEIGHT if TWO_PAGES_PER_SHEET else 0
        img = draw_table_page(chunk, page_num, total_virtual_pages, FONT_SIZE,
                              virtual_width, virtual_height, use_margins, footer_h,
                              headers=headers, col_widths=col_widths)
        virtual_images.append(img)

    if TWO_PAGES_PER_SHEET:
        physical_pages = []
        for i in range(0, len(virtual_images), 2):
            pair = virtual_images[i:i+2]
            physical = create_physical_page(pair)
            physical_pages.append(physical)
        total_physical_pages = len(physical_pages)
        print(f"Páginas físicas (hojas) generadas: {total_physical_pages}")
    else:
        physical_pages = virtual_images
        total_physical_pages = len(physical_pages)

    print("\nGuardando PDF...")
    success = save_pdf_with_png_fallback(physical_pages, output_pdf, DPI)
    if success:
        print(f"✓ PDF generado: {output_pdf}")
        print(f"  Total de hojas: {total_physical_pages}")
    else:
        print("❌ Falló la generación del PDF")
    print("==============================================")

def generate_pdf():
    """Genera el PDF con la tabla de compras (comportamiento normal)."""
    elements = []
    for sec in SECTIONS:
        elements.append({"type": "section", "name": sec["name"]})
        for item in sec["items"]:
            elements.append({"type": "item", "data": item})

    default_col_widths = [
        int(VIRTUAL_WIDTH * 0.40),
        int(VIRTUAL_WIDTH * 0.12),
        int(VIRTUAL_WIDTH * 0.12),
        int(VIRTUAL_WIDTH * 0.09),
        int(VIRTUAL_WIDTH * 0.09),
        int(VIRTUAL_WIDTH * 0.09),
        int(VIRTUAL_WIDTH * 0.09),
    ]
    generate_pdf_from_elements(elements, HEADERS, default_col_widths, OUTPUT_PDF, VIRTUAL_WIDTH, VIRTUAL_HEIGHT)

# ==================== EDITOR COMPLETO CON TKSHEET ====================

def open_editor_window(sections, json_file):
    """Abre una ventana con tksheet para editar cantidades, cargar hojas, calcular y exportar PDFs."""
    if not TKINTER_AVAILABLE:
        print("❌ No se puede abrir el editor porque falta tkinter/tksheet.")
        print("   Instala tksheet: pip install tksheet")
        return

    # Preparar datos para la tabla de cantidades
    # Columnas: Nombre, Descripción, Precio, Cantidad (editable, tipo int)
    data = []
    for sec in sections:
        for nombre, desc, precio in sec["items"]:
            data.append([nombre, desc, precio, 0])  # <- 0 entero

    # Crear ventana principal
    root = tk.Tk()
    root.title("Gestor de compras - tksheet")
    root.geometry("900x650")

    # Variable para detectar cambios no guardados
    dirty = False
    # Variable para recordar el nombre de la hoja actual (para pre-llenar el diálogo de guardado)
    current_sheet_name = None

    # ========== FRAME SUPERIOR: selector y botones ==========
    top_frame = ttk.Frame(root)
    top_frame.pack(fill=tk.X, padx=10, pady=5)

    # Etiqueta de estado (indicador de cambios)
    status_label = ttk.Label(top_frame, text="Guardado", foreground="green")
    status_label.pack(side=tk.RIGHT, padx=10)

    def set_dirty(flag):
        nonlocal dirty
        dirty = flag
        if dirty:
            status_label.config(text="Modificado", foreground="red")
        else:
            status_label.config(text="Guardado", foreground="green")

    # Función para cargar la hoja seleccionada en el combobox
    def cargar_hoja(selected):
        """Carga la hoja 'selected' en la tabla."""
        nonlocal data, current_sheet_name
        json_data = load_json_data(json_file)
        if selected not in json_data:
            messagebox.showerror("Error", f"La hoja '{selected}' no existe.")
            return
        hoja = json_data[selected]
        if not hoja or len(hoja) < 2:
            messagebox.showinfo("Vacío", f"La hoja '{selected}' está vacía.")
            return
        # hoja tiene formato [["nombre","cantidad"], ["item1", 2], ...]
        cantidad_dict = {}
        for row in hoja[1:]:  # omitir encabezado
            if len(row) >= 2:
                val = row[1]
                try:
                    if isinstance(val, float):
                        val = int(val)
                    elif isinstance(val, str):
                        if val.isdigit():
                            val = int(val)
                        else:
                            val = 0
                    else:
                        val = int(val)
                except (ValueError, TypeError):
                    val = 0
                cantidad_dict[row[0].strip()] = val
        # Actualizar los datos de la tabla
        new_data = []
        for row in data:
            name = row[0]
            qty = cantidad_dict.get(name, 0)
            new_data.append([name, row[1], row[2], qty])
        sheet.set_sheet_data(new_data)
        sheet.refresh()
        set_dirty(False)
        current_sheet_name = selected  # Recordar el nombre de la hoja cargada
        messagebox.showinfo("Cargado", f"Hoja '{selected}' cargada correctamente.")

    def load_json_data(fname):
        try:
            with open(fname, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    ttk.Label(top_frame, text="Hoja:").pack(side=tk.LEFT, padx=5)

    # Combobox para seleccionar hojas existentes (excepto "items")
    sheet_names = [k for k in load_json_data(json_file).keys() if k != "items"]
    sheet_var = tk.StringVar()
    combobox = ttk.Combobox(top_frame, textvariable=sheet_var, values=sheet_names, state="readonly", width=20)
    combobox.pack(side=tk.LEFT, padx=5)
    # No seleccionar ninguna hoja automáticamente; dejar vacío para evitar confusiones
    combobox.set('')

    def on_cargar():
        """Carga la hoja seleccionada en la tabla de cantidades, con advertencia si hay cambios sin guardar."""
        selected = sheet_var.get()
        if not selected:
            messagebox.showwarning("Selección", "No hay hoja seleccionada.")
            return
        if dirty:
            respuesta = messagebox.askyesnocancel(
                "Cambios sin guardar",
                "Hay cambios sin guardar. ¿Quieres guardarlos antes de cargar otra hoja?\n"
                "Sí = guardar, No = descartar, Cancelar = cancelar carga"
            )
            if respuesta is None:  # Cancelar
                return
            if respuesta:  # Sí
                on_guardar()
                # Después de guardar, dirty se vuelve False, pero puede haber error al guardar.
                # Si el guardado falló, deberíamos cancelar la carga? Asumimos que guardó bien.
            # Si No, descartamos cambios y continuamos.
        # Cargar la hoja
        cargar_hoja(selected)

    ttk.Button(top_frame, text="Cargar", command=on_cargar).pack(side=tk.LEFT, padx=5)

    def on_guardar():
        """Guarda la hoja actual en el JSON con un nombre proporcionado por el usuario."""
        nonlocal current_sheet_name
        # Pedir nombre para la hoja, pre-llenar con el nombre actual si existe
        nombre_hoja = simpledialog.askstring(
            "Guardar hoja",
            "Ingresa un nombre para la hoja:",
            parent=root,
            initialvalue=current_sheet_name if current_sheet_name else ""
        )
        if nombre_hoja is None:  # Cancelar
            return
        nombre_hoja = nombre_hoja.strip()
        if not nombre_hoja:
            messagebox.showwarning("Nombre vacío", "El nombre de la hoja no puede estar vacío.")
            return

        # Verificar si ya existe una hoja con ese nombre
        json_data = load_json_data(json_file)
        if nombre_hoja in json_data and nombre_hoja != "items":
            if not messagebox.askyesno("Sobrescribir", f"Ya existe una hoja con el nombre '{nombre_hoja}'. ¿Sobrescribir?"):
                return

        rows = sheet.get_sheet_data()
        new_sheet = [["nombre", "cantidad"]]
        for row in rows:
            if len(row) >= 4:
                nombre = str(row[0]).strip()
                try:
                    val = row[3]
                    if isinstance(val, float):
                        val = int(val)
                    elif isinstance(val, str):
                        try:
                            val = int(float(val))
                        except ValueError:
                            val = 0
                    else:
                        val = int(val)
                except (ValueError, TypeError):
                    val = 0
                if nombre:
                    new_sheet.append([nombre, val])

        # Añadir la hoja al JSON
        json_data[nombre_hoja] = new_sheet
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        # Actualizar combobox
        new_sheet_names = [k for k in json_data.keys() if k != "items"]
        combobox['values'] = new_sheet_names
        if nombre_hoja in new_sheet_names:
            combobox.set(nombre_hoja)
        current_sheet_name = nombre_hoja  # Recordar el nombre usado
        set_dirty(False)
        messagebox.showinfo("Guardado", f"Hoja '{nombre_hoja}' guardada en {json_file}")

    ttk.Button(top_frame, text="Guardar", command=on_guardar).pack(side=tk.LEFT, padx=5)

    def on_eliminar():
        """Elimina la hoja actual del JSON."""
        nonlocal current_sheet_name
        selected = sheet_var.get()
        if not selected:
            messagebox.showwarning("Selección", "No hay hoja seleccionada para eliminar.")
            return
        if not messagebox.askyesno("Confirmar eliminación", f"¿Seguro que quieres eliminar la hoja '{selected}'?"):
            return
        # Cargar JSON, eliminar clave, guardar
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data_json = json.load(f)
        except FileNotFoundError:
            data_json = {"items": []}
        if selected in data_json:
            del data_json[selected]
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(data_json, f, indent=2, ensure_ascii=False)
            # Actualizar combobox
            new_sheet_names = [k for k in data_json.keys() if k != "items"]
            combobox['values'] = new_sheet_names
            if new_sheet_names:
                combobox.set(new_sheet_names[0])
            else:
                combobox.set('')
            # Si la hoja eliminada era la actual, limpiar el nombre recordado
            if selected == current_sheet_name:
                current_sheet_name = None
            # Limpiar la tabla (poner cantidades a 0)
            new_data = []
            for row in data:
                new_data.append([row[0], row[1], row[2], 0])
            sheet.set_sheet_data(new_data)
            sheet.refresh()
            set_dirty(False)
            messagebox.showinfo("Eliminado", f"Hoja '{selected}' eliminada.")
        else:
            messagebox.showerror("Error", f"La hoja '{selected}' no existe.")

    ttk.Button(top_frame, text="Eliminar hoja", command=on_eliminar).pack(side=tk.LEFT, padx=5)
    ttk.Button(top_frame, text="Cancelar", command=root.destroy).pack(side=tk.LEFT, padx=5)

    # ========== NOTEBOOK: Cantidades y Resultados ==========
    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    # Pestaña 1: Tabla de cantidades
    tab1 = ttk.Frame(notebook)
    notebook.add(tab1, text="Cantidades")

    sheet = tksheet.Sheet(tab1,
                          data=data,
                          headers=["Nombre", "Descripción", "Precio", "Cantidad"],
                          column_widths=[200, 150, 80, 100],
                          width=860, height=400)
    sheet.pack(fill=tk.BOTH, expand=True)

    # Habilitar TODAS las interacciones (selección, edición, teclado, etc.)
    sheet.enable_bindings("all")

    # Hacer columnas A, B, C (0,1,2) de solo lectura (Nombre, Descripción, Precio)
    sheet.readonly("A:C")

    # ========== CORRECCIÓN DEFINITIVA DEL INDICADOR DE CAMBIOS ==========
    # El evento oficial de tksheet para detectar CUALQUIER modificación es <<SheetModified>>
    def on_sheet_modified(event):
        set_dirty(True)

    # Enlazar el evento oficial de modificación
    sheet.bind("<<SheetModified>>", on_sheet_modified)

    # También podemos usar extra_bindings como respaldo para asegurar la detección
    try:
        # end_edit_cell se dispara después de cada edición de celda
        sheet.extra_bindings("end_edit_cell", lambda event: set_dirty(True))
    except AttributeError:
        pass  # Si no existe, ignorar
    # ========== FIN CORRECCIÓN ==========

    # Función de búsqueda (Ctrl+F)
    def buscar():
        """Abre un diálogo para buscar texto en la columna 'Nombre'."""
        busqueda = simpledialog.askstring("Buscar", "Buscar producto:", parent=root)
        if busqueda is None or not busqueda.strip():
            return
        texto = busqueda.strip()
        # Buscar en la columna 0 (nombre)
        # Obtener todos los datos
        filas = sheet.get_sheet_data()
        encontrado = False
        for idx, fila in enumerate(filas):
            if len(fila) > 0 and texto.lower() in str(fila[0]).lower():
                # Seleccionar la celda (fila, columna 0)
                sheet.select_cell(idx, 0)
                sheet.see(idx, 0)  # Desplazar para que sea visible
                encontrado = True
                break
        if not encontrado:
            messagebox.showinfo("Búsqueda", f"No se encontró '{texto}' en la lista de productos.")

    # Binding de teclado Ctrl+F (Linux/Windows) o Cmd+F (macOS)
    root.bind_all("<Control-f>", lambda event: buscar())
    root.bind_all("<Command-f>", lambda event: buscar())  # Para macOS

    # Dar foco a la hoja para que reciba eventos de teclado
    sheet.focus_set()

    # Pestaña 2: Resultados (solo lectura pero seleccionable)
    tab2 = ttk.Frame(notebook)
    notebook.add(tab2, text="Resultados")

    result_sheet = tksheet.Sheet(tab2,
                                 data=[],
                                 headers=["Artículo", "Precio × Cantidad", "Subtotal"],
                                 column_widths=[300, 150, 100],
                                 width=860, height=400)
    result_sheet.pack(fill=tk.BOTH, expand=True)

    # Hacer la hoja de resultados de solo lectura pero seleccionable
    result_sheet.enable_bindings("all")        # Habilita todas las interacciones
    result_sheet.disable_bindings("edit_cell") # Deshabilita solo la edición

    # ========== BOTONES DE ACCIÓN INFERIOR ==========
    bottom_frame = ttk.Frame(root)
    bottom_frame.pack(fill=tk.X, padx=10, pady=5)

    # --- FUNCIONES DE CÁLCULO Y EXPORTACIÓN ---

    def calcular_resultados(short=False):
        """Calcula el total y muestra los resultados en la pestaña de resultados, agrupados por sección."""
        # Leer cantidades de la hoja principal
        rows = sheet.get_sheet_data()
        cantidad_dict = {}
        for row in rows:
            if len(row) >= 4:
                nombre = str(row[0]).strip()
                try:
                    qty = float(row[3]) if row[3] is not None else 0.0
                except (ValueError, TypeError):
                    qty = 0.0
                if nombre:
                    cantidad_dict[nombre] = qty

        # Construir la tabla de resultados
        result_data = []
        total_general = 0.0

        # Iterar por secciones en el orden definido en SECTIONS
        for sec in SECTIONS:
            sec_items = []
            sec_total = 0.0
            # Artículos de esta sección
            for name, _, price in sec["items"]:
                qty = cantidad_dict.get(name, 0.0)
                if qty > 0 or not short:   # mostrar todos si verbose, solo >0 si short
                    subtotal = price * qty
                    if qty > 0:
                        sec_total += subtotal
                        total_general += subtotal
                    if short and qty == 0:
                        continue
                    sec_items.append([name, f"{price:.2f} × {qty:.2f}", subtotal])
            if sec_items:
                # Encabezado de sección (estilo visual)
                result_data.append([f"--- {sec['name']} ---", "", ""])
                result_data.extend(sec_items)
                # Fila de subtotal de la sección
                result_data.append([f"--- Subtotal {sec['name']} ---", "", sec_total])

        # Fila de total general
        result_data.append(["TOTAL", "", total_general])

        # Actualizar la hoja de resultados y cambiar a la pestaña
        result_sheet.set_sheet_data(result_data)
        result_sheet.refresh()
        notebook.select(tab2)

    def export_pdf(short=False):
        """Genera PDF detallado o corto a partir de los datos actuales."""
        rows = sheet.get_sheet_data()
        cantidad_dict = {}
        for row in rows:
            if len(row) >= 4:
                nombre = str(row[0]).strip()
                try:
                    qty = float(row[3]) if row[3] is not None else 0.0
                except (ValueError, TypeError):
                    qty = 0.0
                if nombre:
                    cantidad_dict[nombre] = qty

        # Construir elementos para PDF
        elements = []
        total_general = 0.0
        # Agrupar por sección
        for sec in SECTIONS:
            items_filtrados = []
            subtotal_seccion = 0.0
            for name, desc, price in sec["items"]:
                qty = cantidad_dict.get(name, 0.0)
                if short and qty == 0:
                    continue
                subtotal = price * qty
                subtotal_seccion += subtotal
                items_filtrados.append((name, desc, price, qty, subtotal))
            if not items_filtrados:
                continue
            total_general += subtotal_seccion
            elements.append({"type": "section", "name": sec["name"]})
            for name, desc, price, qty, subtotal in items_filtrados:
                elements.append({
                    "type": "item",
                    "data": (name, f"{price:.2f} × {qty:.2f}", subtotal)
                })
            elements.append({"type": "subtotal", "name": f"Subtotal {sec['name']}", "data": ("", "", subtotal_seccion)})
        if elements:
            elements.append({"type": "total", "name": "TOTAL", "data": ("", "", total_general)})

        # Generar PDF
        verbose_headers = ["Artículo", "Precio × Cantidad", "Subtotal"]
        col_widths = [
            int(VIRTUAL_WIDTH * 0.50),
            int(VIRTUAL_WIDTH * 0.25),
            int(VIRTUAL_WIDTH * 0.25),
        ]
        diff = VIRTUAL_WIDTH - sum(col_widths)
        if diff != 0:
            col_widths[-1] += diff

        # Nombre del archivo: basado en la hoja actual o fecha
        selected = sheet_var.get()
        if selected:
            base = selected
        else:
            base = datetime.now().strftime("%Y-%m-%d")
        # Limpiar caracteres no válidos para nombre de archivo
        base = re.sub(r'[^\w\-]', '_', base)
        if short:
            output_pdf = f"{base}.pdf"
        else:
            output_pdf = f"{base}_verbose.pdf"

        generate_pdf_from_elements(elements, verbose_headers, col_widths, output_pdf, VIRTUAL_WIDTH, VIRTUAL_HEIGHT)

    # Orden y nombres según solicitud:
    # Calcular, Calcular detallado, Exportar PDF, Exportar PDF detallado
    ttk.Button(bottom_frame, text="Calcular", command=lambda: calcular_resultados(short=True)).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text="Calcular detallado", command=lambda: calcular_resultados(short=False)).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text="Exportar PDF", command=lambda: export_pdf(short=True)).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text="Exportar PDF detallado", command=lambda: export_pdf(short=False)).pack(side=tk.LEFT, padx=5)

    # Manejar cierre de ventana con advertencia si hay cambios sin guardar
    def on_closing():
        if dirty:
            respuesta = messagebox.askyesnocancel(
                "Cambios sin guardar",
                "Hay cambios sin guardar. ¿Quieres guardarlos antes de salir?\n"
                "Sí = guardar, No = salir sin guardar, Cancelar = volver"
            )
            if respuesta is None:  # Cancelar
                return
            if respuesta:  # Sí
                on_guardar()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    root.mainloop()

# ==================== FUNCIÓN PRINCIPAL ====================

def main():
    parser = argparse.ArgumentParser(description="Gestor de compras con datos desde JSON")
    parser.add_argument("--json", default=DEFAULT_JSON,
                        help=f"Archivo JSON con los datos (default: {DEFAULT_JSON})")
    parser.add_argument("--export-pdf", action="store_true",
                        help="Solo genera el PDF normal (sin editor)")
    args = parser.parse_args()

    # Cargar datos desde el JSON especificado
    global SECTIONS
    SECTIONS = load_sections_from_json(args.json)

    if args.export_pdf:
        generate_pdf()
    else:
        # Por defecto: generar PDF + abrir editor completo
        generate_pdf()
        open_editor_window(SECTIONS, args.json)

if __name__ == "__main__":
    main()
