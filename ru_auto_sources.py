"""
Реестр источников AutoDiag AI — российские авто, грузовики, автобусы.

Категории:
  passenger  — легковые (LADA/ВАЗ, УАЗ, …)
  truck      — грузовые (КАМАЗ, ГАЗ, МАЗ, Урал, …)
  bus        — автобусы (ПАЗ, ЛиАЗ, НефАЗ, …)
  general    — общие OBD / диагностика
  special    — спецтехника (заготовка, расширим позже)

type:
  list   — пагинация {range_start} / {range_end}
  page   — одна страница-каталог
  ddg_site — DuckDuckGo site:домен + query-шаблоны (для форумов)
"""

# ─── Приоритетные DTC для site-поиска (часто на РФ-авто и дизеле) ───
PRIORITY_DTC_SEED = [
    # O2 / fuel
    "P0134", "P0135", "P0171", "P0172", "P0300", "P0301", "P0420",
    # diesel / common rail (грузовики, автобусы)
    "P0087", "P0088", "P0093", "P0190", "P0191", "P0192", "P0193",
    "P0200", "P0201", "P0216", "P0251", "P0263", "P0299",
    "P0335", "P0340", "P0380", "P0400", "P0401", "P0402",
    "P0470", "P0480", "P0500", "P0560", "P0562", "P0606",
    "P0620", "P0622", "P2002", "P2031", "P242F",
]

# ─── Подсказки марок для site-поиска ───
BRAND_SEARCH_HINTS = [
    # легковые
    "LADA", "ВАЗ", "Веста", "Гранта", "Нива", "УАЗ", "Патриот",
    # грузовики
    "КАМАЗ", "KAMAZ", "ГАЗель", "ГАЗон", "NEXT", "МАЗ", "Урал",
    "ЗИЛ", "БелАЗ",
    # автобусы
    "ПАЗ", "ЛиАЗ", "НефАЗ", "КАВЗ", "Волжанин",
    # спецтехника (заготовка)
    "Амкодор", "ЧТЗ", "Кировец", "Т-150",
]

# ─── Маркеры «релевантно для РФ» ───
RUSSIAN_RELEVANCE_HINTS = [
    # легковые
    "лада", "lada", "ваз", "vaz", "калина", "приора", "гранта", "веста",
    "нива", "xray", "largus", "уаз", "uaz", "патриот", "хантер", "буханка",
    "волга", "москвич", "tagaz", "derways",
    # грузовики / коммерция
    "камаз", "kamaz", "газ", "gaz", "газель", "соболь", "газон", "next",
    "валдай", "маз", "maz", "урал", "ural", "зил", "зил-", "белаз", "belaz",
    "камаз-евро", "cummins", "яMZ", "ямз", "камаз 5490", "камаз 65115",
    "isuzu", "fuso",  # часто на шасси РФ-перевозок
    # автобусы
    "паз", "paz", "лиаз", "liaz", "нефаз", "nefaz", "кавз", "волжанин",
    "gorodskoy avtobus", "маршрутка",
    # спецтехника (на будущее)
    "амкодор", "чтз", "кировец", "трактор", "экскаватор", "погрузчик",
    "гбо", "метан", "пропан", "газобаллон",
]

# ─── Источники кодов / статей ───
KNOWN_CODE_SOURCES = [
    # ===== GENERAL OBD =====
    {
        "name": "OBD-Codes.ru",
        "url": "https://obd-codes.ru/powertrain/p{range_start:04d}",
        "type": "list",
        "range_start": 0,
        "range_end": 200,
        "parser": "html_table",
        "category": "general",
    },
    {
        "name": "CarDiagn.com",
        "url": "https://cardiagn.com/obd2-p{range_start:04d}-{range_end:04d}/",
        "type": "list",
        "range_start": 0,
        "range_end": 200,
        "parser": "wordpress",
        "category": "general",
    },
    {
        "name": "ELM327.ru codes",
        "url": "https://elm327.ru/oshibki-obd2/",
        "type": "page",
        "parser": "generic",
        "category": "general",
    },
    {
        "name": "OBD2-codes list RU",
        "url": "https://www.obd-codes.com/trouble_codes/",
        "type": "page",
        "parser": "generic",
        "category": "general",
    },

    # ===== PASSENGER (LADA / UAZ / …) =====
    {
        "name": "Drive2.ru",
        "type": "ddg_site",
        "site": "drive2.ru",
        "queries": [
            "ошибка {code}",
            "код {code} ремонт",
            "{brand} {code}",
        ],
        "category": "passenger",
    },
    {
        "name": "Drom.ru club",
        "type": "ddg_site",
        "site": "drom.ru",
        "queries": [
            "ошибка {code}",
            "код {code} {brand}",
        ],
        "category": "passenger",
    },
    {
        "name": "За рулём (zr.ru)",
        "type": "ddg_site",
        "site": "zr.ru",
        "queries": [
            "ошибка {code}",
            "DTC {code}",
            "LADA {code}",
        ],
        "category": "passenger",
    },
    {
        "name": "LadaOnline",
        "type": "ddg_site",
        "site": "ladaonline.ru",
        "queries": ["{code}", "ошибка {code}"],
        "category": "passenger",
    },
    {
        "name": "ChipMaker Lada",
        "type": "ddg_site",
        "site": "chipmaker.ru",
        "queries": ["{code} ВАЗ", "{code} LADA", "ошибка {code}"],
        "category": "passenger",
    },
    {
        "name": "Forum Lada",
        "type": "ddg_site",
        "site": "forum.lada-forum.ru",
        "queries": ["{code}", "ошибка {code}"],
        "category": "passenger",
    },
    {
        "name": "UAZBUKA",
        "type": "ddg_site",
        "site": "uazbuka.ru",
        "queries": ["{code}", "ошибка {code} УАЗ"],
        "category": "passenger",
    },
    {
        "name": "Auto.ru magazine",
        "type": "ddg_site",
        "site": "auto.ru",
        "queries": ["ошибка {code} LADA", "код {code} причины"],
        "category": "passenger",
    },
    {
        "name": "CarFrance / Lada",
        "type": "ddg_site",
        "site": "carfrance.ru",
        "queries": ["{code}", "ошибка {code}"],
        "category": "passenger",
    },

    # ===== TRUCKS (КАМАЗ, ГАЗ, МАЗ, Урал) =====
    {
        "name": "KAMAZ official",
        "type": "ddg_site",
        "site": "kamaz.ru",
        "queries": [
            "ошибка {code}",
            "код неисправности {code}",
            "диагностика {code}",
        ],
        "category": "truck",
    },
    {
        "name": "GAZ Group",
        "type": "ddg_site",
        "site": "gazgroup.ru",
        "queries": ["ошибка {code}", "ГАзель {code}", "NEXT {code}"],
        "category": "truck",
    },
    {
        "name": "МАЗ (maz.by)",
        "type": "ddg_site",
        "site": "maz.by",
        "queries": ["ошибка {code}", "код {code}"],
        "category": "truck",
    },
    {
        "name": "Урал (uralaz.ru)",
        "type": "ddg_site",
        "site": "uralaz.ru",
        "queries": ["ошибка {code}", "диагностика {code}"],
        "category": "truck",
    },
    {
        "name": "Грузовик Пресс",
        "type": "ddg_site",
        "site": "gruzovikpress.ru",
        "queries": ["ошибка {code} КАМАЗ", "код {code} дизель"],
        "category": "truck",
    },
    {
        "name": "5koleso trucks",
        "type": "ddg_site",
        "site": "5koleso.ru",
        "queries": ["ошибка {code} грузовик", "КАМАЗ {code}"],
        "category": "truck",
    },
    {
        "name": "Trucker.su",
        "type": "ddg_site",
        "site": "trucker.su",
        "queries": ["ошибка {code}", "КАМАЗ {code}", "МАЗ {code}"],
        "category": "truck",
    },
    {
        "name": "Avtosovet trucks",
        "type": "ddg_site",
        "site": "avtosovet.ru",
        "queries": ["код ошибки {code}", "грузовой {code}"],
        "category": "truck",
    },
    {
        "name": "Drive2 trucks",
        "type": "ddg_site",
        "site": "drive2.ru",
        "queries": [
            "КАМАЗ ошибка {code}",
            "ГАЗель ошибка {code}",
            "МАЗ ошибка {code}",
            "дизель {code} common rail",
        ],
        "category": "truck",
    },
    {
        "name": "ChipMaker trucks",
        "type": "ddg_site",
        "site": "chipmaker.ru",
        "queries": [
            "КАМАЗ {code}",
            "ЯМЗ {code}",
            "Cummins {code} КАМАЗ",
            "EDC7 {code}",
        ],
        "category": "truck",
    },
    {
        "name": "ECU-гараж / прошивка",
        "type": "ddg_site",
        "site": "ecutuning.ru",
        "queries": ["{code} КАМАЗ", "{code} дизель"],
        "category": "truck",
    },
    {
        "name": "Forum Truck",
        "type": "ddg_site",
        "site": "forum.truck-forum.ru",
        "queries": ["ошибка {code}", "{code}"],
        "category": "truck",
    },

    # ===== BUSES (ПАЗ, ЛиАЗ, НефАЗ) =====
    {
        "name": "PAZ official",
        "type": "ddg_site",
        "site": "paz.ru",
        "queries": ["ошибка {code}", "диагностика {code}"],
        "category": "bus",
    },
    {
        "name": "LiAZ",
        "type": "ddg_site",
        "site": "liaz.ru",
        "queries": ["ошибка {code}", "код {code}"],
        "category": "bus",
    },
    {
        "name": "NefAZ",
        "type": "ddg_site",
        "site": "nefaz.ru",
        "queries": ["ошибка {code}", "диагностика"],
        "category": "bus",
    },
    {
        "name": "BusForum.ru",
        "type": "ddg_site",
        "site": "busforum.ru",
        "queries": ["ошибка {code}", "ПАЗ {code}", "ЛиАЗ {code}"],
        "category": "bus",
    },
    {
        "name": "BusFan",
        "type": "ddg_site",
        "site": "busfan.ru",
        "queries": ["ошибка {code} автобус", "ПАЗ {code}"],
        "category": "bus",
    },
    {
        "name": "Drive2 buses",
        "type": "ddg_site",
        "site": "drive2.ru",
        "queries": [
            "ПАЗ ошибка {code}",
            "ЛиАЗ ошибка {code}",
            "НефАЗ ошибка {code}",
            "автобус код {code}",
        ],
        "category": "bus",
    },

    # ===== SPECIAL (заготовка — расширим позже) =====
    {
        "name": "SpecTech portals",
        "type": "ddg_site",
        "site": "stroyteh.ru",
        "queries": ["ошибка {code}", "диагностика {code}"],
        "category": "special",
    },
    {
        "name": "Amkodor / special (DDG)",
        "type": "ddg_site",
        "site": "drive2.ru",
        "queries": [
            "спецтехника ошибка {code}",
            "трактор код {code}",
            "погрузчик {code}",
        ],
        "category": "special",
    },
]

# ─── Запросы схем ───
SCHEMA_SEARCH_QUERIES = [
    # легковые
    "схема датчика {component} LADA ВАЗ",
    "расположение {component} двигатель ВАЗ схема",
    "{component} где находится LADA схема",
    # грузовики / дизель
    "схема {component} КАМАЗ дизель",
    "расположение {component} ЯМЗ common rail",
    "{component} ГАЗель NEXT схема",
    "схема {component} МАЗ двигатель",
    # автобусы
    "схема {component} ПАЗ",
    "расположение {component} ЛиАЗ автобус",
    # общее
    "OBD2 {component} схема расположения",
]

# ─── Запросы ремонта ───
REPAIR_SEARCH_QUERIES = [
    "ошибка {code} причины и ремонт",
    "код {code} как исправить",
    "DTC {code} LADA ВАЗ",
    "ошибка {code} КАМАЗ",
    "код {code} ГАЗель NEXT",
    "ошибка {code} МАЗ ЯМЗ",
    "код {code} ПАЗ автобус",
    "DTC {code} common rail дизель",
    "ошибка {code} ЛиАЗ",
    "P-код {code} российский грузовик",
]

# ─── Компоненты (легковые + дизель грузовик/автобус) ───
COMPONENTS = [
    # бензин / общие
    "датчик кислорода", "лямбда-зонд", "датчик коленвала", "датчик распредвала",
    "датчик детонации", "ДМРВ", "ДАтчик абсолютного давления",
    "клапан EGR", "катализатор", "топливный насос",
    "форсунка", "катушка зажигания", "дроссельная заслонка",
    "датчик температуры ОЖ", "датчик давления масла",
    "адсорбер", "клапан продувки адсорбера", "датчик скорости",
    # дизель / common rail (грузовики, автобусы)
    "ТНВД", "топливная рампа", "датчик давления в рампе",
    "форсунка Common Rail", "турбина", "геометрия турбины",
    "клапан управления турбиной", "интеркулер",
    "сажевый фильтр DPF", "датчик дифференциального давления DPF",
    "дозатор мочевины AdBlue", "SCR катализатор",
    "свеча накаливания", "реле свечей накаливания",
    "датчик давления наддува", "клапан сброса давления",
    "модуль EDC", "блок ЯМЗ", "ЭБУ КАМАЗ",
    "компрессор пневмосистемы", "датчик давления воздуха",
    "ABS модулятор", "датчик ABS колеса",
]
