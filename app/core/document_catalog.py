"""Built-in document template catalog (delivery note, order note, invoice)."""

BUILT_IN_DOCUMENT_CATALOG: list[dict] = [
    {"id": "dn-classic-teal", "document_type": "delivery_note", "name": "Classic Teal Header", "name_sw": "Kichwa cha Teal", "layout": "classic", "popular": True},
    {"id": "dn-minimal-blue", "document_type": "delivery_note", "name": "Minimal Blue Grid", "name_sw": "Grid ya Bluu Rahisi", "layout": "minimal"},
    {"id": "dn-corporate-wave", "document_type": "delivery_note", "name": "Corporate Wave", "name_sw": "Wimbi la Kampuni", "layout": "corporate"},
    {"id": "dn-modern-beige", "document_type": "delivery_note", "name": "Modern Beige", "name_sw": "Beige ya Kisasa", "layout": "modern"},
    {"id": "on-warm-classic", "document_type": "order_note", "name": "Warm Classic", "name_sw": "Classic ya Joto", "layout": "classic", "popular": True},
    {"id": "on-creative-curve", "document_type": "order_note", "name": "Creative Curve", "name_sw": "Curve ya Ubunifu", "layout": "modern"},
    {"id": "on-navy-corporate", "document_type": "order_note", "name": "Navy Corporate", "name_sw": "Navy ya Kampuni", "layout": "corporate"},
    {"id": "on-minimal-grid", "document_type": "order_note", "name": "Minimal Grid", "name_sw": "Grid Rahisi", "layout": "minimal"},
    {"id": "inv-minimal-round", "document_type": "invoice", "name": "Minimal Rounded", "name_sw": "Rounded Rahisi", "layout": "minimal", "popular": True},
    {"id": "inv-artistic-brush", "document_type": "invoice", "name": "Artistic Brush", "name_sw": "Brush ya Sanaa", "layout": "modern"},
    {"id": "inv-professional-grid", "document_type": "invoice", "name": "Professional Grid", "name_sw": "Grid ya Kitaalamu", "layout": "classic"},
    {"id": "inv-tech-corporate", "document_type": "invoice", "name": "Tech Corporate", "name_sw": "Tech ya Kampuni", "layout": "corporate"},
]

DEFAULT_ACTIVE_TEMPLATES = {
    "delivery_note": "dn-classic-teal",
    "order_note": "on-warm-classic",
    "invoice": "inv-minimal-round",
}

DOCUMENT_TYPE_LABELS = {
    "delivery_note": {"en": "Delivery Note", "sw": "Noti ya Uwasilishaji"},
    "order_note": {"en": "Order Note", "sw": "Noti ya Agizo"},
    "invoice": {"en": "Invoice", "sw": "Ankara"},
}
