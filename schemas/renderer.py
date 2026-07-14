"""
AutoDiag AI v1.0 — SVG-рендерер 2D-схем узлов
Генерирует векторные изображения из данных nodes/links/checkpoints.
Без внешних зависимостей — чистый SVG.
"""

from typing import Optional

# ── Цвета по категориям ──────────────────────────────────────────
CATEGORY_COLORS: dict[str, tuple[str, str, str]] = {
    "fuel":      ("#F57C00", "#FFF3E0", "Топливная система"),
    "ignition":  ("#FBC02D", "#FFFDE7", "Зажигание"),
    "sensor":    ("#1E88E5", "#E3F2FD", "Датчики / электроника"),
    "intake":    ("#43A047", "#E8F5E9", "Впуск"),
    "exhaust":   ("#E53935", "#FFEBEE", "Выпуск"),
    "cooling":   ("#00ACC1", "#E0F7FA", "Охлаждение"),
    "evap":      ("#8E24AA", "#F3E5F5", "EVAP"),
    "ecu":       ("#546E7A", "#ECEFF1", "ЭБУ / питание"),
    "default":   ("#78909C", "#ECEFF1", "Прочее"),
}

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "fuel":     ["топлив", "форсунк", "бенз", "насос", "фильтр",
                 "рамп", "регулятор давлен", "тнвд", "бак"],
    "ignition": ["зажиган", "свеч", "катушк", "детонац", "искр", "цилиндр"],
    "sensor":   ["датчик", "лямбда", "o₂", "сенсор", "maf", "ect",
                 "разъём", "проводк", "прибор"],
    "intake":   ["впуск", "дроссель", "воздуш", "вакуум", "egr",
                 "рециркул", "трубка egr", "клапан egr"],
    "exhaust":  ["выпуск", "катализ", "глушитель", "коллектор",
                 "lambda", "прокладка"],
    "cooling":  ["охлажд", "радиатор", "термостат", "помп", "ож",
                 "антифриз", "вентилятор", "водян"],
    "evap":     ["evap", "адсорб", "продувк", "уголь", "крышка"],
    "ecu":      ["эбу", "ecu", "акб", "предохранитель", "реле",
                 "масс", "питани"],
}


def _classify(label: str) -> str:
    """Определить категорию компонента по названию."""
    ll = label.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw in ll:
                return cat
    return "default"


# ── Рендеринг SVG ────────────────────────────────────────────────

def render_schema_svg(error_code: str, schema_data: dict) -> str:
    """
    Сгенерировать SVG-схему из данных schema_data.

    Возвращает строку с полноценным SVG-изображением: граф узлов слева,
    чек-лист справа, легенда внизу.
    """
    nodes = schema_data.get("nodes", [])
    checkpoints = schema_data.get("checkpoints", [])
    title = schema_data.get("title", f"Схема {error_code}")
    desc = schema_data.get("description", "")

    if not nodes:
        return _empty_svg(error_code, title, desc)

    node_cats = {n["id"]: _classify(n["label"]) for n in nodes}

    # ── границы графа ────────────────────────────────────────────
    min_x = min(n["x"] for n in nodes)
    max_x = max(n["x"] + _node_width(n["label"]) for n in nodes)
    min_y = min(n["y"] for n in nodes)
    max_y = max(n["y"] + 32 for n in nodes)

    graph_w = int(max_x - min_x + 40)
    graph_h = int(max_y - min_y + 60)
    pad = 30

    # Координаты внутри SVG
    def sx(x: float) -> float:
        return x - min_x + pad
    def sy(y: float) -> float:
        return y - min_y + 55  # 55 = отступ сверху под заголовок

    total_w = graph_w + 310
    total_h = max(graph_h + 80, len(checkpoints) * 30 + 130)

    # ── Сборка SVG ───────────────────────────────────────────────
    p: list[str] = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'viewBox="0 0 {total_w} {total_h}" '
             f'width="{total_w}" height="{total_h}">')
    p.append('<defs>')
    p.append('<marker id="arrow" markerWidth="8" markerHeight="6" '
             'refX="8" refY="3" orient="auto">'
             '<polygon points="0 0, 8 3, 0 6" fill="#90A4AE"/></marker>')
    p.append('<filter id="sh"><feDropShadow dx="1" dy="1" stdDeviation="2" '
             'flood-opacity="0.12"/></filter>')
    p.append('</defs>')

    # Фон
    p.append(f'<rect width="{total_w}" height="{total_h}" '
             f'fill="#F5F7FA" rx="10"/>')

    # Заголовок
    p.append(f'<text x="{pad}" y="28" font-family="Arial,sans-serif" '
             f'font-size="18" font-weight="bold" fill="#263238">{title}</text>')
    p.append(f'<text x="{pad}" y="47" font-family="Arial,sans-serif" '
             f'font-size="11" fill="#78909C">Код: {error_code}  |  {desc}</text>')

    # Разделитель
    sep_x = graph_w + 15
    p.append(f'<line x1="{sep_x}" y1="60" x2="{sep_x}" y2="{total_h - 30}" '
             f'stroke="#CFD8DC" stroke-width="1" stroke-dasharray="5,4"/>')

    # ── Стрелки-связи ────────────────────────────────────────────
    for n in nodes:
        for tid in n.get("links", []):
            tgt = next((t for t in nodes if t["id"] == tid), None)
            if not tgt:
                continue
            x1 = sx(n["x"]) + _node_width(n["label"])
            y1 = sy(n["y"]) + 16
            x2 = sx(tgt["x"])
            y2 = sy(tgt["y"]) + 16
            mx = (x1 + x2) / 2
            p.append(f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" '
                     f'stroke="#90A4AE" stroke-width="1.5" fill="none" '
                     f'marker-end="url(#arrow)"/>')

    # ── Узлы ─────────────────────────────────────────────────────
    for n in nodes:
        cat = node_cats.get(n["id"], "default")
        stroke, fill, _ = CATEGORY_COLORS[cat]
        x, y = sx(n["x"]), sy(n["y"])
        w = _node_width(n["label"])
        h = 32
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="2" '
                 f'filter="url(#sh)"/>')
        p.append(f'<text x="{x + w/2}" y="{y + 21}" '
                 f'font-family="Arial,sans-serif" font-size="11" '
                 f'fill="#37474F" text-anchor="middle" '
                 f'font-weight="500">{n["label"]}</text>')

    # ── Чек-лист ─────────────────────────────────────────────────
    if checkpoints:
        cx = graph_w + 35
        p.append(f'<text x="{cx}" y="75" font-family="Arial,sans-serif" '
                 f'font-size="13" font-weight="bold" fill="#37474F">'
                 f'🔍 Чек-лист проверок</text>')
        cp_colors = ["#E53935", "#FB8C00", "#FDD835", "#43A047",
                     "#00ACC1", "#1E88E5", "#8E24AA", "#D81B60"]
        for i, cp in enumerate(checkpoints):
            cy = 105 + i * 29
            clr = cp_colors[i % len(cp_colors)]
            p.append(f'<circle cx="{cx + 11}" cy="{cy - 4}" r="10" fill="{clr}"/>')
            p.append(f'<text x="{cx + 11}" y="{cy}" '
                     f'font-family="Arial,sans-serif" font-size="10" '
                     f'fill="white" text-anchor="middle" '
                     f'font-weight="bold">{i + 1}</text>')
            text = cp if len(cp) < 48 else cp[:45] + "..."
            p.append(f'<text x="{cx + 27}" y="{cy}" '
                     f'font-family="Arial,sans-serif" font-size="11" '
                     f'fill="#455A64">{text}</text>')

    # ── Легенда ──────────────────────────────────────────────────
    used = sorted(set(node_cats.values()),
                  key=lambda c: list(CATEGORY_COLORS.keys()).index(c)
                                if c in CATEGORY_COLORS else 99)
    lx = pad
    ly = total_h - 22
    p.append(f'<text x="{lx}" y="{ly}" font-family="Arial,sans-serif" '
             f'font-size="9" fill="#B0BEC5">Легенда:</text>')
    lx += 55
    for cat in used:
        stroke, fill, name = CATEGORY_COLORS[cat]
        p.append(f'<rect x="{lx}" y="{ly - 8}" width="10" height="10" '
                 f'rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1"/>')
        lx += 13
        p.append(f'<text x="{lx}" y="{ly}" font-family="Arial,sans-serif" '
                 f'font-size="9" fill="#78909C">{name}</text>')
        lx += len(name) * 6 + 18

    p.append('</svg>')
    return "\n".join(p)


def _node_width(label: str) -> int:
    """Примерная ширина узла по длине текста."""
    return max(90, min(len(label) * 8 + 24, 210))


def _empty_svg(code: str, title: str, desc: str) -> str:
    """Заглушка когда нет данных для рендеринга."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 150" width="400" height="150">'
        '<rect width="400" height="150" fill="#F5F7FA" rx="10"/>'
        f'<text x="200" y="55" font-family="Arial,sans-serif" font-size="16" '
        f'font-weight="bold" fill="#37474F" text-anchor="middle">{title}</text>'
        f'<text x="200" y="80" font-family="Arial,sans-serif" font-size="12" '
        f'fill="#78909C" text-anchor="middle">Код: {code}</text>'
        f'<text x="200" y="105" font-family="Arial,sans-serif" font-size="11" '
        f'fill="#B0BEC5" text-anchor="middle">Нет данных для отображения схемы</text>'
        '</svg>'
    )


# ── Экспорт в PNG ────────────────────────────────────────────────
# (удалён: требовался только для отправки в Telegram)
