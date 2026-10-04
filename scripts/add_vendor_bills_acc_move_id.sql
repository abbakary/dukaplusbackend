-- Run once on production if bills fail with: column "acc_move_id" of relation "vendor_bills" does not exist
ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS acc_move_id VARCHAR(36);
ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS purchase_order_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_vendor_bills_acc_move_id ON vendor_bills (acc_move_id);
