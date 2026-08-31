"""Backend Business-Type Engine — extends legacy profiles with new verticals."""

from __future__ import annotations

from typing import Any

from app.core.business_profiles import BUSINESS_PROFILES as _LEGACY_PROFILES

_NEW_TYPES: dict[str, dict[str, Any]] = {
    "supermarket": {
        "id": "supermarket", "label_sw": "Supermarket", "label_en": "Supermarket", "icon": "🏬",
        "inventory_title_sw": "Orodha ya Supermarket", "inventory_title_en": "Supermarket Catalog",
        "pos_title_sw": "POS ya Malipo", "pos_title_en": "Checkout POS",
        "product_fields": ["barcode", "brand", "weight", "expiry_date"],
        "default_units": ["pcs", "kg", "liters", "cartons"],
        "default_categories": ["Food Basket", "Drinks", "Fresh Foods", "Chilled & Frozen", "Household", "Baby Products"],
        "compliance": ["TRA EFD", "TFDA"],
        "nav_extra": [{"id": "barcodes", "label_sw": "Barcode", "label_en": "Barcodes"}],
        "features": {"batch_tracking": True, "expiry_alerts": True, "barcode_scan": True, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "electronics": {
        "id": "electronics", "label_sw": "Vifaa vya Umeme / Simu", "label_en": "Electronics & Phones", "icon": "📱",
        "inventory_title_sw": "Orodha ya Elektroniki", "inventory_title_en": "Electronics Catalog",
        "pos_title_sw": "POS ya Elektroniki", "pos_title_en": "Electronics POS",
        "product_fields": ["imei", "serial_number", "model", "warranty_months"],
        "default_units": ["pcs", "sets"],
        "default_categories": ["Mobile Phones", "Chargers", "Phone Accessories", "Audio", "Computers"],
        "compliance": ["TRA EFD"],
        "nav_extra": [
            {"id": "serial-numbers", "label_sw": "Serial / IMEI", "label_en": "Serial Numbers"},
            {"id": "warranty", "label_sw": "Dhamana", "label_en": "Warranty"},
        ],
        "features": {"batch_tracking": False, "expiry_alerts": False, "barcode_scan": True, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "auto_parts": {
        "id": "auto_parts", "label_sw": "Vipuri vya Magari", "label_en": "Auto Spare Parts", "icon": "🚗",
        "inventory_title_sw": "Orodha ya Vipuri", "inventory_title_en": "Spare Parts Catalog",
        "pos_title_sw": "Uza Vipuri", "pos_title_en": "Parts Sales",
        "product_fields": ["vehicle_make", "vehicle_model", "oem_number", "part_number"],
        "default_units": ["pcs", "sets"],
        "default_categories": ["Engine Parts", "Brake System", "Suspension", "Filters & Fluids", "Tyres & Rims"],
        "compliance": ["TRA EFD"],
        "nav_extra": [
            {"id": "vehicle-compat", "label_sw": "Ulinganifu wa Gari", "label_en": "Vehicle Compatibility"},
            {"id": "workshop", "label_sw": "Kibanda cha Kazi", "label_en": "Workshop"},
        ],
        "features": {"batch_tracking": False, "expiry_alerts": False, "barcode_scan": True, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "fashion": {
        "id": "fashion", "label_sw": "Nguo na Mitindo", "label_en": "Clothing & Fashion", "icon": "👗",
        "inventory_title_sw": "Orodha ya Mitindo", "inventory_title_en": "Fashion Catalog",
        "pos_title_sw": "POS ya Nguo", "pos_title_en": "Fashion POS",
        "product_fields": ["size", "color", "material", "brand"],
        "default_units": ["pcs", "pairs"],
        "default_categories": ["Men", "Women", "Kids", "Shoes", "Accessories"],
        "compliance": ["TRA EFD"],
        "nav_extra": [{"id": "variants", "label_sw": "Variants", "label_en": "Size & Color Variants"}],
        "features": {"batch_tracking": False, "expiry_alerts": False, "barcode_scan": True, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "agrovet": {
        "id": "agrovet", "label_sw": "Agrovet", "label_en": "Agrovet", "icon": "🌾",
        "inventory_title_sw": "Akiba ya Agrovet", "inventory_title_en": "Agrovet Inventory",
        "pos_title_sw": "POS ya Agrovet", "pos_title_en": "Agrovet POS",
        "product_fields": ["crop", "animal_type", "batch_number", "expiry_date"],
        "default_units": ["bags", "kg", "liters", "bottles"],
        "default_categories": ["Seeds", "Fertilizers", "Veterinary Medicines", "Animal Feeds"],
        "compliance": ["TFDA", "TRA EFD"],
        "nav_extra": [{"id": "batch-expiry", "label_sw": "Batch & Mwisho", "label_en": "Batch & Expiry"}],
        "features": {"batch_tracking": True, "expiry_alerts": True, "barcode_scan": False, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "beauty": {
        "id": "beauty", "label_sw": "Urembo na Cosmetics", "label_en": "Beauty & Cosmetics", "icon": "💄",
        "inventory_title_sw": "Bidhaa za Urembo", "inventory_title_en": "Beauty Products",
        "pos_title_sw": "POS ya Urembo", "pos_title_en": "Beauty POS",
        "product_fields": ["brand", "batch_number", "expiry_date"],
        "default_units": ["pcs", "bottles", "packs"],
        "default_categories": ["Skincare", "Haircare", "Makeup", "Fragrance", "Hygiene"],
        "compliance": ["TRA EFD", "TFDA"],
        "nav_extra": [],
        "features": {"batch_tracking": False, "expiry_alerts": True, "barcode_scan": True, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "salon": {
        "id": "salon", "label_sw": "Saluni / Kinyozi", "label_en": "Salon / Barbershop", "icon": "💇",
        "inventory_title_sw": "Bidhaa za Saluni", "inventory_title_en": "Salon Products",
        "pos_title_sw": "Toa Huduma", "pos_title_en": "Service Billing",
        "product_fields": ["duration_minutes", "commission_rate"],
        "default_units": ["service", "session", "hour"],
        "default_categories": ["Hair", "Nails", "Beauty"],
        "compliance": ["TRA EFD", "BRELA"],
        "nav_extra": [
            {"id": "appointments", "label_sw": "Miadi", "label_en": "Appointments"},
            {"id": "commissions", "label_sw": "Kamisheni", "label_en": "Commissions"},
        ],
        "features": {"batch_tracking": False, "expiry_alerts": False, "barcode_scan": False, "fractional_units": False, "table_management": False, "appointments": True},
    },
    "stationery": {
        "id": "stationery", "label_sw": "Vifaa vya Ofisi", "label_en": "Stationery / Bookshop", "icon": "📚",
        "inventory_title_sw": "Orodha ya Ofisi", "inventory_title_en": "Stationery Catalog",
        "pos_title_sw": "POS ya Ofisi", "pos_title_en": "Stationery POS",
        "product_fields": ["isbn", "author", "barcode"],
        "default_units": ["pcs", "packs", "reams"],
        "default_categories": ["Writing Instruments", "Notebooks", "School Supplies", "Books"],
        "compliance": ["TRA EFD"],
        "nav_extra": [],
        "features": {"batch_tracking": False, "expiry_alerts": False, "barcode_scan": True, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "furniture": {
        "id": "furniture", "label_sw": "Samani", "label_en": "Furniture", "icon": "🪑",
        "inventory_title_sw": "Orodha ya Samani", "inventory_title_en": "Furniture Catalog",
        "pos_title_sw": "POS ya Samani", "pos_title_en": "Furniture POS",
        "product_fields": ["length_cm", "width_cm", "material", "color"],
        "default_units": ["pcs", "sets"],
        "default_categories": ["Living Room", "Bedroom", "Office Furniture", "Mattresses"],
        "compliance": ["TRA EFD"],
        "nav_extra": [],
        "features": {"batch_tracking": False, "expiry_alerts": False, "barcode_scan": False, "fractional_units": False, "table_management": False, "appointments": False},
    },
    "mixed": {
        "id": "mixed", "label_sw": "Biashara Mchanganyiko", "label_en": "Mixed Business", "icon": "🏢",
        "inventory_title_sw": "Bidhaa na Huduma", "inventory_title_en": "Products & Services",
        "pos_title_sw": "Mauzo na Malipo", "pos_title_en": "Sales & Billing",
        "product_fields": ["barcode", "is_service"],
        "default_units": ["pcs", "service", "kg"],
        "default_categories": ["Products", "Services", "Other"],
        "compliance": ["TRA EFD"],
        "nav_extra": [{"id": "appointments", "label_sw": "Miadi", "label_en": "Appointments"}],
        "features": {"batch_tracking": False, "expiry_alerts": False, "barcode_scan": True, "fractional_units": False, "table_management": False, "appointments": True},
    },
}

BUSINESS_PROFILES: dict[str, dict[str, Any]] = {**_LEGACY_PROFILES, **_NEW_TYPES}


def get_business_profile(business_type: str) -> dict[str, Any]:
    return BUSINESS_PROFILES.get(business_type, BUSINESS_PROFILES["retail"])


def flatten_default_categories(business_type: str) -> list[str]:
    return list(get_business_profile(business_type).get("default_categories", ["General"]))


def product_metadata_for_type(business_type: str) -> dict[str, Any]:
    profile = get_business_profile(business_type)
    meta: dict[str, Any] = {"business_type": business_type}
    for field in profile.get("product_fields", []):
        meta[field] = None
    return meta
