ALTER TABLE item_remote ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0;

ALTER TABLE item_remote ADD COLUMN space_id INTEGER;

UPDATE item_remote SET space_id = (SELECT space_id FROM items WHERE items.id = item_remote.item_id);

ALTER TABLE attachments ADD COLUMN drive_file_id TEXT;
