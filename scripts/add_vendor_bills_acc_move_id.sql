-- Run once on production if API fails with missing acc_move_id on vendor_bills or sales
ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS acc_move_id VARCHAR(36);
ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS purchase_order_id VARCHAR(36);
ALTER TABLE sales ADD COLUMN IF NOT EXISTS acc_move_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_vendor_bills_acc_move_id ON vendor_bills (acc_move_id);
CREATE INDEX IF NOT EXISTS ix_sales_acc_move_id ON sales (acc_move_id);
