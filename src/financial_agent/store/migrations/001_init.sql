CREATE TABLE contacts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE nicknames (
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  nickname TEXT NOT NULL,
  PRIMARY KEY (contact_id, nickname)
);
CREATE TABLE destinations (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK (type IN ('email_mp', 'cbu_cvu', 'alias')),
  value TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  created_at TEXT NOT NULL,
  UNIQUE (contact_id, type, value)
);
CREATE UNIQUE INDEX destinations_one_primary ON destinations(contact_id) WHERE is_primary = 1;
CREATE TABLE notes (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE pending_actions (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('delete_contact', 'delete_destination', 'update_destination_value', 'set_primary')),
  payload TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pendiente' CHECK (status IN ('pendiente', 'confirmada', 'cancelada', 'vencida')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  resolved_at TEXT
);
