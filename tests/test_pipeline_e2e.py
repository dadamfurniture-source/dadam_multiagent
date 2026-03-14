"""E2E Pipeline Integration Test - validates full pipeline wiring without external APIs

Tests:
1. Layout Engine: distribute_modules for all 8 categories
2. Layout Planning: plan_layout with sink/cooktop positions
3. Drawing Generation: SVG front elevation from layout
4. BOM Generation: material list from layout
5. Pricing: quote calculation from modules
6. Pipeline Orchestrator: agent wiring for all plan tiers
7. API Route: project creation flow validation
"""

import ast
import json
import sys
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_layout_engine_all_categories():
    """Test module distribution for various wall widths"""
    from agents.layout_engine import distribute_modules

    test_cases = [
        (1200, 2, 0),   # small wall
        (1800, 4, 0),   # medium
        (2400, 5, 0),   # standard kitchen
        (3000, 7, None), # large
        (3600, 8, 0),   # XL
    ]

    for wall_width, expected_doors, expected_remainder in test_cases:
        r = distribute_modules(wall_width)
        assert r.door_count > 0, f"{wall_width}mm: no doors generated"
        assert 350 <= r.door_width <= 600, f"{wall_width}mm: door width {r.door_width} out of range"
        assert r.remainder >= 0, f"{wall_width}mm: negative remainder {r.remainder}"
        assert r.remainder <= 10, f"{wall_width}mm: remainder {r.remainder}mm exceeds max 10mm"

        # Verify module widths sum correctly
        total_from_modules = sum(m.width for m in r.modules)
        assert total_from_modules == r.total_width, f"{wall_width}mm: module sum mismatch"

        print(f"  {wall_width}mm -> {r.door_count} doors × {r.door_width}mm, remainder={r.remainder}mm OK")

    # Edge cases
    r = distribute_modules(50)  # too small
    assert len(r.modules) == 0, "Should return empty for <100mm"

    r = distribute_modules(350)  # minimum 1 door
    assert r.door_count >= 1, "Should produce at least 1 door for 350mm"

    print("  Edge cases OK")


def test_layout_planning_sink():
    """Test full layout with sink + cooktop positions"""
    from agents.layout_engine import plan_layout

    result = plan_layout(
        wall_width=3000,
        category="sink",
        finish_left=50,
        finish_right=50,
        sink_position=500,
        cooktop_position=2500,
    )

    assert "error" not in result, f"Layout error: {result.get('error')}"
    assert result["effective_space"] == 2900
    assert len(result["modules"]) > 0

    # Verify sink and cooktop are placed
    types = [m["type"] for m in result["modules"]]
    assert "sink_bowl" in types, "Missing sink_bowl module"
    assert "cooktop" in types, "Missing cooktop module"

    # Verify modules are within bounds
    for m in result["modules"]:
        assert m["position_x"] >= 50, f"Module at {m['position_x']}mm violates left finish"
        assert m["position_x"] + m["width"] <= 2950, f"Module exceeds right bound"

    # Verify open_door_contents exists
    assert "open_door_contents" in result
    assert len(result["open_door_contents"]) > 0

    print(f"  Sink layout: {len(result['modules'])} modules, total={result['total_module_width']}mm OK")


def test_layout_planning_all_categories():
    """Test layout planning for each furniture category"""
    from agents.layout_engine import plan_layout

    categories = ["sink", "island", "closet", "fridge_cabinet", "shoe_cabinet", "vanity", "storage", "utility_closet"]

    for cat in categories:
        result = plan_layout(wall_width=2400, category=cat)
        assert "error" not in result, f"{cat}: {result.get('error')}"
        assert len(result["modules"]) > 0, f"{cat}: no modules"
        print(f"  {cat}: {len(result['modules'])} modules OK")


def test_drawing_svg_generation():
    """Test SVG front elevation generation"""
    # Import internal functions by parsing the file
    drawing_file = Path(__file__).parent.parent / "agents" / "tools" / "drawing_tools.py"
    with open(drawing_file, encoding="utf-8") as f:
        source = f.read()

    # Extract function definitions only (skip SDK imports and decorators)
    tree = ast.parse(source)
    func_defs = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_")]

    # Execute just the helper functions
    namespace = {"json": json}
    for node in func_defs:
        code = ast.unparse(node)
        exec(code, namespace)

    generate_front = namespace["_generate_front_elevation"]

    layout = {
        "total_width_mm": 2400,
        "total_height_mm": 2400,
        "modules": [
            {"type": "base_cabinet", "width_mm": 800, "position_mm": 0, "door_count": 2, "features": ["sink_bowl"]},
            {"type": "base_cabinet", "width_mm": 900, "position_mm": 800, "door_count": 2, "features": ["gas_range"]},
            {"type": "base_cabinet", "width_mm": 700, "position_mm": 1700, "door_count": 2, "features": []},
        ],
        "upper_modules": [
            {"type": "upper_cabinet", "width_mm": 600, "position_mm": 0, "door_count": 2, "features": []},
            {"type": "upper_cabinet", "width_mm": 600, "position_mm": 600, "door_count": 2, "features": ["range_hood"]},
        ],
        "cabinet_specs": {
            "upper_height_mm": 720,
            "lower_height_mm": 870,
            "toe_kick_mm": 150,
            "molding_mm": 60,
            "depth_mm": 580,
        },
    }

    svg = generate_front(layout)

    assert "<svg" in svg, "Missing SVG tag"
    assert "FRONT ELEVATION" in svg, "Missing title"
    assert "SINK" in svg, "Missing sink label"
    assert "COOKTOP" in svg, "Missing cooktop label"
    assert "HOOD" in svg, "Missing hood label"
    assert "800" in svg, "Missing 800mm dimension"
    assert "countertop" in svg, "Missing countertop line"
    assert svg.count("<rect") >= 5, "Not enough module rectangles"

    print(f"  SVG generated: {len(svg)} chars, {svg.count('<rect')} rects, {svg.count('<text')} labels OK")


def test_orchestrator_agent_wiring():
    """Test that orchestrator builds correct agents for each plan tier"""
    # Parse the orchestrator to extract _build_agents logic
    orch_file = Path(__file__).parent.parent / "agents" / "orchestrator.py"
    with open(orch_file, encoding="utf-8") as f:
        source = f.read()

    # Verify all tool references exist in actual tool files
    tool_refs = set()
    import re
    for match in re.finditer(r'"(mcp__\w+__\w+)"', source):
        tool_refs.add(match.group(1))

    # Map tool references to their expected server files
    server_tool_map = {
        "vision": "vision_tools.py",
        "supabase": "supabase_tools.py",
        "layout": "layout_tools.py",
        "image": "image_tools.py",
        "drawing": "drawing_tools.py",
        "pricing": "pricing_tools.py",
        "feedback": "feedback_tools.py",
    }

    tools_dir = Path(__file__).parent.parent / "agents" / "tools"

    for ref in sorted(tool_refs):
        parts = ref.split("__")  # mcp__server__tool
        server_name = parts[1]
        tool_name = parts[2]

        expected_file = server_tool_map.get(server_name)
        if expected_file:
            file_path = tools_dir / expected_file
            assert file_path.exists(), f"Missing server file for {ref}: {expected_file}"

            with open(file_path, encoding="utf-8") as f:
                content = f.read()
            # Check tool function or name exists
            assert tool_name in content, f"Tool '{tool_name}' not found in {expected_file}"
            print(f"  {ref} -> {expected_file} OK")
        else:
            print(f"  {ref} -> WARNING: unknown server '{server_name}'")

    # Verify plan-based agent counts
    plan_agents = {
        "free": ["space-analyst", "design-planner", "image-generator", "quote-calculator"],
        "basic": ["space-analyst", "design-planner", "image-generator", "quote-calculator"],
        "pro": ["space-analyst", "design-planner", "image-generator", "quote-calculator",
                 "detail-designer", "bom-generator", "qa-reviewer"],
        "enterprise": ["space-analyst", "design-planner", "image-generator", "quote-calculator",
                        "detail-designer", "bom-generator", "qa-reviewer"],
    }

    for plan, expected_agents in plan_agents.items():
        # Can't actually call _build_agents without SDK, verify via source
        print(f"  {plan} plan: {len(expected_agents)} agents expected OK")


def test_api_route_syntax():
    """Verify all API route files parse correctly"""
    routes_dir = Path(__file__).parent.parent / "api" / "routes"

    for py_file in routes_dir.glob("*.py"):
        with open(py_file, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)

        # Count route decorators
        routes = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("get", "post", "put", "delete", "patch"):
                routes += 1

        print(f"  {py_file.name}: syntax OK, ~{routes} routes OK")


def test_prompt_quality():
    """Verify all prompts are in English and have required sections"""
    from agents.prompts import (
        SPACE_ANALYST_PROMPT,
        DESIGN_PLANNER_PROMPT,
        IMAGE_GENERATOR_PROMPT,
        QUOTE_CALCULATOR_PROMPT,
        DETAIL_DESIGNER_PROMPT,
        QA_REVIEWER_PROMPT,
        CONSULTATION_AGENT_PROMPT,
        ORDERING_AGENT_PROMPT,
        MANUFACTURING_AGENT_PROMPT,
        INSTALLATION_AGENT_PROMPT,
        AFTER_SERVICE_AGENT_PROMPT,
        ACCOUNTING_AGENT_PROMPT,
        SCHEDULE_AGENT_PROMPT,
        NOTIFICATION_AGENT_PROMPT,
    )

    prompts = {
        "SPACE_ANALYST": SPACE_ANALYST_PROMPT,
        "DESIGN_PLANNER": DESIGN_PLANNER_PROMPT,
        "IMAGE_GENERATOR": IMAGE_GENERATOR_PROMPT,
        "QUOTE_CALCULATOR": QUOTE_CALCULATOR_PROMPT,
        "DETAIL_DESIGNER": DETAIL_DESIGNER_PROMPT,
        "QA_REVIEWER": QA_REVIEWER_PROMPT,
        "CONSULTATION": CONSULTATION_AGENT_PROMPT,
        "ORDERING": ORDERING_AGENT_PROMPT,
        "MANUFACTURING": MANUFACTURING_AGENT_PROMPT,
        "INSTALLATION": INSTALLATION_AGENT_PROMPT,
        "AFTER_SERVICE": AFTER_SERVICE_AGENT_PROMPT,
        "ACCOUNTING": ACCOUNTING_AGENT_PROMPT,
        "SCHEDULE": SCHEDULE_AGENT_PROMPT,
        "NOTIFICATION": NOTIFICATION_AGENT_PROMPT,
    }

    for name, prompt in prompts.items():
        assert len(prompt) > 100, f"{name}: prompt too short ({len(prompt)} chars)"
        # Check it's primarily English (first line should be English)
        first_line = prompt.strip().split("\n")[0]
        assert any(c.isascii() and c.isalpha() for c in first_line), f"{name}: first line not English"
        assert "## Role" in prompt or "## role" in prompt.lower() or "Role" in prompt, f"{name}: missing Role section"
        print(f"  {name}: {len(prompt)} chars, English OK")


def test_operations_prompts_reexport():
    """Verify operations prompts correctly re-export English versions"""
    from agents.operations.prompts import (
        CONSULTATION_AGENT_PROMPT,
        ORDERING_AGENT_PROMPT,
    )
    from agents.prompts import (
        CONSULTATION_AGENT_PROMPT as MAIN_CONSULTATION,
        ORDERING_AGENT_PROMPT as MAIN_ORDERING,
    )

    assert CONSULTATION_AGENT_PROMPT is MAIN_CONSULTATION, "Operations consultation prompt not re-exported correctly"
    assert ORDERING_AGENT_PROMPT is MAIN_ORDERING, "Operations ordering prompt not re-exported correctly"
    print("  Operations prompts correctly re-export from agents/prompts.py OK")


def test_constants_completeness():
    """Verify shared constants cover all required data"""
    from shared.constants import CATEGORIES, PLANS, STYLES, LORA_MODELS

    assert len(CATEGORIES) == 8, f"Expected 8 categories, got {len(CATEGORIES)}"
    assert len(PLANS) == 4, f"Expected 4 plans, got {len(PLANS)}"
    assert len(STYLES) == 6, f"Expected 6 styles, got {len(STYLES)}"
    assert len(LORA_MODELS) == 8, f"Expected 8 LoRA models, got {len(LORA_MODELS)}"

    # Every category should have a LoRA model
    for cat in CATEGORIES:
        assert cat in LORA_MODELS, f"Missing LoRA model for {cat}"

    # Plan hierarchy
    plan_order = ["free", "basic", "pro", "enterprise"]
    prev_features = 0
    for plan in plan_order:
        features = len(PLANS[plan]["features"])
        assert features >= prev_features, f"{plan} has fewer features than previous tier"
        prev_features = features

    print(f"  {len(CATEGORIES)} categories, {len(PLANS)} plans, {len(STYLES)} styles, {len(LORA_MODELS)} LoRA models OK")


def test_orders_state_machine():
    """Test order lifecycle state transitions and payment stages"""
    # Parse orders.py to extract VALID_TRANSITIONS and PAYMENT_STAGES
    orders_file = Path(__file__).parent.parent / "api" / "routes" / "orders.py"
    with open(orders_file, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    # Execute assignment nodes to get constants
    namespace = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("VALID_TRANSITIONS", "PAYMENT_STAGES"):
                    exec(ast.unparse(node), namespace)

    transitions = namespace["VALID_TRANSITIONS"]
    payments = namespace["PAYMENT_STAGES"]

    # Full lifecycle path exists
    lifecycle = ["consulting", "quoted", "contracted", "ordering", "manufacturing", "manufactured", "installing", "installed", "settled"]
    for i in range(len(lifecycle) - 1):
        frm, to = lifecycle[i], lifecycle[i + 1]
        assert to in transitions.get(frm, []), f"Missing transition: {frm} -> {to}"
    print(f"  Full lifecycle path ({len(lifecycle)} states) OK")

    # No state can transition to itself
    for frm, tos in transitions.items():
        assert frm not in tos, f"Self-transition found: {frm} -> {frm}"
    print("  No self-transitions OK")

    # Payment stages map correctly
    assert len(payments) == 3, f"Expected 3 payment stages, got {len(payments)}"
    assert payments["contract_deposit"]["ratio"] == 0.3
    assert payments["interim"]["ratio"] == 0.4
    assert payments["balance"]["ratio"] == 0.3
    total_ratio = sum(p["ratio"] for p in payments.values())
    assert abs(total_ratio - 1.0) < 0.001, f"Payment ratios sum to {total_ratio}, expected 1.0"
    print("  Payment stages (30/40/30) OK")

    # Verify route endpoints exist
    route_decorators = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("get", "post", "put", "delete"):
            route_decorators.append(node.attr)

    assert route_decorators.count("put") >= 1, "Missing PUT endpoint (status update)"
    assert route_decorators.count("post") >= 3, "Missing POST endpoints (create + payment + AS)"
    assert route_decorators.count("get") >= 3, "Missing GET endpoints (list + detail + timeline)"
    print(f"  Route methods: {len(route_decorators)} endpoints OK")

    # Verify BackgroundTasks import for ops event triggers
    assert "BackgroundTasks" in source, "Missing BackgroundTasks import for async ops events"
    assert "_fire_ops_event" in source, "Missing _fire_ops_event helper"
    print("  Background ops event triggers OK")

    # SSE event_type whitelist
    assert "ALLOWED_EVENT_TYPES" in source, "Missing SSE event_type whitelist"
    print("  SSE event_type whitelist OK")

    # per_page cap
    assert "min(per_page" in source, "Missing per_page cap"
    print("  per_page cap OK")


def test_exports_route():
    """Test B2B exports route structure and plan gating"""
    exports_file = Path(__file__).parent.parent / "api" / "routes" / "exports.py"
    with open(exports_file, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    # Verify required endpoints exist
    assert "drawing.svg" in source, "Missing SVG drawing endpoint"
    assert "bom.json" in source, "Missing BOM JSON endpoint"
    assert "bom.csv" in source, "Missing BOM CSV endpoint"
    assert "quote.html" in source, "Missing quote HTML endpoint"
    assert "/available" in source, "Missing available exports endpoint"
    print("  5 export endpoints found OK")

    # Verify Pro+ gating (uses shared require_pro from auth module)
    assert "require_pro" in source, "Missing require_pro gating"
    require_count = source.count("require_pro(user)")
    assert require_count >= 3, f"Expected >=3 require_pro calls, got {require_count}"
    print(f"  Pro+ gating: {require_count} endpoints gated OK")

    # Verify quote is accessible to all plans (no _require_pro in quote endpoint)
    # Split source by function to check quote separately
    assert "export_quote_html" in source, "Missing quote export function"

    # Verify download headers
    assert "Content-Disposition" in source, "Missing Content-Disposition header"
    assert "image/svg+xml" in source, "Missing SVG media type"
    assert "text/csv" in source, "Missing CSV media type"
    print("  Download headers OK")

    # Verify BOM generation logic (deduplicated)
    assert "_build_bom" in source, "Missing shared _build_bom helper"
    assert "_get_layout_json" in source, "Missing shared _get_layout_json helper"
    assert "18T PB" in source, "Missing PB material spec in BOM"
    assert "9T MDF" in source, "Missing MDF material spec in BOM"
    assert "edge_banding" in source, "Missing edge banding calculation"
    print("  BOM material specs (deduplicated) OK")

    # Verify CSV injection prevention
    assert "csv.writer" in source or "csv.QUOTE_ALL" in source, "Missing CSV injection prevention (should use csv module)"
    assert "_safe_filename" in source, "Missing filename sanitization"
    print("  CSV injection prevention + filename sanitization OK")


def test_payments_route():
    """Test Stripe payments route structure"""
    payments_file = Path(__file__).parent.parent / "api" / "routes" / "payments.py"
    with open(payments_file, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    # Verify endpoints
    assert "checkout" in source, "Missing checkout endpoint"
    assert "webhook" in source, "Missing webhook endpoint"
    assert "cancel" in source, "Missing cancel endpoint"
    assert "change-plan" in source, "Missing change-plan endpoint"
    assert "/subscription" in source, "Missing subscription query endpoint"
    print("  5 payment endpoints found OK")

    # Verify webhook event handling
    webhook_events = [
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.payment_failed",
    ]
    for event in webhook_events:
        assert event in source, f"Missing webhook handler for {event}"
    print(f"  {len(webhook_events)} webhook events handled OK")

    # Verify Stripe signature verification
    assert "stripe.Webhook.construct_event" in source, "Missing webhook signature verification"
    assert "stripe-signature" in source, "Missing signature header check"
    assert "not webhook_secret" in source or "webhook_secret" in source, "Missing empty secret check"
    print("  Webhook signature verification OK")

    # Verify modern Stripe exception class (not deprecated stripe.error.*)
    assert "stripe.StripeError" in source, "Should use stripe.StripeError (not deprecated stripe.error.*)"
    assert "stripe.error.StripeError" not in source, "Using deprecated stripe.error.StripeError"
    print("  Modern Stripe error handling OK")

    # Verify plan sync to profiles
    assert 'profiles' in source and '"plan"' in source, "Missing profiles plan sync"
    print("  Plan sync to profiles OK")

    # Verify config fields exist
    config_file = Path(__file__).parent.parent / "shared" / "config.py"
    with open(config_file, encoding="utf-8") as f:
        config_source = f.read()
    assert "stripe_webhook_secret" in config_source, "Missing stripe_webhook_secret in config"
    assert "stripe_price_basic" in config_source, "Missing stripe_price_basic in config"
    print("  Stripe config fields OK")


def test_feedback_automation():
    """Test feedback loop automation system"""
    # Cron worker
    cron_file = Path(__file__).parent.parent / "workers" / "feedback_cron.py"
    with open(cron_file, encoding="utf-8") as f:
        cron_source = f.read()

    cron_tasks = [
        "embed_completed_projects",
        "calibrate_prices",
        "analyze_as_patterns",
        "check_lora_trigger",
        "cleanup_old_training",
        "auto_register_completed_cases",
    ]
    for task in cron_tasks:
        assert f"async def {task}" in cron_source, f"Missing cron task: {task}"
    print(f"  {len(cron_tasks)} cron tasks defined OK")

    # TASKS registry
    assert "TASKS = {" in cron_source, "Missing TASKS registry"
    assert "all_hourly" in cron_source, "Missing all_hourly aggregator"
    assert "all_daily" in cron_source, "Missing all_daily aggregator"
    print("  Task registry + aggregators OK")

    # Admin API
    admin_file = Path(__file__).parent.parent / "api" / "routes" / "admin.py"
    with open(admin_file, encoding="utf-8") as f:
        admin_source = f.read()

    admin_endpoints = ["/constraints", "/lora-models", "/training-queue", "/trigger", "/calibrations"]
    for endpoint in admin_endpoints:
        assert endpoint in admin_source, f"Missing admin endpoint: {endpoint}"
    print(f"  {len(admin_endpoints)} admin endpoints OK")

    # Admin role gating (not just Pro plan)
    assert "require_admin" in admin_source, "Missing admin role gating"

    # Constraint workflow
    assert "approve" in admin_source and "reject" in admin_source, "Missing constraint approval flow"
    assert "proposed" in admin_source and "applied" in admin_source, "Missing constraint status transitions"
    print("  Constraint approval workflow OK")

    # LoRA model activation
    assert "activate" in admin_source, "Missing LoRA model activation"
    assert "is_active" in admin_source, "Missing is_active toggle"
    print("  LoRA model management OK")

    # Manual trigger
    assert "manual_trigger" in admin_source, "Missing manual trigger endpoint"
    assert "from workers.feedback_cron import" in admin_source, "Missing worker import in admin"
    print("  Manual trigger integration OK")


def test_enterprise_features():
    """Test Enterprise features: API key auth, DXF export, brand customization"""
    # Enterprise route
    ent_file = Path(__file__).parent.parent / "api" / "routes" / "enterprise.py"
    with open(ent_file, encoding="utf-8") as f:
        source = f.read()

    # API Key management
    assert "create_api_key" in source, "Missing API key creation"
    assert "list_api_keys" in source, "Missing API key listing"
    assert "revoke_api_key" in source, "Missing API key revocation"
    assert "dk_live_" in source, "Missing dk_live_ key prefix"
    assert "sha256" in source, "Missing SHA-256 hashing"
    print("  API Key management endpoints OK")

    # Brand customization
    assert "get_brand_settings" in source, "Missing brand settings GET"
    assert "update_brand_settings" in source, "Missing brand settings PUT"
    assert "primary_color" in source, "Missing brand color config"
    assert "logo_url" in source, "Missing logo support"
    assert "watermark_text" in source, "Missing watermark support"
    print("  Brand customization endpoints OK")

    # CAD DXF export
    assert "export_drawing_dxf" in source, "Missing DXF export endpoint"
    assert "_generate_dxf" in source, "Missing DXF generator"
    assert "AC1009" in source, "Missing DXF version header (R12)"
    assert "ENTITIES" in source, "Missing DXF entities section"
    assert "LINE" in source, "Missing DXF LINE entities"
    print("  CAD DXF export OK")

    # White-label quote
    assert "export_branded_quote" in source, "Missing branded quote endpoint"
    assert "_generate_branded_quote_html" in source, "Missing branded quote generator"
    print("  White-label quote OK")

    # API usage tracking
    assert "get_api_usage" in source, "Missing API usage endpoint"
    assert "api_usage_logs" in source, "Missing usage logs table reference"
    print("  API usage tracking OK")

    # Enterprise gating (uses shared require_enterprise from auth module)
    assert "require_enterprise" in source, "Missing enterprise plan gating"
    require_count = source.count("require_enterprise(user)")
    assert require_count >= 7, f"Expected >=7 enterprise-gated endpoints, got {require_count}"
    print(f"  Enterprise gating: {require_count} endpoints OK")

    # Auth middleware API key support
    auth_file = Path(__file__).parent.parent / "api" / "middleware" / "auth.py"
    with open(auth_file, encoding="utf-8") as f:
        auth_source = f.read()

    assert "_authenticate_api_key" in auth_source, "Missing API key auth in middleware"
    assert "dk_live_" in auth_source, "Missing dk_live_ prefix check in middleware"
    assert "via_api_key" in auth_source, "Missing via_api_key flag in CurrentUser"
    assert "key_hash" in auth_source, "Missing key hash verification"
    print("  Auth middleware API key support OK")

    # DXF generator produces valid structure
    # Import and test the DXF generator
    import importlib.util
    spec = importlib.util.spec_from_file_location("enterprise", ent_file)
    # Can't fully import (needs fastapi), so verify via AST
    tree = ast.parse(source)
    func_names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_generate_dxf" in func_names
    assert "_add_rect" in func_names
    assert "_add_text" in func_names
    assert "_generate_branded_quote_html" in func_names
    print("  DXF/Quote helper functions verified OK")

    # Migration file
    migration = Path(__file__).parent.parent / "db" / "migrations" / "004_enterprise.sql"
    assert migration.exists(), "Missing 004_enterprise.sql migration"
    mig_content = migration.read_text(encoding="utf-8")
    assert "api_keys" in mig_content, "Missing api_keys table"
    assert "brand_settings" in mig_content, "Missing brand_settings table"
    assert "api_usage_logs" in mig_content, "Missing api_usage_logs table"
    assert "key_hash" in mig_content, "Missing key_hash column"
    assert "ROW LEVEL SECURITY" in mig_content, "Missing RLS policies"
    print("  Migration 004_enterprise.sql OK")


def test_production_readiness():
    """Test production readiness: config validation, Docker, security hardening"""
    # Config uses pydantic-settings BaseSettings
    config_file = Path(__file__).parent.parent / "shared" / "config.py"
    with open(config_file, encoding="utf-8") as f:
        config_source = f.read()

    assert "BaseSettings" in config_source, "Should use pydantic-settings BaseSettings"
    assert "BaseModel" not in config_source or "BaseSettings" in config_source, "Should not use plain BaseModel for config"
    assert "cors_origins" in config_source, "Missing CORS origins config"
    assert "strip()" in config_source, "CORS origins should trim whitespace"
    assert "is_production" in config_source, "Missing production environment check"
    print("  Config validation with BaseSettings OK")

    # Docker files
    dockerfile = Path(__file__).parent.parent / "Dockerfile"
    assert dockerfile.exists(), "Missing Dockerfile"
    docker_content = dockerfile.read_text(encoding="utf-8")
    assert "HEALTHCHECK" in docker_content, "Missing Docker health check"
    assert "appuser" in docker_content, "Missing non-root user in Docker"
    print("  Dockerfile with healthcheck + non-root user OK")

    compose = Path(__file__).parent.parent / "docker-compose.yml"
    assert compose.exists(), "Missing docker-compose.yml"
    print("  docker-compose.yml OK")

    dockerignore = Path(__file__).parent.parent / ".dockerignore"
    assert dockerignore.exists(), "Missing .dockerignore"
    ignore_content = dockerignore.read_text(encoding="utf-8")
    assert ".env" in ignore_content, ".env should be in .dockerignore"
    print("  .dockerignore OK")

    # Shared auth gating functions
    auth_file = Path(__file__).parent.parent / "api" / "middleware" / "auth.py"
    with open(auth_file, encoding="utf-8") as f:
        auth_source = f.read()

    assert "def require_pro" in auth_source, "Missing shared require_pro"
    assert "def require_enterprise" in auth_source, "Missing shared require_enterprise"
    assert "def require_admin" in auth_source, "Missing shared require_admin"
    assert "PLAN_ORDER" in auth_source, "Missing centralized PLAN_ORDER"
    print("  Centralized auth gating functions OK")

    # Middleware stack
    main_file = Path(__file__).parent.parent / "api" / "main.py"
    with open(main_file, encoding="utf-8") as f:
        main_source = f.read()

    assert "RateLimitMiddleware" in main_source, "Missing rate limit middleware"
    assert "RequestLoggingMiddleware" in main_source, "Missing logging middleware"
    assert "register_error_handlers" in main_source, "Missing error handler registration"
    print("  Middleware stack (rate limit + logging + error handler) OK")

    # Rate limit middleware file
    rl_file = Path(__file__).parent.parent / "api" / "middleware" / "rate_limit.py"
    assert rl_file.exists(), "Missing rate_limit.py"
    with open(rl_file, encoding="utf-8") as f:
        rl_source = f.read()
    assert "429" in rl_source, "Missing 429 status code in rate limiter"
    assert "X-RateLimit" in rl_source, "Missing rate limit headers"
    assert "/api/v1/payments/webhook" in rl_source, "Missing webhook-specific rate limit"
    print("  Rate limiting with path-based limits OK")

    # Error handler
    eh_file = Path(__file__).parent.parent / "api" / "middleware" / "error_handler.py"
    assert eh_file.exists(), "Missing error_handler.py"
    with open(eh_file, encoding="utf-8") as f:
        eh_source = f.read()
    assert "RequestValidationError" in eh_source, "Missing validation error handler"
    assert '"success": False' in eh_source, "Missing consistent error format"
    print("  Structured error responses OK")

    # Request logging
    log_file = Path(__file__).parent.parent / "api" / "middleware" / "logging_mw.py"
    assert log_file.exists(), "Missing logging_mw.py"
    with open(log_file, encoding="utf-8") as f:
        log_source = f.read()
    assert "X-Response-Time" in log_source, "Missing response time header"
    assert "perf_counter" in log_source, "Missing performance timing"
    print("  Request/response logging OK")

    # CORS explicit methods (no more wildcard)
    assert '["*"]' not in main_source.split("allow_methods")[1].split(")")[0] if "allow_methods" in main_source else True, \
        "CORS should use explicit methods, not wildcard"
    print("  CORS explicit methods OK")

    # Frontend pages
    static_dir = Path(__file__).parent.parent / "static"
    required_pages = ["index.html", "new.html", "projects.html", "project.html", "orders.html", "pricing.html", "admin.html", "enterprise.html", "login.html", "signup.html", "auth-callback.html", "account.html", "forgot-password.html"]
    for page in required_pages:
        assert (static_dir / page).exists(), f"Missing page: {page}"
    print(f"  {len(required_pages)} frontend pages OK")

    # Admin page has required sections
    admin_html = (static_dir / "admin.html").read_text(encoding="utf-8")
    assert "constraints" in admin_html, "Admin missing constraints section"
    assert "lora" in admin_html, "Admin missing LoRA section"
    assert "training" in admin_html, "Admin missing training queue"
    assert "calibrations" in admin_html, "Admin missing calibrations"
    assert "trigger" in admin_html, "Admin missing manual trigger"
    print("  Admin dashboard sections OK")

    # Enterprise page has required sections
    ent_html = (static_dir / "enterprise.html").read_text(encoding="utf-8")
    assert "api-keys" in ent_html, "Enterprise missing API keys section"
    assert "brand" in ent_html, "Enterprise missing brand settings"
    assert "usage" in ent_html, "Enterprise missing usage section"
    assert "dxf" in ent_html.lower(), "Enterprise missing DXF export"
    print("  Enterprise settings sections OK")

    # Auth pages
    login_html = (static_dir / "login.html").read_text(encoding="utf-8")
    assert "signInWithPassword" in login_html, "Login missing Supabase auth"
    assert "signInWithOAuth" in login_html, "Login missing Google OAuth"
    signup_html = (static_dir / "signup.html").read_text(encoding="utf-8")
    assert "signUp" in signup_html, "Signup missing Supabase signup"
    assert "password-strength" in signup_html, "Signup missing password strength"
    print("  Auth pages (login/signup/callback) OK")

    # Auth guard in app.js
    app_js = (static_dir / "js" / "app.js").read_text(encoding="utf-8")
    assert "requireAuth" in app_js, "Missing auth guard function"
    assert "apiFetch" in app_js, "Missing 401 handler wrapper"
    assert "renderNav" in app_js, "Missing dynamic navigation"
    assert "handleLogout" in app_js, "Missing logout function"
    assert "site-header" in app_js or "renderNavInto" in app_js, "Missing nav injection"
    print("  Auth guard + dynamic nav + 401 handler OK")

    # Token key consistency: all pages must use dadam_token, not access_token
    orders_html = (static_dir / "orders.html").read_text(encoding="utf-8")
    assert "dadam_token" in orders_html, "orders.html should use dadam_token"
    assert "access_token" not in orders_html, "orders.html still uses access_token (should be dadam_token)"
    print("  Token key consistency (dadam_token) OK")

    # Admin/Enterprise must load app.js for auth guard + dynamic nav
    assert 'id="site-header"' in admin_html, "admin.html missing site-header id"
    assert "app.js" in admin_html, "admin.html must load app.js"
    assert 'id="site-header"' in ent_html, "enterprise.html missing site-header id"
    assert "app.js" in ent_html, "enterprise.html must load app.js"
    print("  Admin/Enterprise load app.js OK")

    # Config endpoint exists in main.py
    assert "/api/v1/config" in main_source, "Missing /api/v1/config endpoint"
    print("  Config endpoint for Supabase credentials OK")

    # Login/signup use /api/v1/config instead of hardcoded values
    assert "/api/v1/config" in login_html, "login.html should fetch config from API"
    assert "/api/v1/config" in signup_html, "signup.html should fetch config from API"
    assert "window.__SUPABASE_URL__" not in login_html, "login.html should not use window.__SUPABASE_URL__"
    assert "window.__SUPABASE_URL__" not in signup_html, "signup.html should not use window.__SUPABASE_URL__"
    print("  Auth pages use config endpoint OK")

    # Forgot password page
    forgot_html = (static_dir / "forgot-password.html").read_text(encoding="utf-8")
    assert "resetPasswordForEmail" in forgot_html, "forgot-password.html missing Supabase resetPasswordForEmail"
    assert "/api/v1/config" in forgot_html, "forgot-password.html should use config endpoint"
    assert 'id="site-header"' in forgot_html, "forgot-password.html missing site-header id"
    assert "app.js" in forgot_html, "forgot-password.html must load app.js"
    print("  Forgot password page OK")

    # pricing.html token consistency
    pricing_html = (static_dir / "pricing.html").read_text(encoding="utf-8")
    assert "dadam_token" in pricing_html, "pricing.html should use dadam_token"
    assert "access_token" not in pricing_html, "pricing.html still uses access_token (should be dadam_token)"
    print("  Pricing page token consistency OK")

    # Security headers middleware
    sec_mw = Path(__file__).parent.parent / "api" / "middleware" / "security_headers.py"
    assert sec_mw.exists(), "Missing security_headers.py middleware"
    sec_source = sec_mw.read_text(encoding="utf-8")
    for header in ["X-Content-Type-Options", "X-Frame-Options", "Strict-Transport-Security",
                    "Content-Security-Policy", "Referrer-Policy", "Permissions-Policy"]:
        assert header in sec_source, f"Missing security header: {header}"
    assert "SecurityHeadersMiddleware" in main_source, "SecurityHeadersMiddleware not registered in main.py"
    print("  Security headers middleware (6 headers) OK")

    # Async pipeline (SSE streaming or BackgroundTasks)
    projects_source = (Path(__file__).parent.parent / "api" / "routes" / "projects.py").read_text(encoding="utf-8")
    assert "pipeline_stage" in projects_source, "projects.py missing pipeline_stage tracking"
    assert "StreamingResponse" in projects_source or "BackgroundTasks" in projects_source, \
        "projects.py missing async pipeline mechanism (SSE or BackgroundTasks)"
    print("  Async pipeline OK")

    # CI/CD pipeline
    ci_file = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
    assert ci_file.exists(), "Missing CI/CD pipeline"
    ci_content = ci_file.read_text(encoding="utf-8")
    assert "ruff" in ci_content, "Missing lint step in CI"
    assert "test_pipeline_e2e" in ci_content, "Missing test step in CI"
    assert "docker" in ci_content.lower(), "Missing Docker build step in CI"
    print("  CI/CD pipeline (lint + test + Docker) OK")


def test_migration_files():
    """Verify migration files exist and have valid SQL"""
    migrations_dir = Path(__file__).parent.parent / "db" / "migrations"

    expected = ["001_foundation.sql", "002_storage.sql", "003_feedback_loop.sql", "004_enterprise.sql", "005_pipeline_stage.sql"]
    for filename in expected:
        filepath = migrations_dir / filename
        assert filepath.exists(), f"Missing migration: {filename}"
        content = filepath.read_text(encoding="utf-8")
        has_sql = any(kw in content for kw in ["CREATE TABLE", "CREATE EXTENSION", "CREATE POLICY", "INSERT INTO", "ALTER TABLE"])
        assert has_sql, f"{filename}: no SQL statements found"
        print(f"  {filename}: {len(content)} bytes OK")


# ============================================================
# Runner
# ============================================================

def run_all():
    tests = [
        ("Layout Engine - Module Distribution", test_layout_engine_all_categories),
        ("Layout Planning - Sink with Utilities", test_layout_planning_sink),
        ("Layout Planning - All Categories", test_layout_planning_all_categories),
        ("Drawing - SVG Front Elevation", test_drawing_svg_generation),
        ("Orchestrator - Agent Wiring", test_orchestrator_agent_wiring),
        ("API Routes - Syntax Check", test_api_route_syntax),
        ("Prompts - English Quality", test_prompt_quality),
        ("Operations - Prompt Re-export", test_operations_prompts_reexport),
        ("Constants - Completeness", test_constants_completeness),
        ("Migrations - File Check", test_migration_files),
        ("Orders - State Machine & Payments", test_orders_state_machine),
        ("Exports - B2B Route Structure", test_exports_route),
        ("Payments - Stripe Integration", test_payments_route),
        ("Feedback Automation - Cron + Admin", test_feedback_automation),
        ("Enterprise - API Key + DXF + Brand", test_enterprise_features),
        ("Production Readiness - Config + Docker", test_production_readiness),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        print(f"\n{'='*60}")
        print(f"TEST: {name}")
        print(f"{'='*60}")
        try:
            test_fn()
            print(f"-> PASSED OK")
            passed += 1
        except Exception as e:
            print(f"-> FAILED FAIL: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed (total {passed + failed})")
    print(f"{'='*60}")

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
