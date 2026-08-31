"""Business-type workplace & UI configuration for multi-vertical SaaS."""

from typing import Any

BUSINESS_PROFILES: dict[str, dict[str, Any]] = {
    "pharmacy": {
        "id": "pharmacy",
        "label_sw": "Duka la Dawa",
        "label_en": "Pharmacy",
        "icon": "💊",
        "inventory_title_sw": "Dawa na Bidhaa za Afya",
        "inventory_title_en": "Medicines & Health Products",
        "pos_title_sw": "Uza Dawa",
        "pos_title_en": "Dispense & Sell",
        "product_fields": ["batch_number", "expiry_date", "requires_prescription"],
        "default_units": ["tablets", "capsules", "bottles", "sachets", "boxes"],
        "default_categories": ["Pain Relief", "Antibiotics", "Vitamins", "First Aid", "OTC"],
        "compliance": ["TMDA", "Pharmacy Council"],
        "nav_extra": [{"id": "prescriptions", "label_sw": "Dawa za Rx", "label_en": "Prescriptions"}],
        "features": {
            "batch_tracking": True,
            "expiry_alerts": True,
            "barcode_scan": True,
            "fractional_units": False,
            "table_management": False,
            "appointments": False,
        },
    },
    "retail": {
        "id": "retail",
        "label_sw": "Rejareja",
        "label_en": "Retail Store",
        "icon": "🛒",
        "inventory_title_sw": "Bidhaa za Rejareja",
        "inventory_title_en": "Retail Product Catalog",
        "pos_title_sw": "Uza Bidhaa",
        "pos_title_en": "Point of Sale",
        "product_fields": ["barcode"],
        "default_units": ["pcs", "pairs", "packs", "cartons"],
        "default_categories": ["Groceries", "Beverages", "Household", "Personal Care", "Electronics"],
        "compliance": ["TRA EFD", "BRELA"],
        "nav_extra": [{"id": "barcodes", "label_sw": "Barcode", "label_en": "Barcodes"}],
        "features": {
            "batch_tracking": False,
            "expiry_alerts": True,
            "barcode_scan": True,
            "fractional_units": False,
            "table_management": False,
            "appointments": False,
        },
    },
    "hardware": {
        "id": "hardware",
        "label_sw": "Vifaa vya Ujenzi",
        "label_en": "Hardware & Building",
        "icon": "🔧",
        "inventory_title_sw": "Vifaa na Nyenzo",
        "inventory_title_en": "Hardware & Materials",
        "pos_title_sw": "Uza Vifaa",
        "pos_title_en": "Hardware POS",
        "product_fields": ["fractional_unit", "min_order_qty"],
        "default_units": ["pcs", "meters", "kg", "bags", "liters", "sheets"],
        "default_categories": ["Cement", "Steel", "Plumbing", "Electrical", "Tools", "Paint"],
        "compliance": ["TRA EFD"],
        "nav_extra": [{"id": "fractional", "label_sw": "Vipimo", "label_en": "Fractional Units"}],
        "features": {
            "batch_tracking": False,
            "expiry_alerts": False,
            "barcode_scan": True,
            "fractional_units": True,
            "table_management": False,
            "appointments": False,
        },
    },
    "restaurant": {
        "id": "restaurant",
        "label_sw": "Mgahawa",
        "label_en": "Restaurant & Cafe",
        "icon": "🍽️",
        "inventory_title_sw": "Viungo na Stock ya Jikoni",
        "inventory_title_en": "Kitchen Inventory & Ingredients",
        "pos_title_sw": "Agiza / POS",
        "pos_title_en": "Orders & POS",
        "product_fields": ["recipe_items", "prep_time_minutes"],
        "default_units": ["plates", "portions", "kg", "liters", "pcs"],
        "default_categories": ["Appetizers", "Main Course", "Drinks", "Desserts", "Ingredients"],
        "compliance": ["TRA EFD", "Food Safety"],
        "nav_extra": [
            {"id": "reception", "label_sw": "Mapokezi", "label_en": "Reception"},
            {"id": "kitchen", "label_sw": "Jikoni (KDS)", "label_en": "Kitchen KDS"},
            {"id": "waiter", "label_sw": "Waudum", "label_en": "Waiter"},
            {"id": "restaurant-live", "label_sw": "Meneja / Live", "label_en": "Restaurant Live"},
        ],
        "features": {
            "batch_tracking": True,
            "expiry_alerts": True,
            "barcode_scan": False,
            "fractional_units": True,
            "table_management": True,
            "appointments": False,
        },
    },
    "service": {
        "id": "service",
        "label_sw": "Huduma",
        "label_en": "Service Business",
        "icon": "💼",
        "inventory_title_sw": "Vifaa vya Huduma",
        "inventory_title_en": "Service Supplies & Consumables",
        "pos_title_sw": "Toa Huduma",
        "pos_title_en": "Service Billing",
        "product_fields": ["duration_minutes", "commission_rate"],
        "default_units": ["session", "hour", "service", "pcs"],
        "default_categories": ["Hair", "Beauty", "Repairs", "Consultation", "Supplies"],
        "compliance": ["TRA EFD", "BRELA"],
        "nav_extra": [{"id": "appointments", "label_sw": "Miadi", "label_en": "Appointments"}],
        "features": {
            "batch_tracking": False,
            "expiry_alerts": False,
            "barcode_scan": False,
            "fractional_units": False,
            "table_management": False,
            "appointments": True,
        },
    },
}


def get_business_profile(business_type: str) -> dict[str, Any]:
    return BUSINESS_PROFILES.get(business_type, BUSINESS_PROFILES["retail"])


def product_metadata_for_type(business_type: str) -> dict[str, Any]:
    profile = get_business_profile(business_type)
    meta: dict[str, Any] = {"business_type": business_type}
    for field in profile.get("product_fields", []):
        meta[field] = None
    return meta
