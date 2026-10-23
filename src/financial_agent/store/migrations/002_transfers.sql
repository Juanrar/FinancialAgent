ALTER TABLE destinations ADD COLUMN verified_holder TEXT;
ALTER TABLE destinations ADD COLUMN verified_account TEXT;
ALTER TABLE destinations ADD COLUMN verified_bank TEXT;
ALTER TABLE destinations ADD COLUMN verified_at TEXT;

CREATE TABLE routines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  destination_id INTEGER REFERENCES destinations(id) ON DELETE SET NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 100),
  day INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
  last_sent_month TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE pending_actions_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('delete_contact', 'delete_destination', 'update_destination_value', 'set_primary', 'transfer', 'update_verification')),
  payload TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pendiente' CHECK (status IN ('pendiente', 'confirmada', 'cancelada', 'vencida', 'en_curso', 'enviada', 'no_salio', 'sin_confirmar', 'hecha_a_mano')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  resolved_at TEXT,
  routine_id INTEGER REFERENCES routines(id) ON DELETE SET NULL,
  detail TEXT
);
INSERT INTO pending_actions_new (id, kind, payload, description, status, created_at, expires_at, resolved_at)
  SELECT id, kind, payload, description, status, created_at, expires_at, resolved_at FROM pending_actions;
DROP TABLE pending_actions;
ALTER TABLE pending_actions_new RENAME TO pending_actions;

CREATE TABLE bot_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
