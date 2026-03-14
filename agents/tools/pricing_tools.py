"""가격/견적 MCP 도구"""

import json

from claude_agent_sdk import create_sdk_mcp_server, tool

# 기본 단가표 (DB에서 관리 예정)
BASE_PRICES = {
    "base_cabinet": {  # 하부장 (폭 mm당)
        "300": 120_000,
        "450": 150_000,
        "600": 180_000,
        "900": 250_000,
    },
    "upper_cabinet": {  # 상부장
        "300": 80_000,
        "450": 100_000,
        "600": 120_000,
        "900": 170_000,
    },
    "tall_cabinet": {  # 장롱/키큰장
        "300": 200_000,
        "450": 280_000,
        "600": 350_000,
        "900": 480_000,
    },
}

DOOR_SURCHARGE = {
    "wrapping": 0,  # 래핑 (기본)
    "high_glossy": 30_000,  # 하이그로시 (개당 추가)
    "paint": 50_000,  # 도장 (개당 추가)
    "solid_wood": 80_000,  # 원목 (개당 추가)
}

COUNTERTOP_PRICES = {  # m² 당
    "artificial_marble": 200_000,
    "natural_stone": 400_000,
    "stainless": 250_000,
    "solid_surface": 300_000,
}

INSTALLATION_BASE = {
    "sink": 200_000,
    "island": 250_000,
    "closet": 150_000,
    "fridge_cabinet": 100_000,
    "shoe_cabinet": 80_000,
    "vanity": 100_000,
    "storage": 120_000,
    "utility_closet": 100_000,
}


@tool(
    "get_modules",
    "카테고리별 사용 가능한 모듈 종류와 규격을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "가구 카테고리"},
        },
        "required": ["category"],
    },
)
async def get_modules(args: dict) -> dict:
    # 카테고리별 모듈 정보 (추후 DB화)
    modules = {
        "sink": {
            "base_modules": ["sink_bowl", "gas_range", "drawer_3", "door_2", "corner"],
            "upper_modules": ["door_2", "dish_rack", "range_hood", "corner"],
            "widths": [300, 450, 600, 900],
            "depth": 580,
            "height": {"base": 850, "upper": 600},
        },
        "closet": {
            "base_modules": ["shelf", "drawer", "hanging_rod", "shoe_rack"],
            "widths": [300, 450, 600, 900],
            "depth": 580,
            "height": {"full": 2400},
            "door_types": ["hinged", "sliding"],
        },
        "fridge_cabinet": {
            "base_modules": ["fridge_space", "upper_storage", "side_panel"],
            "widths": [600, 900],
            "depth": 600,
        },
    }

    category = args["category"]
    data = modules.get(category, {"message": f"{category} 카테고리 모듈 정보 준비중"})
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}


@tool(
    "get_prices",
    "모듈 구성에 대한 가격을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "width_mm": {"type": "integer"},
                        "door_type": {"type": "string"},
                    },
                },
                "description": "모듈 리스트",
            },
            "countertop_material": {"type": "string"},
            "countertop_area_m2": {"type": "number"},
        },
        "required": ["modules"],
    },
)
async def get_prices(args: dict) -> dict:
    total = 0
    items = []

    for module in args["modules"]:
        module_type = module["type"]
        width = str(module.get("width_mm", 600))
        price_map = BASE_PRICES.get(module_type, BASE_PRICES.get("base_cabinet", {}))
        base_price = price_map.get(width, price_map.get("600", 180_000))

        # 도어 추가비
        door_type = module.get("door_type", "wrapping")
        door_extra = DOOR_SURCHARGE.get(door_type, 0)

        module_total = base_price + door_extra
        total += module_total
        items.append(
            {
                "module": f"{module_type} {width}mm",
                "base_price": base_price,
                "door_surcharge": door_extra,
                "total": module_total,
            }
        )

    # 상판
    if args.get("countertop_material") and args.get("countertop_area_m2"):
        ct_price = (
            COUNTERTOP_PRICES.get(args["countertop_material"], 200_000) * args["countertop_area_m2"]
        )
        total += int(ct_price)
        items.append(
            {
                "module": f"상판 ({args['countertop_material']})",
                "total": int(ct_price),
            }
        )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"items": items, "subtotal": total}, ensure_ascii=False),
            }
        ]
    }


@tool(
    "get_installation_cost",
    "카테고리별 설치비를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "include_demolition": {"type": "boolean", "description": "기존 철거 포함 여부"},
        },
        "required": ["category"],
    },
)
async def get_installation_cost(args: dict) -> dict:
    base = INSTALLATION_BASE.get(args["category"], 150_000)
    demolition = 100_000 if args.get("include_demolition") else 0
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "installation_fee": base,
                        "demolition_fee": demolition,
                        "total": base + demolition,
                    }
                ),
            }
        ]
    }


@tool(
    "get_materials",
    "BOM용 자재 정보를 조회합니다. (Pro+)",
    {
        "type": "object",
        "properties": {
            "module_type": {"type": "string"},
            "width_mm": {"type": "integer"},
            "door_type": {"type": "string"},
        },
        "required": ["module_type", "width_mm"],
    },
)
async def get_materials(args: dict) -> dict:
    # 기본 자재 구성 (추후 DB화)
    width = args["width_mm"]
    materials = [
        {"name": "본체 측판 (18T PB)", "spec": "580x850mm", "qty": 2},
        {"name": "본체 상판 (18T PB)", "spec": f"{width}x580mm", "qty": 1},
        {"name": "본체 하판 (18T PB)", "spec": f"{width}x580mm", "qty": 1},
        {"name": "뒷판 (9T MDF)", "spec": f"{width}x850mm", "qty": 1},
        {"name": f"도어 ({args.get('door_type', 'wrapping')})", "spec": f"{width}x720mm", "qty": 1},
        {"name": "경첩", "spec": "35mm 풀커버", "qty": 2},
        {"name": "선반", "spec": f"{width - 36}x550mm", "qty": 1},
    ]
    return {"content": [{"type": "text", "text": json.dumps(materials, ensure_ascii=False)}]}


pricing_server = create_sdk_mcp_server(
    name="pricing",
    version="1.0.0",
    tools=[get_modules, get_prices, get_installation_cost, get_materials],
)
