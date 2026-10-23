CREATE TABLE chart_annotation (
    annotation_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    price REAL NOT NULL CHECK(price > 0),
    label TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL CHECK(color IN ('blue','amber','red','green','violet')),
    price_basis TEXT NOT NULL CHECK(price_basis IN ('none','forward_event_affine')),
    anchor_date TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    CHECK((price_basis='none' AND anchor_date IS NULL) OR
          (price_basis='forward_event_affine' AND anchor_date IS NOT NULL))
);
CREATE INDEX chart_annotation_instrument ON chart_annotation(instrument_id, created_at_ms, annotation_id);
